"""샤오홍슈 하이브리드 — 쿠키 입력 방식 (검증용)

`run_xhs_hybrid_test.py`는 QR + 영속 세션 (운영용).
이 스크립트는 **이미 가지고 있는 cookie로 빠르게 검증**할 때 사용.

사용 시나리오:
  - 다른 PC에서 PC Chrome으로 로그인 → cookie 따와서 이 PC에서 시도
  - 영속 세션 셋업 전 cookie 정합성 검증
  - 진단용 (Oxylabs 끄고 사용자 IP로 직접 시도 등)

흐름:
  1. --cookie 인자 → 자동 sanitize + a1/web_session 검증
  2. Playwright (anonymous, 시그니처 생성용) — Oxylabs 프록시 적용
  3. XhsClient 호출 → get_user_notes → get_note_by_id_from_html
  4. CSV 저장

사전 준비:
  pip install xhs playwright
  playwright install chromium

실행:
  # cookie 따는 법: F12 Network → 요청 클릭 → Request Headers의 Cookie 줄 통째 복사
  python runners/run_xhs_cookie_test.py \\
      --user-id 5842afd75e87e7332ea90fda \\
      --cookie "a1=...; web_session=...; webId=...; ..."

진단용 (사용자 IP 노출 — 1회 진단만):
  python runners/run_xhs_cookie_test.py --user-id ... --cookie "..." --no-proxy
"""
import argparse
import csv
import os
import random
import re
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


# ====================================================================
# ★ 빠른 실행용 — 여기 값만 바꾸고 그냥 `python ...`으로 돌리면 됨 ★
# ====================================================================

# 테스트할 인플루언서 user_id (xhs_config.py에서 골라옴)
USER_ID = "5842afd75e87e7332ea90fda"   # 虞书欣Esther

# Cookie — 우선순위:
#   1) hybrid_test가 저장한 output/xhs_cookie.txt (있으면 자동 사용 ⭐ 추천)
#   2) 아래 COOKIE 변수에 직접 박은 값 (수동 입력)
#
# hybrid_test로 QR 로그인 한 번 하면 output/xhs_cookie.txt에 자동 저장됨.
# 그 cookie는 Oxylabs IP에서 발급된 것이라 cookie_test도 Oxylabs로 OK.
#
# 직접 박을 거면 (보통 안 권장 — IP 미스매치 가능):
#   F12 Network → Request Headers의 Cookie 줄 통째 복사
COOKIE = """\
a1=여기에박을때만값; \
web_session=...\
"""

# 테스트할 게시물 수
MAX_NOTES = 5

# 결과 CSV 저장 경로
OUTPUT = "output/cookie_test.csv"

# 옵션
USE_PROXY = True       # False면 Oxylabs 안 쓰고 사용자 PC IP 직접 사용 (진단용)
HEADLESS = False       # True면 Playwright 창 숨김

# ====================================================================
# 아래는 수정 안 하셔도 됨
# ====================================================================


# ============ Oxylabs (도우인 자격증명 재사용) ============
OXYLABS_PROXY = {
    "server": "http://pr.oxylabs.io:7777",
    "username": "customer-prcs_data1_LpjIC-cc-cn",
    "password": "Prcsdata_1234",
}

# stealth 스크립트
BASE_DIR = os.path.dirname(os.path.abspath(__file__))
STEALTH_JS = os.path.abspath(os.path.join(
    BASE_DIR, "..", "crawlers", "MediaCrawler", "libs", "stealth.min.js"
))

# hybrid_test가 저장하는 cookie 파일 (있으면 자동 사용)
COOKIE_TXT_PATH = os.path.abspath(os.path.join(
    BASE_DIR, "..", "output", "xhs_cookie.txt"
))


def load_cookie_from_file_if_exists() -> str:
    """hybrid_test가 저장한 cookie 파일이 있으면 로드.

    Returns:
        cookie 문자열 (있으면) 또는 None (없으면)
    """
    if not os.path.exists(COOKIE_TXT_PATH):
        return None
    try:
        with open(COOKIE_TXT_PATH, "r", encoding="utf-8") as f:
            content = f.read().strip()
        if content and "web_session=" in content:
            return content
    except Exception as e:
        print(f"[Cookie file] 로드 실패: {e}")
    return None


def parse_args():
    """argparse — 모두 optional. 인자 없으면 위쪽 상수값 사용."""
    p = argparse.ArgumentParser(description="샤오홍슈 하이브리드 (쿠키 검증용)")
    p.add_argument("--user-id", default=USER_ID,
                   help=f"크리에이터 user_id (기본: {USER_ID})")
    p.add_argument("--cookie", default=COOKIE,
                   help="전체 cookie 문자열 (기본: 코드 상단 COOKIE 값 사용)")
    p.add_argument("--out", default=OUTPUT, help=f"결과 CSV (기본: {OUTPUT})")
    p.add_argument("--max-notes", type=int, default=MAX_NOTES,
                   help=f"테스트 게시물 수 (기본: {MAX_NOTES})")
    p.add_argument("--no-proxy", action="store_true", default=not USE_PROXY,
                   help="⚠️ Oxylabs 끄기 (사용자 IP 노출 — 진단용 1회만)")
    p.add_argument("--headless", action="store_true", default=HEADLESS,
                   help="Playwright 창 숨김")
    return p.parse_args()


# ============ S3 parquet 스키마 19컬럼 ============
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


# ============ 쿠키 정리 + 검증 ============
def sanitize_cookie_str(cookie_str: str) -> str:
    """줄바꿈/탭 제거 (HTTP 헤더 거부 방지)"""
    cleaned = re.sub(r"[\r\n\t]+", "", cookie_str)
    cleaned = re.sub(r" +", " ", cleaned)
    return cleaned.strip()


def parse_cookie_str(cookie_str: str) -> Dict[str, str]:
    out = {}
    for part in cookie_str.split(";"):
        part = part.strip()
        if "=" in part:
            k, _, v = part.partition("=")
            out[k.strip()] = v.strip()
    return out


def validate_required_cookies(cookie_str: str) -> Dict[str, str]:
    cookies = parse_cookie_str(cookie_str)
    missing = [k for k in ["a1", "web_session"] if k not in cookies or not cookies[k]]
    if missing:
        print(f"[ERROR] cookie 필수 항목 누락: {missing}")
        print(f"        받은 키들: {list(cookies.keys())}")
        print("        F12 Network → 요청 클릭 → Request Headers의 Cookie 줄 통째 복사")
        sys.exit(1)
    return cookies


# ============ Playwright sign 함수 (anonymous 브라우저) ============
def make_sign_function(use_proxy: bool, headless: bool):
    """Playwright 기반 sign 함수 — XhsClient 내부에서 호출.

    이 브라우저는 시그니처 생성 전용 (로그인 안 함).
    XhsClient의 cookie 인자에 들어간 a1을 sign 함수가 받아서
    여기 브라우저 cookie에 주입 → window._webmsxyw 호출.

    Returns:
        (sign_func, cleanup_func)
    """
    pw = sync_playwright().start()

    launch_opts = {"headless": headless}
    if use_proxy:
        launch_opts["proxy"] = OXYLABS_PROXY
        print(f"[Browser] Oxylabs 프록시 적용 (cn)")
    else:
        print(f"[Browser] ⚠️ 프록시 OFF — 사용자 IP 노출")

    browser = pw.chromium.launch(**launch_opts)
    context = browser.new_context(
        viewport={"width": 1920, "height": 1080},
        user_agent=(
            "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
            "AppleWebKit/537.36 (KHTML, like Gecko) "
            "Chrome/131.0.0.0 Safari/537.36"
        ),
        locale="zh-CN",
    )

    if os.path.exists(STEALTH_JS):
        try:
            context.add_init_script(path=STEALTH_JS)
            print(f"[Browser] stealth.min.js 적용")
        except Exception as e:
            print(f"[Browser] stealth 적용 실패: {e}")

    page = context.new_page()
    print(f"[Browser] xiaohongshu.com 접속 (시그니처 컨텍스트 로드)")
    page.goto("https://www.xiaohongshu.com", wait_until="domcontentloaded", timeout=30000)
    time.sleep(2)

    current_a1 = {"value": ""}

    def sign(uri, data=None, a1="", web_session=""):
        if a1 and a1 != current_a1["value"]:
            context.add_cookies([{
                "name": "a1", "value": a1,
                "domain": ".xiaohongshu.com", "path": "/",
            }])
            page.reload()
            time.sleep(1)
            current_a1["value"] = a1

        try:
            result = page.evaluate(
                "([url, data]) => window._webmsxyw(url, data)",
                [uri, data]
            )
        except Exception as e:
            print(f"[sign] 실패, 페이지 reload 후 재시도: {e}")
            page.reload()
            time.sleep(2)
            result = page.evaluate(
                "([url, data]) => window._webmsxyw(url, data)",
                [uri, data]
            )
        return {
            "x-s": result["X-s"],
            "x-t": str(result["X-t"]),
        }

    def cleanup():
        try:
            browser.close()
        except Exception:
            pass
        try:
            pw.stop()
        except Exception:
            pass

    return sign, cleanup


def human_sleep(min_s=8.0, max_s=15.0):
    time.sleep(random.uniform(min_s, max_s))


def main():
    args = parse_args()
    os.makedirs(os.path.dirname(args.out) or ".", exist_ok=True)

    print(f"""
============================================================
  샤오홍슈 하이브리드 (쿠키 검증)
============================================================
  user_id   : {args.user_id}
  out       : {args.out}
  max_notes : {args.max_notes}
  proxy     : {'OFF' if args.no_proxy else 'Oxylabs (cn)'}
  headless  : {args.headless}
============================================================
""")

    # Cookie 우선순위:
    #   1) --cookie 명시적 전달 (위 default가 COOKIE 상수)
    #   2) output/xhs_cookie.txt (hybrid_test가 저장)
    #   3) COOKIE 상수
    file_cookie = load_cookie_from_file_if_exists()
    if file_cookie and (not args.cookie or "여기에박을때만" in args.cookie):
        # default 상수는 placeholder라 파일이 있으면 우선
        print(f"[Cookie] 파일에서 자동 로드: {COOKIE_TXT_PATH}")
        args.cookie = file_cookie
    elif file_cookie:
        print(f"[Cookie] 파일 존재하지만 명시 입력 우선 사용")
    else:
        print(f"[Cookie] 파일 없음 → COOKIE 상수 또는 --cookie 인자 사용")
        print(f"         (Tip: hybrid_test로 QR 로그인하면 자동 생성됨)")

    # 쿠키 sanitize + 검증
    raw_len = len(args.cookie)
    args.cookie = sanitize_cookie_str(args.cookie)
    if len(args.cookie) != raw_len:
        print(f"[Cookie] 줄바꿈/공백 정리됨 ({raw_len} → {len(args.cookie)} chars)")

    cookies = validate_required_cookies(args.cookie)
    print(f"[Cookie] 받은 쿠키 {len(cookies)}개: {list(cookies.keys())}")

    # Playwright sign 함수 셋업
    print("\n[Setup] Playwright sign 함수 초기화")
    sign_func, cleanup = make_sign_function(
        use_proxy=not args.no_proxy,
        headless=args.headless,
    )

    try:
        # XhsClient
        proxies = None
        if not args.no_proxy:
            proxy_url = (
                f"http://{OXYLABS_PROXY['username']}:"
                f"{OXYLABS_PROXY['password']}@pr.oxylabs.io:7777"
            )
            proxies = {"http": proxy_url, "https": proxy_url}

        client = XhsClient(
            cookie=args.cookie,    # 전체 cookie 통째로
            sign=sign_func,
            proxies=proxies,
        )

        # ===== Step 1: get_user_notes (xsec_token 자동 동봉) =====
        print(f"\n[1/2] get_user_notes 호출 (user_id={args.user_id})")
        try:
            res = client.get_user_notes(args.user_id, cursor="")
        except IPBlockError as e:
            print(f"\n[ERROR] IP 차단: {e}")
            sys.exit(1)
        except DataFetchError as e:
            print(f"\n[ERROR] DataFetchError: {e}")
            print("       흔한 원인:")
            print("       - 登录已过期: cookie 만료 또는 IP 미스매치 (발급 IP ≠ 사용 IP)")
            print("       - 461 CAPTCHA: 계정/IP 의심 누적")
            print("       - 解析失败: 시그니처 알고리즘 변경")
            sys.exit(1)
        except Exception as e:
            print(f"\n[ERROR] {type(e).__name__}: {e}")
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

        # ===== Step 2: get_note_by_id_from_html (HTML 파싱) =====
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
        cleanup()


if __name__ == "__main__":
    main()
