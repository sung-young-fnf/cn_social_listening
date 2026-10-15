"""XHS 로그인 전용 — Oxylabs 프록시로 로그인 후 브라우저만 계속 열어둠 (수동 분석용).

grab_xhs.py 의 검증된 헬퍼(프록시/Chrome/로그인/keep-open)를 그대로 재사용한다.
크롤링은 하지 않는다. QR(또는 자동) 로그인까지만 하고 창을 살려둔다.
F12 Network / __INITIAL_STATE__ 등을 직접 분석할 때 쓴다.

사용법:
    python runners/grab_test.py                  # 저장 cookie 있으면 자동 로그인, 없으면 QR
    python runners/grab_test.py --reset-session  # cookie/user_data_dir 삭제 후 QR 다시
"""
import argparse
import asyncio
import json
import os
import shutil
import sys

from playwright.async_api import async_playwright

# grab_xhs.py 의 헬퍼 + 상수 재사용 (같은 runners/ 폴더 → 스크립트 실행 시 sys.path[0] 에 포함)
from grab_xhs import (
    require_proxy_creds,
    build_proxy,
    find_system_chrome,
    verify_proxy_ip,
    is_real_login,
    wait_for_qr_login,
    verify_login_stable,
    save_cookies_to_file,
    diag_login_signals,
    keep_browser_open,
    shutdown,
    USER_DATA_DIR,
    COOKIE_FILE,
    XHS_HOME_URL,
)

try:
    from dotenv import load_dotenv
    load_dotenv()
except ImportError:
    pass


def parse_args():
    p = argparse.ArgumentParser(
        description="XHS 로그인 전용 — Oxylabs 프록시 로그인 후 창 유지 (수동 분석용)"
    )
    p.add_argument("--reset-session", action="store_true",
                   help="user_data_dir + cookie 삭제 후 QR 재로그인")
    args = p.parse_args()
    # verify_proxy_ip / shutdown 이 참조하는 속성 — 이 스크립트는 항상 창을 유지한다
    args.keep_open = True
    return args


async def main():
    args = parse_args()

    # 회사 IP 보호 (fail-closed) — Oxylabs 자격증명 필수
    require_proxy_creds()

    if args.reset_session:
        if os.path.exists(USER_DATA_DIR):
            shutil.rmtree(USER_DATA_DIR)
            print("[reset] user_data_dir 삭제")
        if os.path.exists(COOKIE_FILE):
            os.remove(COOKIE_FILE)
            print("[reset] cookie 파일 삭제")
    os.makedirs(USER_DATA_DIR, exist_ok=True)

    proxy = build_proxy()

    chrome_path = find_system_chrome()
    if not chrome_path:
        print("[FAIL] 시스템 Chrome 못 찾음. Chrome 설치 또는 CHROME_PATH 환경변수로 지정.")
        sys.exit(1)
    chrome_env = os.getenv("CHROME_PATH")
    if chrome_env and os.path.exists(chrome_env):
        chrome_path = chrome_env
    print(f"[chrome] 시스템 Chrome: {chrome_path}")

    async with async_playwright() as pw:
        # grab_xhs.py main() 과 동일한 launch 옵션 (회사 IP 보호 + 봇 마커 회피)
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

        # 1) 출구 IP 검증 (회사 IP면 즉시 종료)
        await verify_proxy_ip(page, ctx, args)

        # 2) 저장된 cookie 로드 (있으면)
        if os.path.exists(COOKIE_FILE):
            try:
                with open(COOKIE_FILE, "r", encoding="utf-8") as f:
                    saved = json.load(f)
                await ctx.add_cookies(saved)
                print(f"[cookie] 저장본 로드 ({len(saved)}개)")
            except Exception as e:
                print(f"[cookie] 로드 실패: {e}")

        # 3) 메인 진입
        print("\n[1] xhs.com 진입")
        try:
            await page.goto(XHS_HOME_URL, wait_until="domcontentloaded", timeout=30000)
        except Exception as e:
            print(f"[ERROR] goto 실패: {e}")
            await shutdown(ctx, args, reason="메인 페이지 진입 실패")
            sys.exit(1)
        await asyncio.sleep(4)

        await diag_login_signals(page, ctx, label="explore 진입 직후")

        # 4) 로그인 — 자동(cookie) 또는 QR
        if await is_real_login(page, ctx):
            print("  ✓ 자동 로그인 (cookie 유효)")
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
            await asyncio.sleep(5)
            await diag_login_signals(page, ctx, label="QR 로그인 안정화 후")
            await save_cookies_to_file(ctx, label="(new login) ")

        # 5) 크롤링 없이 창만 유지 — 직접 분석
        await keep_browser_open(ctx, reason="로그인 완료 — 크롤링 안 함. F12/State 직접 분석")
        await ctx.close()


if __name__ == "__main__":
    asyncio.run(main())
