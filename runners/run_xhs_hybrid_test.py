"""샤오홍슈 하이브리드 크롤러 — 영속 세션 + Oxylabs (cookie 수동 복사 X)

흐름 (첫 실행 1회만 QR 스캔, 이후 영구 자동):
  1. Playwright launch_persistent_context로 user_data_dir 만들고 Oxylabs 프록시 적용
  2. 첫 실행 → QR 스캔 (폰 샤오홍슈 앱) → 로그인 → cookie 자동 user_data_dir 저장
  3. 이후 실행 → 같은 user_data_dir + Oxylabs → 자동 로그인 상태 (cookie 영속)
  4. context에서 cookie 자동 추출 → XhsClient에 주입
  5. xhs.get_user_notes로 인플루언서 게시물 목록 (xsec_token 같이 옴)
  6. 각 게시물 → xhs.get_note_by_id_from_html (HTML 파싱)
  7. CSV 19컬럼 저장

핵심:
  - cookie 수동 복사 안 함 (만료/IP 미스매치 문제 해결)
  - 발급 IP(Oxylabs CN)와 사용 IP(Oxylabs CN)가 처음부터 같음
  - QR 한 번 스캔하면 며칠~몇 주 자동 유지

사전 준비:
  pip install xhs playwright
  playwright install chromium

실행 (첫 실행 = QR 스캔 필요):
  python runners/run_xhs_hybrid_test.py --user-id 5842afd75e87e7332ea90fda --max-notes 5

실행 (이후 = 자동 진행):
  python runners/run_xhs_hybrid_test.py --user-id 5842afd75e87e7332ea90fda --max-notes 5

세션 리셋 (cookie burnt 시):
  python runners/run_xhs_hybrid_test.py --user-id ... --reset-session
"""
import argparse
import csv
import os
import random
import shutil
import sys
import time
from datetime import datetime
from typing import Dict

try:
    from xhs import XhsClient, DataFetchError, IPBlockError
except ImportError:
    print("[ERROR] xhs 라이브러리 없음. 설치: pip install xhs")
    sys.exit(1)

try:
    from playwright.sync_api import sync_playwright
except ImportError:
    print("[ERROR] playwright 없음. 설치: pip install playwright && playwright install chromium")
    sys.exit(1)


# ============ Oxylabs (도우인 자격증명 재사용) ============
OXYLABS_PROXY = {
    "server": "http://pr.oxylabs.io:7777",
    "username": "customer-prcs_data1_LpjIC-cc-cn",
    "password": "Prcsdata_1234",
}

# ============ 경로 ============
BASE_DIR = os.path.dirname(os.path.abspath(__file__))

# user_data_dir — MediaCrawler의 cdp_xhs_user_data_dir와 분리해서 별도 관리
USER_DATA_DIR = os.path.abspath(os.path.join(
    BASE_DIR, "..", "crawlers", "MediaCrawler", "browser_data",
    "xhs_hybrid_user_data_dir"
))

# stealth 스크립트 (anti-detection)
STEALTH_JS = os.path.abspath(os.path.join(
    BASE_DIR, "..", "crawlers", "MediaCrawler", "libs", "stealth.min.js"
))


def parse_args():
    p = argparse.ArgumentParser(description="샤오홍슈 하이브리드 (영속 세션)")
    p.add_argument("--user-id", required=True, help="크리에이터 user_id")
    p.add_argument("--out", default="output/hybrid_test.csv", help="결과 CSV")
    p.add_argument("--max-notes", type=int, default=5, help="테스트 게시물 수")
    p.add_argument("--no-proxy", action="store_true",
                   help="⚠️ Oxylabs 끄기 (사용자 IP 노출 — 비추천)")
    p.add_argument("--reset-session", action="store_true",
                   help="user_data_dir 삭제하고 QR 새로 로그인")
    p.add_argument("--login-timeout", type=int, default=180,
                   help="QR 스캔 대기 시간 (초)")
    return p.parse_args()


# ============ 유틸 — S3 parquet 스키마 19컬럼 ============
POST_COLUMNS = [
    "keyword", "author", "content", "likes", "stars", "comments",
    "images_captured", "post_date", "location", "post_type", "recommendations",
    "shares", "key", "timestamp", "note_title", "note_text", "unique_hash",
    "thumbnail_path", "post_url",
]


def parse_chinese_number(text):
    if not text or text == "":
        return 0
    text = str(text).replace("+", "").replace(",", "").strip()
    try:
        if "万" in text:
            return int(float(text.replace("万", "")) * 10000)
        elif "亿" in text:
            return int(float(text.replace("亿", "")) * 100000000)
        else:
            return int(float(text))
    except (ValueError, TypeError):
        return 0


def build_post_row(note: Dict, profile_id: str, timestamp_str: str) -> Dict:
    interact = note.get("interact_info", {})
    user = note.get("user", {})

    image_list = note.get("image_list", []) or []
    images_captured = len(image_list) if isinstance(image_list, list) else 0

    note_type = note.get("type", "")
    if note_type == "normal":
        post_type = "이미지"
    elif note_type == "video":
        post_type = "동영상"
    else:
        post_type = note_type

    note_id = note.get("note_id", "")
    thumbnail_path = (
        f"xiaohongshu/profile/image/{profile_id}/{note_id}/{note_id}_1.jpg"
        if note_id else ""
    )

    note_time = note.get("time", 0)
    if isinstance(note_time, int) and note_time > 0:
        ts = note_time / 1000 if note_time > 10**12 else note_time
        post_date = datetime.fromtimestamp(ts).strftime("%Y-%m-%d")
    else:
        post_date = ""

    return {
        "keyword": profile_id,
        "author": user.get("nickname", note.get("nickname", "")),
        "content": note.get("desc", ""),
        "likes": parse_chinese_number(interact.get("liked_count", 0)),
        "stars": parse_chinese_number(interact.get("collected_count", 0)),
        "comments": parse_chinese_number(interact.get("comment_count", 0)),
        "images_captured": images_captured,
        "post_date": post_date,
        "location": note.get("ip_location", ""),
        "post_type": post_type,
        "recommendations": 0,
        "shares": parse_chinese_number(interact.get("share_count", 0)),
        "key": f"{user.get('nickname', '')}__{interact.get('liked_count', 0)}",
        "timestamp": timestamp_str,
        "note_title": note.get("title", ""),
        "note_text": note.get("desc", ""),
        "unique_hash": note_id,
        "thumbnail_path": thumbnail_path,
        "post_url": f"https://www.xiaohongshu.com/explore/{note_id}",
    }


# ============ 영속 브라우저 + 로그인 ============

LOGGED_IN_SELECTOR = "xpath=//a[contains(@href, '/user/profile/')]//span[text()='我']"


def is_logged_in(page) -> bool:
    """프로필 '我' 버튼 보이면 로그인됨"""
    try:
        return page.is_visible(LOGGED_IN_SELECTOR, timeout=2000)
    except Exception:
        return False


def wait_for_qr_login(page, timeout_seconds: int) -> bool:
    """QR 스캔 후 로그인 완료까지 폴링"""
    print(f"\n    ★ 폰의 샤오홍슈 앱(小红书)으로 화면의 QR을 스캔해주세요.")
    print(f"      최대 {timeout_seconds}초 대기. 창 닫지 마세요.\n")

    start = time.time()
    last_print_at = 0
    while time.time() - start < timeout_seconds:
        try:
            if page.is_visible(LOGGED_IN_SELECTOR, timeout=500):
                return True
        except Exception:
            pass

        elapsed = int(time.time() - start)
        if elapsed - last_print_at >= 30:
            remaining = timeout_seconds - elapsed
            print(f"      ⏳ 대기 중... 남은 {remaining}초")
            last_print_at = elapsed
        time.sleep(1)
    return False


def setup_persistent_browser(use_proxy: bool, reset: bool, login_timeout: int):
    """user_data_dir 기반 영속 세션. 필요 시 QR 로그인.

    Returns:
        (playwright, context, page)
    """
    if reset and os.path.exists(USER_DATA_DIR):
        shutil.rmtree(USER_DATA_DIR)
        print(f"[Reset] user_data_dir 삭제됨")

    os.makedirs(USER_DATA_DIR, exist_ok=True)
    print(f"[Browser] user_data_dir: {USER_DATA_DIR}")

    pw = sync_playwright().start()

    launch_opts = {
        "user_data_dir": USER_DATA_DIR,
        "headless": False,  # QR 스캔 + 디버그 위해
        "viewport": {"width": 1920, "height": 1080},
        "user_agent": (
            "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
            "AppleWebKit/537.36 (KHTML, like Gecko) "
            "Chrome/131.0.0.0 Safari/537.36"
        ),
        "locale": "zh-CN",
    }
    if use_proxy:
        launch_opts["proxy"] = OXYLABS_PROXY
        print(f"[Browser] Oxylabs 프록시 적용 (cn)")
    else:
        print(f"[Browser] ⚠️ 프록시 OFF — 사용자 IP 노출")

    context = pw.chromium.launch_persistent_context(**launch_opts)

    # stealth 스크립트
    if os.path.exists(STEALTH_JS):
        try:
            context.add_init_script(path=STEALTH_JS)
            print(f"[Browser] stealth.min.js 적용")
        except Exception as e:
            print(f"[Browser] stealth 적용 실패: {e}")

    page = context.pages[0] if context.pages else context.new_page()

    # explore 페이지 진입 — 로그인 검증
    print(f"[Browser] xiaohongshu.com 접속")
    try:
        page.goto(
            "https://www.xiaohongshu.com/explore",
            wait_until="domcontentloaded",
            timeout=30000,
        )
    except Exception as e:
        print(f"[Browser] 접속 실패: {e}")
        print(f"          프록시 IP 문제 가능. --reset-session 또는 --no-proxy로 재시도")
        try:
            context.close()
            pw.stop()
        except Exception:
            pass
        sys.exit(1)
    time.sleep(3)

    # 로그인 상태 분기
    if is_logged_in(page):
        print(f"[Login] ✅ 이미 로그인됨 (영속 세션 활용)")
    else:
        print(f"[Login] 로그인 안 됨 → QR 스캔 필요")
        ok = wait_for_qr_login(page, timeout_seconds=login_timeout)
        if not ok:
            print(f"[ERROR] QR 로그인 시간 초과 (또는 실패)")
            try:
                context.close()
                pw.stop()
            except Exception:
                pass
            sys.exit(1)
        print(f"[Login] ✅ 로그인 성공! 다음 실행부터는 자동 진행")
        time.sleep(3)

    return pw, context, page


def get_cookie_string_from_context(context) -> str:
    """Playwright context에서 cookie 추출 → 문자열"""
    cookies = context.cookies()
    return "; ".join(f"{c['name']}={c['value']}" for c in cookies)


# ============ Cookie 저장/공유 ============
COOKIE_SHARE_DIR = os.path.abspath(os.path.join(BASE_DIR, "..", "output"))
COOKIE_TXT_PATH = os.path.join(COOKIE_SHARE_DIR, "xhs_cookie.txt")
COOKIE_JSON_PATH = os.path.join(COOKIE_SHARE_DIR, "xhs_session.json")


def save_cookies_for_reuse(context, source_note: str = "hybrid_test"):
    """영속 세션의 cookie를 파일로 저장 (cookie_test.py 등에서 재사용).

    저장 파일:
      output/xhs_cookie.txt   — cookie 문자열 (그대로 붙여넣기 가능)
      output/xhs_session.json — cookie + 메타데이터 (발급 시각 등)
    """
    import json
    os.makedirs(COOKIE_SHARE_DIR, exist_ok=True)

    cookies_list = context.cookies()
    cookie_str = "; ".join(f"{c['name']}={c['value']}" for c in cookies_list)

    # 1) 단순 텍스트 (재사용 빠름)
    with open(COOKIE_TXT_PATH, "w", encoding="utf-8") as f:
        f.write(cookie_str)

    # 2) JSON 메타데이터 (감사 / 만료 추적)
    meta = {
        "source": source_note,
        "saved_at": datetime.now().isoformat(),
        "ip_hint": "Oxylabs CN (발급 IP — 재사용 시도 같은 풀로)",
        "cookie_count": len(cookies_list),
        "has_a1": any(c["name"] == "a1" for c in cookies_list),
        "has_web_session": any(c["name"] == "web_session" for c in cookies_list),
        "cookies": [
            {"name": c["name"], "value": c["value"], "domain": c.get("domain", ""),
             "path": c.get("path", ""), "expires": c.get("expires", -1)}
            for c in cookies_list
        ],
    }
    with open(COOKIE_JSON_PATH, "w", encoding="utf-8") as f:
        json.dump(meta, f, ensure_ascii=False, indent=2)

    print(f"\n[Cookie] 영속 세션 → 파일 저장 완료")
    print(f"         {COOKIE_TXT_PATH}")
    print(f"         {COOKIE_JSON_PATH}")
    print(f"         cookie_test.py에서 자동으로 이 파일 사용 가능")


def make_sign_function(page):
    """page 위에서 _webmsxyw 호출 — 영속 세션이라 a1/web_session 정합성 자동.

    a1을 매번 강제 주입할 필요 없음 (이미 로그인된 페이지 == 정확한 a1 보유).
    """
    def sign(uri, data=None, a1="", web_session=""):
        try:
            result = page.evaluate(
                "([url, data]) => window._webmsxyw(url, data)",
                [uri, data]
            )
            return {"x-s": result["X-s"], "x-t": str(result["X-t"])}
        except Exception as e:
            print(f"[sign] 실패, 페이지 reload 후 재시도: {e}")
            page.reload()
            time.sleep(2)
            result = page.evaluate(
                "([url, data]) => window._webmsxyw(url, data)",
                [uri, data]
            )
            return {"x-s": result["X-s"], "x-t": str(result["X-t"])}
    return sign


def human_sleep(min_s=8.0, max_s=15.0):
    time.sleep(random.uniform(min_s, max_s))


def main():
    args = parse_args()
    os.makedirs(os.path.dirname(args.out) or ".", exist_ok=True)

    print(f"""
============================================================
  샤오홍슈 하이브리드 (영속 세션 + Oxylabs)
============================================================
  user_id    : {args.user_id}
  out        : {args.out}
  max_notes  : {args.max_notes}
  proxy      : {'OFF' if args.no_proxy else 'Oxylabs (cn)'}
  reset      : {args.reset_session}
============================================================
""")

    pw, context, page = setup_persistent_browser(
        use_proxy=not args.no_proxy,
        reset=args.reset_session,
        login_timeout=args.login_timeout,
    )

    try:
        # 영속 세션에서 cookie 자동 추출
        cookie_str = get_cookie_string_from_context(context)
        cookie_dict = {c["name"]: c["value"] for c in context.cookies()}

        print(f"\n[Cookie] 영속 세션에서 자동 추출 ({len(cookie_dict)}개)")
        if "web_session" not in cookie_dict:
            print("[ERROR] web_session 없음 — 로그인 실패 추정")
            sys.exit(1)
        print(f"         keys: {list(cookie_dict.keys())}")

        # 재사용 위해 파일로 저장 (cookie_test.py 등에서 활용)
        save_cookies_for_reuse(context, source_note="hybrid_test")

        # XhsClient 셋업
        sign_func = make_sign_function(page)

        proxies = None
        if not args.no_proxy:
            proxy_url = (
                f"http://{OXYLABS_PROXY['username']}:"
                f"{OXYLABS_PROXY['password']}@pr.oxylabs.io:7777"
            )
            proxies = {"http": proxy_url, "https": proxy_url}

        client = XhsClient(
            cookie=cookie_str,
            sign=sign_func,
            proxies=proxies,
        )

        # ===== Step 1: get_user_notes =====
        print(f"\n[1/2] get_user_notes 호출 (user_id={args.user_id})")
        try:
            res = client.get_user_notes(args.user_id, cursor="")
        except IPBlockError as e:
            print(f"[ERROR] IP 차단: {e}")
            print("       → Oxylabs IP가 burnt. --reset-session으로 새 세션 시도")
            sys.exit(1)
        except Exception as e:
            print(f"[ERROR] {type(e).__name__}: {e}")
            print("       → cookie/IP/시그니처 문제. 자세한 에러 메시지 확인")
            sys.exit(1)

        all_notes = res.get("notes", [])
        print(f"      받은 게시물: {len(all_notes)}개 (xsec_token 포함)")

        if not all_notes:
            print(f"      게시물 0개. 응답: {res}")
            sys.exit(1)

        notes_to_fetch = all_notes[:args.max_notes]
        print(f"      이번 테스트: {len(notes_to_fetch)}개")
        for i, n in enumerate(notes_to_fetch, 1):
            print(f"        [{i}] {n.get('note_id')}")

        # ===== Step 2: 각 게시물 상세 (HTML 파싱) =====
        print(f"\n[2/2] 게시물 상세 — get_note_by_id_from_html")

        timestamp_str = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        rows = []

        for i, n in enumerate(notes_to_fetch, 1):
            note_id = n.get("note_id", "")
            xsec_token = n.get("xsec_token", "")
            if not xsec_token:
                print(f"      [{i}/{len(notes_to_fetch)}] ⚠ xsec_token 없음, 스킵")
                continue

            try:
                detail = client.get_note_by_id_from_html(
                    note_id=note_id,
                    xsec_token=xsec_token,
                    xsec_source="pc_user",
                )
                row = build_post_row(detail, args.user_id, timestamp_str)
                rows.append(row)
                print(f"      [{i}/{len(notes_to_fetch)}] ✅ {note_id} "
                      f"likes={row['likes']} comments={row['comments']}")
            except IPBlockError as e:
                print(f"      [{i}/{len(notes_to_fetch)}] ❌ IP 차단: {e}")
                break
            except DataFetchError as e:
                print(f"      [{i}/{len(notes_to_fetch)}] ❌ {e}")
            except Exception as e:
                print(f"      [{i}/{len(notes_to_fetch)}] ❌ {type(e).__name__}: {e}")

            if i < len(notes_to_fetch):
                human_sleep(8, 15)

        # CSV 저장
        if rows:
            with open(args.out, "w", encoding="utf-8-sig", newline="") as f:
                writer = csv.DictWriter(f, fieldnames=POST_COLUMNS)
                writer.writeheader()
                writer.writerows(rows)
            print(f"\n[OK] {len(rows)}행 → {args.out}")
        else:
            print(f"\n[WARN] 저장된 행 없음")

    finally:
        # 세션은 user_data_dir에 영속이라 close해도 OK
        try:
            context.close()
        except Exception:
            pass
        try:
            pw.stop()
        except Exception:
            pass


if __name__ == "__main__":
    main()
