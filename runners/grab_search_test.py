"""XHS 프로필 진입 가능 여부 테스트 — 검색 흐름 검증 전용.

목적:
    data/xhs_config.py의 XHS_CREATOR_ID_LIST 전원에 대해
    "검색 → 프로필 진입"이 되는지만 확인한다. (크롤링/저장 X)
    프로필에 진입해서 게시글이 1개라도 잡히면 진입 성공으로 본다.

검색 흐름 (2026-06-30 봇 감지 강화 대응):
    봇 감지가 심해져서 검색 직후 全部 탭에 프로필 카드가 안 뜨고
    '安全验证(请勿频繁操作)' captcha 모달 + '没找到相关内容'만 나오는 케이스가 많아짐.
    [用户] 탭을 클릭하면 해당 계정이 정상적으로 노출됨(사용자 F12 확인).
    → 全部 탭 우선 매칭은 폐기하고, 항상 [用户] 탭 경유로 진입한다.
      검색 직후 captcha 모달이 떠있으면 X로 닫고 [用户] 탭을 클릭.

로그인/프록시/IP검증은 grab_xhs.py 함수를 그대로 import — 동작 100% 동일.

사용법:
    python runners/grab_search_test.py                  # 전체 검사
    python runners/grab_search_test.py --reset-session  # QR 재발급
    python runners/grab_search_test.py --limit 15       # 앞 15명만
    python runners/grab_search_test.py --start-index 68 # 68번째부터

출력 (콘솔 요약):
    5842afd75e... (虞书欣Esther): ✅ 1개
    5583d44062... (HOKA):         ✅ 8개   ← 用户 탭 클릭으로 진입
    ...
"""
import argparse
import asyncio
import os
import random
import re
import shutil
import sys
from datetime import datetime, timedelta

from playwright.async_api import async_playwright

# .env 로드 (Oxylabs 자격증명 등)
try:
    from dotenv import load_dotenv
    load_dotenv()
except ImportError:
    pass

# === grab_xhs.py 함수 재사용 (로그인/프록시/IP검증 흐름 그대로) ===
# 같은 runners/ 디렉토리라 직접 import 가능 (script dir이 sys.path[0]).
from grab_xhs import (  # noqa: E402
    verify_proxy_ip,
    find_system_chrome,
    is_real_login,
    verify_login_stable,
    wait_for_qr_login,
    save_cookies_to_file,
    diag_login_signals,
    _input_search_keyword,
    _captcha_present,
    ROTATE_MARKERS,
    load_xhs_creator_map,
    load_persisted_sessid,
    save_persisted_sessid,
    save_session_state,
    require_proxy_creds,
    shutdown,
    COOKIE_FILE,
    USER_DATA_DIR,
    OUTPUT_DIR,
    XHS_HOME_URL,
)

# 로컬 릴레이 — 브라우저 재실행/QR 없이 Oxylabs IP 핫스왑 (grab_xhs와 동일)
try:
    from oxylabs_relay import build_relay_from_env
except ImportError:
    from runners.oxylabs_relay import build_relay_from_env
import secrets  # noqa: E402


# === IP 교체(sessid 로테이션)를 유발하는 실패 판정 (테스트용) ===
# grab_xhs와 동일 취지: 접속/차단성 실패는 IP 교체로 재시도, '계정 진짜 없음'은 제외.
_ROTATE_REASONS = ("search_input_failed", "search_not_navigated", "goto_failed", "captcha")


def _should_rotate(reason, msg):
    if reason in _ROTATE_REASONS:
        return True
    m = msg or ""
    return any(k in m for k in ROTATE_MARKERS)


# === URL 폴링 헬퍼 (glob 대신 단순 포함 검사) ===
async def _wait_for_url_contains(page, needle, timeout=10):
    """page.url에 needle 문자열이 들어올 때까지 최대 timeout초 폴링. 들어오면 True."""
    for _ in range(timeout):
        try:
            if needle in (page.url or ""):
                return True
        except Exception:
            pass
        await asyncio.sleep(1)
    try:
        return needle in (page.url or "")
    except Exception:
        return False


# === 검색 결과에서 user 카드 link 탐색 ===
async def _find_user_link(page, user_id, timeout=20000):
    """검색 결과 페이지에서 a[href*='/user/profile/{user_id}'] 카드 찾기.
    찾으면 locator, 못 찾으면 None.
    """
    loc = page.locator(f"a[href*='/user/profile/{user_id}']").first
    try:
        await loc.wait_for(state="visible", timeout=timeout)
        return loc
    except Exception:
        return None


# === [用户] 탭 클릭 — 全部 탭에 프로필 카드 안 뜰 때 ===
async def _click_user_tab(page):
    """검색 결과 상단 탭(全部/图文/视频/用户) 중 '用户'를 클릭. 성공 True.

    실제 DOM (F12 확정):
      <div id="user" class="channel">用户</div>
      형제로 全部/图文/视频 채널 div + filter div.
      Role=generic, keyboard-focusable=No 인 평범한 div라 get_by_text/click이
      actionability 체크에서 실패했음.

    클릭은 grab_xhs_keyword._apply_sort_filter 패턴 — bounding_box 중심 좌표로
    mouse move → down → up. Playwright actionability 우회.
    """
    # 1. 정확 셀렉터 — id=user (class=channel)
    tab = page.locator("div#user.channel").first
    try:
        await tab.wait_for(state="visible", timeout=20000)
    except Exception:
        # id 변형 대비 — channel div 중 텍스트 '用户'
        tab = page.locator("div.channel", has_text="用户").first
        try:
            await tab.wait_for(state="visible", timeout=20000)
        except Exception:
            print("     · [diag] 用户 탭(div#user.channel) 못 찾음")
            return False

    # 2. 좌표 기반 mouse click (keyword 크롤러와 동일 — actionability 우회)
    try:
        box = await tab.bounding_box()
        if box and box["width"] >= 1 and box["height"] >= 1:
            cx = box["x"] + box["width"] / 2
            cy = box["y"] + box["height"] / 2
            await page.mouse.move(cx, cy)
            await asyncio.sleep(0.15)
            await page.mouse.down()
            await asyncio.sleep(0.08)
            await page.mouse.up()
            return True
    except Exception:
        pass

    # 3. fallback — 일반 click (force 포함)
    for method in (
        lambda: tab.click(timeout=20000),
        lambda: tab.click(force=True, timeout=20000),
    ):
        try:
            await method()
            return True
        except Exception:
            continue

    return False


# === 安全验证 captcha 모달 닫기 (검색 직후 떠서 탭 클릭 막는 케이스) ===
async def _dismiss_captcha_modal(page):
    """검색 결과에 뜨는 '安全验证(请勿频繁操作)' captcha 모달을 X 버튼으로 닫는다.

    모달 없으면 False, 닫았으면 True.

    실제 DOM (사용자 F12 확정, 2026-06-30):
      <div class="captcha-modal-content">
        <div class="captcha-modal__header">
          <div class="captcha-modal-title">安全验证</div>
          <div class="captcha-modal__close" title="关闭"><svg.../></div>
    close 버튼이 svg 든 평범한 div라 _click_user_tab과 동일하게
    bounding_box 중심 좌표 mouse click으로 actionability 우회.
    """
    close_btn = page.locator("div.captcha-modal__close").first
    try:
        await close_btn.wait_for(state="visible", timeout=2000)
    except Exception:
        return False  # 모달 없음 — 정상

    print(f"     · ⚠ 安全验证 captcha 모달 감지 → X 버튼으로 닫기")

    # 1. 좌표 기반 mouse click (svg 든 div — actionability 우회)
    try:
        box = await close_btn.bounding_box()
        if box and box["width"] >= 1 and box["height"] >= 1:
            cx = box["x"] + box["width"] / 2
            cy = box["y"] + box["height"] / 2
            await page.mouse.move(cx, cy)
            await asyncio.sleep(0.15)
            await page.mouse.down()
            await asyncio.sleep(0.08)
            await page.mouse.up()
            await asyncio.sleep(0.8)
            return True
    except Exception:
        pass

    # 2. fallback — 일반 click (force 포함)
    for method in (
        lambda: close_btn.click(timeout=3000),
        lambda: close_btn.click(force=True, timeout=3000),
    ):
        try:
            await method()
            await asyncio.sleep(0.8)
            return True
        except Exception:
            continue

    print(f"     · [diag] captcha 모달 X 버튼 클릭 실패")
    return False


# === 검색 → 프로필 진입 (用户 탭 강제 진입) ===
async def navigate_via_search_with_user_tab(page, user_id, nickname):
    """홈 → 검색 → (全部에서 카드 탐색) → 없으면 用户 탭 클릭 후 재탐색 → goto.

    반환: (success, msg, reason)
        reason: ok / search_input_failed / search_not_navigated / not_found / goto_failed / url_mismatch
    """
    # 1. 홈 진입 → 검색박스 타이핑 → Enter → 결과 페이지 (grab_xhs 헬퍼 그대로)
    #    검색이 실제 실행돼 search_result 페이지로 이동했는지 보증 — 안 되면 1회 재시도.
    reached = False
    last_msg = ""
    for attempt in range(2):
        ok, msg = await _input_search_keyword(page, nickname)
        last_msg = msg
        if not ok:
            if attempt == 0:
                continue
            return False, msg, "search_input_failed"
        # 검색 결과 페이지(URL에 search_result) 도달 확인
        if await _wait_for_url_contains(page, "search_result", timeout=20):
            reached = True
            break
        print(f"     · 검색 결과 페이지 미도달(search_result 없음) → 재시도 {attempt+1}/2")
    if not reached:
        return False, f"검색 결과 페이지 미도달 ({last_msg})", "search_not_navigated"

    # 2. ★ 봇 감지 강화(2026-06-30)로 全部 탭에 카드가 안 뜨는 케이스가 많아짐.
    #    → 全部 탭 우선 매칭은 주석 처리하고, 항상 [用户] 탭을 먼저 클릭한 뒤 탐색.
    # --- (기존 全部 탭 우선 매칭 — 주석 처리) ---
    # link = await _find_user_link(page, user_id, timeout=5000)
    # via = "全部"
    # if link is None:
    #     print(f"     · 全部 탭에 프로필 카드 없음 → [用户] 탭 클릭 후 재탐색")
    #     clicked = await _click_user_tab(page)
    #     if clicked:
    #         await asyncio.sleep(2.5)  # 用户 목록 로딩 대기
    #         link = await _find_user_link(page, user_id, timeout=6000)
    #         via = "用户"
    #     else:
    #         print(f"     · [用户] 탭 클릭 실패 (selector 못 찾음)")
    # if link is None:
    #     return False, f"검색 결과에 user_id 없음 (全部+用户 모두)", "not_found"

    # 2. 무조건 [用户] 탭 클릭 후 사용자 목록에서 user_id 매칭 카드 탐색
    #    검색 직후 '安全验证(请勿频繁操作)' captcha 모달이 떠서 탭 클릭을 막는 경우가 있음
    #    (2026-06-30 확인). → 모달이 있으면 먼저 X로 닫고 [用户] 탭을 클릭한다.
    via = "用户"
    await _dismiss_captcha_modal(page)
    # ★ 닫기 불가한 이미지 선택형 캡차가 떠 있으면 즉시 IP 교체 트리거
    if await _captcha_present(page):
        return False, "安全验证 captcha 감지 — IP 교체 필요", "captcha"
    print(f"     · [用户] 탭 클릭 후 사용자 목록에서 탐색")
    clicked = await _click_user_tab(page)
    if not clicked:
        # 모달이 (다시) 떠있을 수 있음 — 한 번 더 닫고 재시도
        await _dismiss_captcha_modal(page)
        clicked = await _click_user_tab(page)
    if not clicked:
        return False, f"[用户] 탭 클릭 실패 (user_id={user_id[:10]}...)", "not_found"
    await asyncio.sleep(2.5)  # 用户 목록 로딩 대기
    link = await _find_user_link(page, user_id, timeout=20000)

    if link is None:
        return False, f"검색 결과에 user_id 없음 (用户 탭)", "not_found"

    # 4. href 추출 → 현재 탭에서 navigate (click은 target=_blank라 새 탭 → X)
    href = await link.get_attribute("href")
    if not href:
        return False, "user_link href 추출 실패", "not_found"

    if href.startswith("/"):
        base = "https://www.rednote.com" if "rednote.com" in page.url else "https://www.xiaohongshu.com"
        href = f"{base}{href}"
    elif not href.startswith("http"):
        return False, f"href 형식 이상: {href[:60]}", "not_found"

    # &tab=note 제거 (수동 클릭 URL엔 없음 — 붙으면 no-posts 뷰)
    href = re.sub(r'[?&]tab=note(?=&|$)', '', href)
    href = href.replace("?&", "?").rstrip("?&")

    try:
        await page.goto(href, wait_until="domcontentloaded", timeout=20000)
    except Exception as e:
        return False, f"profile goto 실패: {e}", "goto_failed"

    # 5. URL 전환 검증 — glob(wait_for_url) 대신 /user/profile/{uid} 포함 여부로 판정.
    #    (glob '*'가 '/'를 못 넘어서 xsec_token에 '/' 섞이면 오탐했음)
    if not await _wait_for_url_contains(page, f"/user/profile/{user_id}", timeout=20):
        return False, f"URL 미전환 (현재: {page.url[:80]})", "url_mismatch"

    return True, f"OK (via {via})", "ok"


# === 프로필 진입 + 게시글 1개라도 잡히는지 확인 ===
async def check_profile(page, user_id, nickname):
    """검색→진입 후 게시글 감지. 스크롤/상세진입 없이 빠르게 1차 응답만.

    반환 dict:
        entered (bool), reason (str), msg (str), count (int)
    """
    if not nickname:
        return {"entered": False, "reason": "no_nickname", "msg": "nickname 매핑 없음", "count": 0}

    # user_posted 응답 listener (page.goto 전 등록 필수)
    captured_ids = set()

    async def on_response(resp):
        try:
            url = resp.url
            if "user_posted" not in url:
                return
            if user_id not in url:
                return
            data = await resp.json()
            if data.get("success"):
                for n in (data.get("data", {}).get("notes") or []):
                    nid = n.get("note_id", "")
                    if nid:
                        captured_ids.add(nid)
        except Exception:
            pass

    page.on("response", on_response)
    try:
        ok, msg, reason = await navigate_via_search_with_user_tab(page, user_id, nickname)
        if not ok:
            return {"entered": False, "reason": reason, "msg": msg, "count": 0}

        # 진입 성공 — 게시글 자체 호출 대기 (스크롤 없이 첫 페이지만)
        await asyncio.sleep(6)

        # 세션 끊김 체크
        if "/login" in page.url:
            return {"entered": False, "reason": "session_lost", "msg": "/login redirect", "count": 0}

        # listener로 못 받았으면 DOM a[href]로 노트 ID 보충 카운트
        count = len(captured_ids)
        if count == 0:
            try:
                count = await page.evaluate("""() => {
                    const ids = new Set();
                    document.querySelectorAll('a[href]').forEach(a => {
                        const m = (a.href || '').match(/\\/(explore|note|discovery\\/item)\\/([a-z0-9]{20,32})/);
                        if (m) ids.add(m[2]);
                    });
                    return ids.size;
                }""")
            except Exception:
                count = 0

        return {"entered": True, "reason": "ok", "msg": msg, "count": count}
    finally:
        try:
            page.remove_listener("response", on_response)
        except Exception:
            pass


def parse_args():
    p = argparse.ArgumentParser(
        description="XHS 프로필 진입 가능 여부 테스트 (검색 흐름 검증, 用户 탭 fallback 포함)",
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    p.add_argument("--reset-session", action="store_true",
                   help="user_data_dir + cookie 리셋 (QR 재발급)")
    p.add_argument("--keep-open", action="store_true",
                   help="완료 후 브라우저 안 닫음 (F12 분석용)")
    p.add_argument("--start-index", type=int, default=0,
                   help="XHS_CREATOR_ID_LIST의 N번째부터 (0-indexed)")
    p.add_argument("--limit", type=int, default=0,
                   help="검사 인원 제한 (0=전체)")
    p.add_argument("--gap-min", type=float, default=4.0, help="계정 간 최소 지터(초)")
    p.add_argument("--gap-max", type=float, default=7.0, help="계정 간 최대 지터(초)")
    # 배치 + 휴식 (봇 감지 회피) — grab_xhs와 동일 패턴
    p.add_argument("--batch-size", type=int, default=10,
                   help="배치 당 계정 수 (기본 10)")
    p.add_argument("--batch-rest", type=int, default=600,
                   help="배치 사이 휴식(초, 기본 600=10분). 0이면 휴식 없음")
    # === IP 자동 교체 (릴레이 sessid 핫스왑) ===
    p.add_argument("--max-ip-rotations", type=int, default=8,
                   help="홈진입 실패/캡차 등 감지 시 IP 자동 교체 최대 횟수(전체 통틀어, 기본 8). 0=교체 안 함")
    p.add_argument("--rotate-retries", type=int, default=2,
                   help="한 계정에서 IP 교체 후 재시도 최대 횟수 (기본 2). 소진 시 해당 계정 SKIP")
    return p.parse_args()


async def main():
    args = parse_args()
    os.makedirs(OUTPUT_DIR, exist_ok=True)

    # creator 매핑 (xhs_config.py 주석에서 {user_id: nickname})
    creator_map = load_xhs_creator_map()
    if not creator_map:
        print("[FAIL] xhs_config.py에서 creator 매핑 못 받음.")
        sys.exit(1)
    all_ids = list(creator_map.keys())
    total_all = len(all_ids)

    if args.start_index < 0 or args.start_index >= total_all:
        print(f"[FAIL] --start-index {args.start_index} 범위 벗어남 (전체 {total_all}명)")
        sys.exit(1)
    user_ids = all_ids[args.start_index:]
    if args.limit and args.limit > 0:
        user_ids = user_ids[:args.limit]
    print(f"[creator-map] 전체 {total_all}명 중 {len(user_ids)}명 검사 "
          f"(start-index={args.start_index}, limit={args.limit or '전체'})")

    # reset
    if args.reset_session:
        if os.path.exists(USER_DATA_DIR):
            shutil.rmtree(USER_DATA_DIR)
            print("[reset] user_data_dir 삭제")
        if os.path.exists(COOKIE_FILE):
            os.remove(COOKIE_FILE)
            print("[reset] cookie 파일 삭제")
    os.makedirs(USER_DATA_DIR, exist_ok=True)

    # === 로컬 릴레이 기동 — 브라우저는 릴레이만 바라봄(재실행/QR 없이 IP 핫스왑) ===
    require_proxy_creds()
    initial_sessid = (os.getenv("OXYLABS_SESSID") or load_persisted_sessid()
                      or f"auto_{secrets.token_hex(4)}")
    save_persisted_sessid(initial_sessid)
    relay = build_relay_from_env(sessid=initial_sessid)
    await relay.start()
    save_session_state(relay_port=relay.port)
    proxy = {"server": relay.address}

    chrome_path = find_system_chrome()
    if not chrome_path:
        print("[FAIL] 시스템 Chrome 못 찾음. Chrome 설치 또는 CHROME_PATH 지정.")
        sys.exit(1)
    chrome_env = os.getenv("CHROME_PATH")
    if chrome_env and os.path.exists(chrome_env):
        chrome_path = chrome_env
    print(f"[chrome] {chrome_path}")

    async with async_playwright() as pw:
        launch_kwargs = {
            "user_data_dir": USER_DATA_DIR,
            "headless": False,
            "proxy": proxy,
            "viewport": {"width": 1920, "height": 1080},
            "locale": "zh-CN",
            "executable_path": chrome_path,
            "channel": "chrome",
            "args": [
                "--disable-blink-features=AutomationControlled",
                "--force-webrtc-ip-handling-policy=disable_non_proxied_udp",
                "--lang=zh-CN",
                "--no-first-run",
                "--no-default-browser-check",
            ],
        }
        ctx = await pw.chromium.launch_persistent_context(**launch_kwargs)
        page = ctx.pages[0] if ctx.pages else await ctx.new_page()

        # 1) IP 검증 (xhs 접속 전, fail-closed) — grab_xhs 그대로
        await verify_proxy_ip(page, ctx, args)

        # 2) 저장된 cookie 로드
        if os.path.exists(COOKIE_FILE):
            try:
                import json
                with open(COOKIE_FILE, "r", encoding="utf-8") as f:
                    saved = json.load(f)
                await ctx.add_cookies(saved)
                print(f"[cookie] 저장본 로드 ({len(saved)}개)")
            except Exception as e:
                print(f"[cookie] 로드 실패: {e}")

        # 3) explore 진입 + 로그인 (자동/QR) — grab_xhs 흐름 그대로
        print("\n[1] xhs.com 진입")
        try:
            await page.goto(XHS_HOME_URL, wait_until="domcontentloaded", timeout=30000)
        except Exception as e:
            print(f"[ERROR] goto 실패: {e}")
            await shutdown(ctx, args, reason="메인 진입 실패")
            sys.exit(1)
        await asyncio.sleep(4)

        await diag_login_signals(page, ctx, label="explore 진입 직후")

        if await is_real_login(page, ctx):
            print("  ✓ 자동 로그인 (user_data_dir cookie 유효)")
            await save_cookies_to_file(ctx, label="(refresh) ")
        else:
            ok = await wait_for_qr_login(page, ctx)
            if not ok:
                print("[ERROR] QR 시간 초과")
                await shutdown(ctx, args, reason="QR 로그인 시간 초과")
                sys.exit(1)
            stable = await verify_login_stable(page, ctx, timeout=30, stable_count=2, interval=3)
            if not stable:
                print("  ⚠ 안정화 timeout — 그래도 진행")
            # QR 로그인 직후 무조건 20초 대기 후 시작 (grab_xhs와 동일)
            await asyncio.sleep(20)
            await diag_login_signals(page, ctx, label="QR 로그인 안정화 후")
            await save_cookies_to_file(ctx, label="(new login) ")

        # 4) 순회 — 검색 진입 테스트 (배치 + 휴식, 봇 감지 회피)
        results = {}
        total = len(user_ids)
        session_invalid = False
        batch_size = max(1, args.batch_size)
        n_batches = (total + batch_size - 1) // batch_size
        rotations_used = 0  # 전체 실행 통틀어 IP 교체 누적 (--max-ip-rotations 상한)

        for batch_idx in range(n_batches):
            b_start = batch_idx * batch_size
            b_end = min(b_start + batch_size, total)
            now_str = datetime.now().strftime("%H:%M")
            print(f"\n[배치 {batch_idx+1}/{n_batches}] ({now_str}) 계정 {b_start+1}-{b_end} ({b_end-b_start}명)")

            for idx in range(b_start + 1, b_end + 1):
                uid = user_ids[idx - 1]
                if idx > 1:
                    await asyncio.sleep(random.uniform(args.gap_min, args.gap_max))
                nickname = creator_map.get(uid)
                nick_str = nickname or "(nickname 미등록)"
                print(f"\n  [{idx}/{total}] {uid} ({nick_str})")

                # 접속 실패(홈진입/검색박스/캡차 등)면 IP 교체 후 같은 계정 재시도
                acct_rotate = 0
                while True:
                    r = await check_profile(page, uid, nickname)
                    if r["entered"] or not _should_rotate(r["reason"], r["msg"]):
                        break
                    if args.max_ip_rotations <= 0 or rotations_used >= args.max_ip_rotations:
                        print(f"  ⚠ IP 교체 한도 소진 ({rotations_used}/{args.max_ip_rotations}) — 교체 없이 SKIP")
                        break
                    if acct_rotate >= args.rotate_retries:
                        print(f"  ⚠ 이 계정 IP 교체 재시도 {acct_rotate}회 소진 — SKIP")
                        break
                    acct_rotate += 1
                    rotations_used += 1
                    print(f"  ♻ IP 교체 트리거 (사유: {r['msg'][:45]}) "
                          f"[누적 {rotations_used}/{args.max_ip_rotations}, "
                          f"이 계정 {acct_rotate}/{args.rotate_retries}]")
                    new_sessid = relay.rotate_sessid()
                    save_persisted_sessid(new_sessid)
                    await asyncio.sleep(4)  # 새 IP 정착 대기
                    try:
                        await verify_proxy_ip(page, ctx, args)  # 새 출구 IP 로그 + 회사IP fail-closed
                    except SystemExit:
                        await relay.close()
                        raise
                    # 루프 상단으로 → 같은 계정 새 IP로 재테스트

                results[uid] = r

                if r["entered"]:
                    print(f"  → ✅ 진입 OK ({r['msg']}) — 게시글 {r['count']}개")
                else:
                    print(f"  → ⏭ SKIP — {r['reason']}: {r['msg']}")
                    if r["reason"] == "session_lost":
                        print("  ⚠ 세션 끊김 — 중단. --reset-session 필요")
                        session_invalid = True
                        break

            if session_invalid:
                break

            # 배치 휴식 (마지막 배치는 휴식 안 함)
            if batch_idx + 1 < n_batches and args.batch_rest > 0:
                rest_min = args.batch_rest / 60
                done_at = datetime.now().strftime("%H:%M")
                resume_at = (datetime.now() + timedelta(seconds=args.batch_rest)).strftime("%H:%M")
                print(f"\n  ✓ 배치 {batch_idx+1} 완료 ({done_at}) → {rest_min:.0f}분 휴식 (재개 {resume_at})")
                await asyncio.sleep(args.batch_rest)

        # 5) 요약 (예시 포맷)
        print(f"\n{'='*50}\n  요약\n{'='*50}")
        enter_ok = skip = 0
        for uid in user_ids:
            r = results.get(uid)
            if not r:
                continue
            nick = creator_map.get(uid, "")
            label = f"  {uid[:10]}... ({nick}):"
            if r["entered"]:
                enter_ok += 1
                print(f"{label} ✅ {r['count']}개")
            else:
                skip += 1
                print(f"{label} ⏭ SKIP — {r['reason']}")

        print(f"\n  진입 성공 {enter_ok} / 실패(SKIP) {skip} / 검사 {len(results)} "
              f"/ IP 교체 {rotations_used}회")
        if session_invalid:
            print("  ⚠ 세션 끊김으로 중단됨 — 나머지 미검사")

        await shutdown(ctx, args, reason="검사 완료")
        await relay.close()


if __name__ == "__main__":
    asyncio.run(main())
