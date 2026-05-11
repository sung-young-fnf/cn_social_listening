"""xsec_token 추출 헬퍼.

main.py가 만든 같은 user_data_dir + Oxylabs CN 프록시로 브라우저 띄움.
프로필 페이지 진입 후 3가지 방식으로 xsec_token 추출:
  1. request listener — 페이지가 user_posted API 호출하면 캡처
  2. window.__INITIAL_STATE__ — Vue/SSR로 박힌 state 직접 접근
  3. raw HTML 정규식 — script 태그 안 박힌 토큰 마지막 시도

사용법:
    python runners/grab_xsec_token.py 5842afd75e87e7332ea90fda

출력된 token을 xhs_config.py URL에 다음 형식으로 박으면 됨:
    https://www.xiaohongshu.com/user/profile/{user_id}?xsec_token={token}&xsec_source=pc_feed
"""
import asyncio
import os
import re
import secrets
import sys
from urllib.parse import parse_qs, urlparse

from playwright.async_api import async_playwright


def _build_oxylabs_proxy():
    """한국 IP + 세션 단위 sticky + 세션 간 rotation.

    sessid env 없으면 자동 random 생성 → 이 프로세스 안에서 동일 유지.
    """
    base_user = os.getenv("OXYLABS_USERNAME", "customer-prcs_data1_LpjIC")
    country = os.getenv("OXYLABS_COUNTRY", "kr")
    if "-cc-" in base_user:
        username_base = base_user
    else:
        username_base = f"{base_user}-cc-{country}"
    sessid = os.getenv("OXYLABS_SESSID")
    if not sessid:
        sessid = f"auto_{secrets.token_hex(4)}"
        os.environ["OXYLABS_SESSID"] = sessid
        mode = "AUTO-STICKY"
    else:
        mode = "STICKY (env)"
    sesstime = os.getenv("OXYLABS_SESSTIME", "30")
    username = f"{username_base}-sessid-{sessid}-sesstime-{sesstime}"
    print(f"[grab] {mode} sessid={sessid} country={country}")
    return {
        "server": f"http://{os.getenv('OXYLABS_HOST', 'pr.oxylabs.io')}:"
                  f"{os.getenv('OXYLABS_PORT', '7777')}",
        "username": username,
        "password": os.getenv("OXYLABS_PASSWORD", "Prcsdata_1234"),
    }


OXYLABS_PROXY = _build_oxylabs_proxy()

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
USER_DATA_DIR = os.path.abspath(os.path.join(
    BASE_DIR, "..", "crawlers", "MediaCrawler", "browser_data", "xhs_user_data_dir"
))


def extract_token_from_html(html: str):
    """raw HTML script 태그 안 박힌 xsec_token 패턴 추출.

    XHS는 SSR로 __INITIAL_STATE__를 inline script로 박음.
    형식: "xsec_token":"AB...==" 또는 xsecToken:"AB...=="
    """
    candidates = []
    # 1) JSON 형식 ("xsec_token":"...")
    for m in re.finditer(r'"xsec_token"\s*:\s*"([^"]+)"', html):
        candidates.append(m.group(1))
    # 2) JS 객체 형식 (xsecToken: "...")
    for m in re.finditer(r'xsecToken["\']?\s*:\s*["\']([^"\']+)["\']', html):
        candidates.append(m.group(1))
    # 3) URL 안 (xsec_token=...&)
    for m in re.finditer(r'xsec_token=([^&"\'\s\\]+)', html):
        token = m.group(1).rstrip('=')
        # url에 들어있는 형태는 = 가 escape되어 빠질 수 있어 복원
        if not token.endswith('=') and '%3D' not in token:
            token = token + '=='
        candidates.append(m.group(1))
    return candidates


async def main(user_id: str):
    profile_url = f"https://www.xiaohongshu.com/user/profile/{user_id}"
    print(f"[grab] user_data_dir : {USER_DATA_DIR}")
    print(f"[grab] target        : {profile_url}")
    print(f"[grab] proxy         : Oxylabs CN")

    if not os.path.exists(USER_DATA_DIR):
        print(f"\n[ERROR] user_data_dir 없음. 먼저 main.py로 QR 로그인 한 번 하세요.")
        sys.exit(1)

    captured_requests = []

    async with async_playwright() as pw:
        ctx = await pw.chromium.launch_persistent_context(
            user_data_dir=USER_DATA_DIR,
            headless=False,
            proxy=OXYLABS_PROXY,
            viewport={"width": 1920, "height": 1080},
            user_agent=(
                "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                "AppleWebKit/537.36 (KHTML, like Gecko) "
                "Chrome/131.0.0.0 Safari/537.36"
            ),
            locale="zh-CN",
        )
        page = ctx.pages[0] if ctx.pages else await ctx.new_page()

        async def on_request(req):
            url = req.url
            if "user_posted" in url or "/user/posted" in url or "homefeed" in url:
                qs = parse_qs(urlparse(url).query)
                token = qs.get("xsec_token", [""])[0]
                source = qs.get("xsec_source", [""])[0]
                if token:
                    captured_requests.append({
                        "token": token, "source": source, "url": url, "method": req.method
                    })
                    print(f"\n[req-FOUND] xsec_token: {token[:40]}... source={source}")

        page.on("request", on_request)

        try:
            print(f"\n[1/4] navigating ...")
            await page.goto(profile_url, wait_until="domcontentloaded", timeout=30000)
            await asyncio.sleep(3)
        except Exception as e:
            print(f"  goto error: {e}")

        # ===== 방법 1: __INITIAL_STATE__ 직접 접근 =====
        print(f"\n[2/4] window.__INITIAL_STATE__ 안 xsec_token 탐색")
        state_tokens = []
        try:
            state_result = await page.evaluate("""() => {
                const out = { has_state: false, tokens: [], keys_with_token: [] };
                if (!window.__INITIAL_STATE__) return out;
                out.has_state = true;
                const seen = new Set();
                const walk = (obj, path = '') => {
                    if (!obj || typeof obj !== 'object') return;
                    if (path.length > 200) return;
                    // Vue 3 ref unwrap
                    const v = (obj && '_value' in obj && typeof obj._value !== 'undefined')
                        ? obj._value : obj;
                    if (!v || typeof v !== 'object') return;
                    if (seen.has(v)) return;
                    seen.add(v);
                    if (Array.isArray(v)) {
                        for (let i = 0; i < Math.min(v.length, 50); i++) walk(v[i], path + '[' + i + ']');
                        return;
                    }
                    for (const k of Object.keys(v)) {
                        try {
                            const val = v[k];
                            if ((k === 'xsec_token' || k === 'xsecToken') && typeof val === 'string' && val.length > 10) {
                                out.tokens.push(val);
                                out.keys_with_token.push(path + '.' + k);
                            } else if (typeof val === 'object') {
                                walk(val, path + '.' + k);
                            }
                        } catch (e) {}
                    }
                };
                walk(window.__INITIAL_STATE__);
                return out;
            }""")
            print(f"  __INITIAL_STATE__ exists : {state_result.get('has_state')}")
            print(f"  tokens found             : {len(state_result.get('tokens', []))}")
            for kp in state_result.get("keys_with_token", [])[:5]:
                print(f"    at {kp}")
            state_tokens = list(set(state_result.get("tokens", [])))
            for t in state_tokens[:3]:
                print(f"    token sample : {t[:60]}...")
        except Exception as e:
            print(f"  state read error: {e}")

        # ===== 방법 2: raw HTML 정규식 =====
        print(f"\n[3/4] raw HTML 정규식 탐색")
        html_tokens = []
        try:
            html = await page.content()
            html_tokens = list(set(extract_token_from_html(html)))
            print(f"  HTML length         : {len(html)}")
            print(f"  HTML tokens unique  : {len(html_tokens)}")
            for t in html_tokens[:5]:
                print(f"    sample            : {t[:60]}...")
        except Exception as e:
            print(f"  html read error: {e}")

        # ===== 방법 3: 추가 listener 대기 (스크롤로 트리거 시도) =====
        print(f"\n[4/4] 스크롤로 lazy-load 트리거 + 15초 대기")
        try:
            for _ in range(3):
                await page.mouse.wheel(0, 1500)
                await asyncio.sleep(2)
            await asyncio.sleep(9)
        except Exception as e:
            print(f"  scroll error: {e}")

        # ===== 결과 종합 =====
        all_tokens = []
        if captured_requests:
            all_tokens.extend([(r["token"], r.get("source") or "pc_feed", "request") for r in captured_requests])
        for t in state_tokens:
            all_tokens.append((t, "pc_feed", "state"))
        for t in html_tokens:
            all_tokens.append((t, "pc_feed", "html"))

        # 중복 제거 (token 기준)
        seen = set()
        unique = []
        for t, s, src in all_tokens:
            if t not in seen:
                seen.add(t)
                unique.append((t, s, src))

        print(f"\n{'='*60}")
        print(f"  결과 — 고유 토큰 {len(unique)}개")
        print(f"{'='*60}")
        if unique:
            for i, (t, s, src) in enumerate(unique[:10], 1):
                print(f"  [{i}] ({src:7s}) token={t[:50]}...")
            best = unique[0]
            print(f"\n  ▶ 추천: 첫 번째 토큰을 xhs_config.py에 다음과 같이 박기")
            print()
            print(f'    "https://www.xiaohongshu.com/user/profile/{user_id}?'
                  f"xsec_token={best[0]}&xsec_source={best[1]}\",")
        else:
            print(f"  토큰 0개. 페이지가 로그인 상태로 못 보고 SSR 데이터 없을 가능성.")
            print(f"  HTML/state 덤프해서 더 자세한 진단 필요.")
            # debug dump
            try:
                dump_path = os.path.abspath(os.path.join(BASE_DIR, "..", "output", "grab_html_dump.html"))
                os.makedirs(os.path.dirname(dump_path), exist_ok=True)
                with open(dump_path, "w", encoding="utf-8") as f:
                    f.write(await page.content())
                print(f"  HTML dump → {dump_path}")
            except Exception as e:
                print(f"  dump 실패: {e}")

        await ctx.close()


if __name__ == "__main__":
    if len(sys.argv) < 2:
        print("Usage: python runners/grab_xsec_token.py <user_id>")
        sys.exit(1)
    asyncio.run(main(sys.argv[1]))
