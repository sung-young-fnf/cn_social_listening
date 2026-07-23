"""로컬 릴레이 프록시 — 브라우저와 Oxylabs 사이의 '중계소'.

목적:
    브라우저는 이 릴레이(127.0.0.1:PORT)만 프록시로 바라본다 → 브라우저 재실행 없음
    → 콜드 로드 없음 → QR 재로그인 안 뜸.
    실제 Oxylabs 접속은 릴레이가 담당하고, IP를 바꾸고 싶으면 릴레이가 쥔
    upstream sessid만 갈아끼운다(rotate). 브라우저 입장에선 Oxylabs 자연
    로테이션과 구분 안 되므로 로그인 세션(web_session 쿠키)은 그대로 유지된다.

동작:
    - 브라우저 → 릴레이 CONNECT tunnel → Oxylabs(현재 sessid) → 목적지(xhs 등)
    - rotate_sessid() 호출 시 upstream username의 sessid를 새 값으로 교체.
      이후 새로 열리는 tunnel부터 새 IP로 나감.

주의:
    - HTTPS(CONNECT) 터널링만 지원 — xhs 크롤은 전부 https라 충분.
    - 릴레이는 asyncio 서버로 백그라운드 task로 띄운다(start()).
"""
import asyncio
import base64
import os
import secrets


class OxylabsRelay:
    """브라우저 ↔ Oxylabs 중계. sessid 핫스왑으로 IP 즉시 교체."""

    def __init__(self, upstream_host, upstream_port, username_base, password,
                 sessid=None, sesstime="30", host="127.0.0.1", port=0):
        self.upstream_host = upstream_host
        self.upstream_port = int(upstream_port)
        self.username_base = username_base   # 예: customer-xxx-cc-kr (sessid/sesstime 제외)
        self.password = password
        self.sesstime = str(sesstime)
        self.sessid = sessid or self._new_sessid()
        self.host = host
        self.port = port                     # 0이면 OS가 빈 포트 자동 할당
        self._server = None
        self._rotations = 0
        self._active = set()                 # 열린 (cwriter, uwriter) 터널 — rotate 시 끊기

    @staticmethod
    def _new_sessid():
        return f"auto_{secrets.token_hex(4)}"

    def _upstream_username(self):
        """현재 sessid로 Oxylabs username 조립."""
        return f"{self.username_base}-sessid-{self.sessid}-sesstime-{self.sesstime}"

    def _proxy_auth_header(self):
        raw = f"{self._upstream_username()}:{self.password}".encode()
        return b"Proxy-Authorization: Basic " + base64.b64encode(raw) + b"\r\n"

    def rotate_sessid(self, new_sessid=None):
        """upstream sessid를 새 값으로 교체 + 현재 열린 터널 전부 끊기.
        → keep-alive 옛 터널(옛 IP) 재사용을 막아 다음 요청부터 확실히 새 IP.
        반환: 새 sessid."""
        self.sessid = new_sessid or self._new_sessid()
        self._rotations += 1
        killed = 0
        for cw, uw in list(self._active):
            for w in (cw, uw):
                try:
                    w.close()
                except Exception:
                    pass
            killed += 1
        self._active.clear()
        print(f"[relay] ★ sessid 교체 #{self._rotations} → {self.sessid} "
              f"(열린 터널 {killed}개 끊음 → 다음 요청부터 새 IP)")
        return self.sessid

    @property
    def address(self):
        """브라우저가 프록시로 지정할 주소 (http://127.0.0.1:PORT)."""
        return f"http://{self.host}:{self.port}"

    async def start(self):
        """릴레이 서버 기동. self.port가 0이면 실제 할당된 포트로 갱신."""
        self._server = await asyncio.start_server(
            self._handle_client, self.host, self.port)
        # 실제 바인드된 포트 반영
        sock = self._server.sockets[0]
        self.port = sock.getsockname()[1]
        print(f"[relay] 기동 → {self.address} → Oxylabs {self.upstream_host}:{self.upstream_port} "
              f"(sessid={self.sessid})")
        return self.port

    async def close(self):
        if self._server:
            self._server.close()
            try:
                await self._server.wait_closed()
            except Exception:
                pass

    async def _handle_client(self, creader, cwriter):
        """브라우저 연결 1건 처리. CONNECT 메서드만 지원(HTTPS 터널)."""
        try:
            request_line = await creader.readline()
            if not request_line:
                cwriter.close()
                return
            parts = request_line.split()
            if len(parts) < 2 or parts[0].upper() != b"CONNECT":
                # CONNECT 외(평문 http)는 미지원 — 브라우저는 https만 쓰므로 무시
                cwriter.write(b"HTTP/1.1 405 Method Not Allowed\r\n\r\n")
                await cwriter.drain()
                cwriter.close()
                return
            target = parts[1].decode()   # host:port
            # 나머지 요청 헤더 소진 (빈 줄까지)
            while True:
                line = await creader.readline()
                if line in (b"\r\n", b"\n", b""):
                    break
            await self._tunnel(target, creader, cwriter)
        except Exception:
            try:
                cwriter.close()
            except Exception:
                pass

    async def _tunnel(self, target, creader, cwriter):
        """Oxylabs에 CONNECT로 target 터널 요청 → 양방향 파이프."""
        try:
            ureader, uwriter = await asyncio.open_connection(
                self.upstream_host, self.upstream_port)
        except Exception as e:
            cwriter.write(b"HTTP/1.1 502 Bad Gateway\r\n\r\n")
            await cwriter.drain()
            cwriter.close()
            print(f"[relay] upstream 연결 실패: {e}")
            return

        # Oxylabs에 CONNECT + 현재 sessid의 Proxy-Authorization 전송
        req = (f"CONNECT {target} HTTP/1.1\r\n"
               f"Host: {target}\r\n").encode()
        req += self._proxy_auth_header()
        req += b"\r\n"
        uwriter.write(req)
        await uwriter.drain()

        # Oxylabs 응답 헤더 읽기 (200이면 터널 확립)
        status_line = await ureader.readline()
        ok = b"200" in status_line
        # 남은 응답 헤더 소진
        while True:
            line = await ureader.readline()
            if line in (b"\r\n", b"\n", b""):
                break

        if not ok:
            cwriter.write(b"HTTP/1.1 502 Bad Gateway\r\n\r\n")
            await cwriter.drain()
            cwriter.close()
            uwriter.close()
            return

        # 브라우저에 200 반환 → 이후 raw 양방향 중계
        cwriter.write(b"HTTP/1.1 200 Connection Established\r\n\r\n")
        await cwriter.drain()

        pair = (cwriter, uwriter)
        self._active.add(pair)   # rotate 시 끊을 수 있게 등록
        try:
            await asyncio.gather(
                self._pipe(creader, uwriter),
                self._pipe(ureader, cwriter),
                return_exceptions=True,
            )
        finally:
            self._active.discard(pair)

    @staticmethod
    async def _pipe(reader, writer):
        try:
            while True:
                data = await reader.read(65536)
                if not data:
                    break
                writer.write(data)
                await writer.drain()
        except Exception:
            pass
        finally:
            try:
                writer.close()
            except Exception:
                pass


def build_relay_from_env(sessid=None):
    """.env(OXYLABS_*) 로 릴레이 인스턴스 생성 (아직 start 안 함)."""
    user = os.getenv("OXYLABS_USERNAME")
    pwd = os.getenv("OXYLABS_PASSWORD")
    if not user or not pwd:
        raise RuntimeError("OXYLABS_USERNAME / OXYLABS_PASSWORD 필요")
    country = os.getenv("OXYLABS_COUNTRY", "kr")
    username_base = user if "-cc-" in user else f"{user}-cc-{country}"
    return OxylabsRelay(
        upstream_host=os.getenv("OXYLABS_HOST", "pr.oxylabs.io"),
        upstream_port=os.getenv("OXYLABS_PORT", "7777"),
        username_base=username_base,
        password=pwd,
        sessid=sessid,
        sesstime=os.getenv("OXYLABS_SESSTIME", "30"),
    )
