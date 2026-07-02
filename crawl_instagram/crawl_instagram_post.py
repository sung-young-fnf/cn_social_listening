"""인스타그램 게시물 크롤러 (테스트) — accounts.txt 계정의 최근 게시물을 CSV로 수집.

세션:
  - Playwright 수동 로그인 1회 → output/ig_cookies.json 에 쿠키 저장 → 이후 자동 재사용.
  - sessionid 쿠키가 있어야 비공개 API(/api/v1/...)가 응답함.

수집(1단계, requests + Oxylabs cc-kr 프록시):
  - user_id   : topsearch(username 정확 매칭) → pk. (web_profile_info 는 429 빡세서 안 씀)
  - 프로필    : /api/v1/users/{user_id}/info/                       (팔로워/게시물 수 등)
  - 게시물    : /api/v1/feed/user/{user_id}/?count=N                (최근 게시물, md 필드 형태)
  - 댓글 본문 수집 X — 게시물별 댓글 수(comment_count)만 컬럼에 포함.

사용법:
    python crawl_instagram/crawl_instagram_post.py --login    # 최초/만료 시 수동 로그인
    python crawl_instagram/crawl_instagram_post.py            # 세션 자동 재사용, 최근 10개 수집
    python crawl_instagram/crawl_instagram_post.py --account your.clothes___ --limit 10
    python crawl_instagram/crawl_instagram_post.py --no-proxy

출력:
    crawl_instagram/output/instagram_posts_YYYYMMDD.csv
"""
import argparse
import asyncio
import csv
import io
import json
import os
import re
import secrets
import sys
import time
from datetime import datetime
from urllib.parse import urlparse

import requests

# UTF-8 콘솔 고정 (한글 깨짐 방지)
sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", line_buffering=True)

try:
    from dotenv import load_dotenv
    ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
    load_dotenv(os.path.join(ROOT, ".env"))
except ImportError:
    pass

HERE = os.path.dirname(os.path.abspath(__file__))
ACCOUNTS_FILE = os.path.join(HERE, "accounts.txt")
OUTPUT_DIR = os.path.join(HERE, "output")
USER_DATA_DIR = os.path.join(OUTPUT_DIR, "ig_user_data_dir")
COOKIE_FILE = os.path.join(OUTPUT_DIR, "ig_cookies.json")
SESSION_STATE_FILE = os.path.join(OUTPUT_DIR, "ig_session_state.json")

IG_APP_ID = "936619743392459"
UA = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
      "(KHTML, like Gecko) Chrome/126.0.0.0 Safari/537.36")

COLUMNS = [
    "PLATFORM", "ID", "SHORTCODE", "CONTENT_URL", "CONTENT_TYPE",
    "CAPTION", "HASHTAGS", "MENTIONS",
    "LIKE_COUNT", "COMMENT_COUNT", "SHARE_COUNT", "CAROUSEL_COUNT",
    "IMAGE_URL", "THUMBNAIL_URL", "TAKEN_AT", "FETCHED_AT",
    "AUTHOR_ID", "AUTHOR_USERNAME", "AUTHOR_DISPLAY_NAME", "AUTHOR_VERIFIED",
    "AUTHOR_AVATAR_URL", "AUTHOR_FOLLOWERS", "AUTHOR_PROFILE_URL", "LANGUAGE",
]

SESSIONID_MIN_LEN = 20  # 게스트/빈 값 거르는 임계


# === Oxylabs 프록시 (crawl_brands 패턴 재사용) ===
def _oxylabs_username():
    user = os.getenv("OXYLABS_USERNAME")
    pwd = os.getenv("OXYLABS_PASSWORD")
    if not user or not pwd:
        print("[FAIL] OXYLABS_USERNAME/PASSWORD 없음 (.env 확인). --no-proxy로 직접 호출 가능.")
        sys.exit(1)
    country = os.getenv("OXYLABS_COUNTRY", "kr")
    base = user if "-cc-" in user else f"{user}-cc-{country}"
    # 로그인 IP와 크롤 IP를 맞추려 sessid 영속
    state = load_session_state() or {}
    sessid = os.getenv("OXYLABS_SESSID") or state.get("sessid")
    if not sessid:
        sessid = f"ig_{secrets.token_hex(4)}"
        save_session_state(sessid=sessid)
    sesstime = os.getenv("OXYLABS_SESSTIME", "30")
    return f"{base}-sessid-{sessid}-sesstime-{sesstime}", pwd, country


def build_proxies_requests():
    username, pwd, country = _oxylabs_username()
    host = os.getenv("OXYLABS_HOST", "pr.oxylabs.io")
    port = os.getenv("OXYLABS_PORT", "7777")
    url = f"http://{username}:{pwd}@{host}:{port}"
    print(f"[proxy:requests] country={country} {host}:{port}")
    return {"http": url, "https": url}


def build_proxy_pw():
    username, pwd, country = _oxylabs_username()
    host = os.getenv("OXYLABS_HOST", "pr.oxylabs.io")
    port = os.getenv("OXYLABS_PORT", "7777")
    print(f"[proxy:playwright] country={country} {host}:{port}")
    return {"server": f"http://{host}:{port}", "username": username, "password": pwd}


# === 세션 상태 영속 ===
def load_session_state():
    if not os.path.exists(SESSION_STATE_FILE):
        return None
    try:
        with open(SESSION_STATE_FILE, "r", encoding="utf-8") as f:
            return json.load(f)
    except Exception:
        return None


def save_session_state(**updates):
    state = load_session_state() or {}
    state.update(updates)
    state["updated_at"] = datetime.now().isoformat()
    try:
        os.makedirs(OUTPUT_DIR, exist_ok=True)
        with open(SESSION_STATE_FILE, "w", encoding="utf-8") as f:
            json.dump(state, f, ensure_ascii=False, indent=2)
    except Exception:
        pass


# === 쿠키 저장/로드 ===
def is_ig_cookie(c):
    return "instagram" in (c.get("domain") or "").lower()


def load_cookies():
    """ig_cookies.json → {name: value} dict. 없으면 {}."""
    if not os.path.exists(COOKIE_FILE):
        return {}
    try:
        with open(COOKIE_FILE, "r", encoding="utf-8") as f:
            arr = json.load(f)
        return {c["name"]: c.get("value", "") for c in arr if c.get("name")}
    except Exception:
        return {}


def has_valid_session(cookie_dict):
    sid = cookie_dict.get("sessionid", "")
    return len(sid) >= SESSIONID_MIN_LEN


def _find_chrome():
    for p in [r"C:\Program Files\Google\Chrome\Application\chrome.exe",
              r"C:\Program Files (x86)\Google\Chrome\Application\chrome.exe"]:
        if os.path.exists(p):
            return p
    return None


# === Playwright 수동 로그인 (헤드풀, 최초 1회) ===
async def playwright_login(use_proxy, timeout=300):
    """헤드풀 창에서 사용자가 직접 로그인 → sessionid 감지되면 쿠키 저장. {name:value} 반환."""
    from playwright.async_api import async_playwright

    os.makedirs(USER_DATA_DIR, exist_ok=True)
    kwargs = {
        "user_data_dir": USER_DATA_DIR,
        "headless": False,
        "viewport": {"width": 1280, "height": 900},
        "locale": "ko-KR",
        "args": ["--disable-blink-features=AutomationControlled",
                 "--force-webrtc-ip-handling-policy=disable_non_proxied_udp",
                 "--no-first-run", "--no-default-browser-check"],
    }
    chrome = _find_chrome()
    if chrome:
        kwargs["executable_path"] = chrome
        kwargs["channel"] = "chrome"
    if use_proxy:
        kwargs["proxy"] = build_proxy_pw()

    async with async_playwright() as pw:
        ctx = await pw.chromium.launch_persistent_context(**kwargs)
        page = ctx.pages[0] if ctx.pages else await ctx.new_page()

        # 출구 IP 진단
        try:
            await page.goto("https://api.ipify.org?format=json",
                            wait_until="domcontentloaded", timeout=20000)
            ip = json.loads(await page.evaluate("() => document.body.innerText")).get("ip", "")
            print(f"[ip-check] proxy 출구 IP: {ip}")
            save_session_state(last_ip=ip)
        except Exception as e:
            print(f"[ip-check] 실패(무시): {str(e)[:50]}")

        await page.goto("https://www.instagram.com/", wait_until="domcontentloaded", timeout=30000)
        await asyncio.sleep(2)

        async def current_cookies():
            return {c["name"]: c.get("value", "")
                    for c in await ctx.cookies() if is_ig_cookie(c)}

        cd = await current_cookies()
        if has_valid_session(cd):
            print("  ✓ 기존 세션 유효 (자동 로그인)")
        else:
            print(f"\n  ★ 창에서 인스타그램에 직접 로그인하세요. 최대 {timeout}초 대기.\n")
            try:
                await page.goto("https://www.instagram.com/accounts/login/",
                                wait_until="domcontentloaded", timeout=30000)
            except Exception:
                pass
            start = asyncio.get_event_loop().time()
            last = 0
            while asyncio.get_event_loop().time() - start < timeout:
                cd = await current_cookies()
                if has_valid_session(cd):
                    print(f"  ✓ 로그인 감지 (sessionid 확보)")
                    break
                el = int(asyncio.get_event_loop().time() - start)
                if el - last >= 20:
                    print(f"     로그인 대기... 남은 {timeout-el}초")
                    last = el
                await asyncio.sleep(2)

        # 전체 인스타 쿠키 저장
        all_ig = [c for c in await ctx.cookies() if is_ig_cookie(c)]
        if all_ig:
            os.makedirs(OUTPUT_DIR, exist_ok=True)
            with open(COOKIE_FILE, "w", encoding="utf-8") as f:
                json.dump(all_ig, f, ensure_ascii=False, indent=2)
            sid_len = len(cd.get("sessionid", ""))
            print(f"  💾 쿠키 저장 ({len(all_ig)}개, sessionid={sid_len}자)")
        await ctx.close()
        return cd


# === requests 세션 ===
def make_session(cookie_dict, proxies):
    s = requests.Session()
    s.headers.update({
        "User-Agent": UA,
        "Accept": "*/*",
        "Accept-Language": "ko-KR,ko;q=0.9,en;q=0.8",
        "X-IG-App-ID": IG_APP_ID,
        "X-Requested-With": "XMLHttpRequest",
        "X-CSRFToken": cookie_dict.get("csrftoken", ""),
        "Referer": "https://www.instagram.com/",
    })
    for k, v in cookie_dict.items():
        s.cookies.set(k, v, domain=".instagram.com")
    if proxies:
        s.proxies.update(proxies)
    return s


def get_json(session, url, params=None, retries=3):
    for attempt in range(retries):
        try:
            r = session.get(url, params=params, timeout=25)
            if r.status_code == 200:
                return r.json()
            print(f"    ! {url} status={r.status_code} (시도 {attempt+1})")
            if r.status_code in (401, 403):
                print(f"      → 세션 만료/차단 가능. --login 으로 재로그인 필요할 수 있음.")
        except Exception as e:
            print(f"    ! {url} 실패: {str(e)[:60]} (시도 {attempt+1})")
        time.sleep(1.5 * (attempt + 1))
    return None


# === 계정 목록 파싱 ===
def parse_account_line(line):
    line = line.strip()
    if not line or line.startswith("#"):
        return None
    if line.startswith("http"):
        parts = [p for p in urlparse(line).path.split("/") if p]
        return parts[0] if parts else None
    return line.split()[0]


def load_accounts(path):
    out, seen = [], set()
    with open(path, "r", encoding="utf-8") as f:
        for line in f:
            u = parse_account_line(line)
            if u and u not in seen:
                seen.add(u)
                out.append(u)
    return out


# === 필드 추출 ===
def parse_hashtags(text):
    return " ".join(re.findall(r"#([0-9A-Za-z_가-힣]+)", text or ""))


def parse_mentions(text):
    return " ".join(re.findall(r"@([0-9A-Za-z_.]+)", text or ""))


CONTENT_TYPE_MAP = {1: "image", 2: "video", 8: "carousel"}


def _candidates_urls(image_versions2):
    cands = (image_versions2 or {}).get("candidates") or []
    return cands


def extract_post_from_feed_item(item, profile, fetched_at):
    """/api/v1/feed/user 응답 item → row dict (md 필드 매핑)."""
    code = item.get("code", "")
    caption = ((item.get("caption") or {}) or {}).get("text", "") if item.get("caption") else ""
    cands = _candidates_urls(item.get("image_versions2"))
    user = item.get("user") or {}
    return {
        "PLATFORM": "instagram",
        "ID": item.get("pk", "") or item.get("id", ""),
        "SHORTCODE": code,
        "CONTENT_URL": f"https://www.instagram.com/p/{code}/" if code else "",
        "CONTENT_TYPE": CONTENT_TYPE_MAP.get(item.get("media_type"), item.get("media_type", "")),
        "CAPTION": caption,
        "HASHTAGS": parse_hashtags(caption),
        "MENTIONS": parse_mentions(caption),
        "LIKE_COUNT": item.get("like_count", ""),
        "COMMENT_COUNT": item.get("comment_count", ""),
        "SHARE_COUNT": item.get("media_repost_count", ""),
        "CAROUSEL_COUNT": item.get("carousel_media_count", ""),
        "IMAGE_URL": cands[0]["url"] if cands else "",
        "THUMBNAIL_URL": cands[-1]["url"] if cands else "",
        "TAKEN_AT": _ts_to_iso(item.get("taken_at")),
        "FETCHED_AT": fetched_at,
        "AUTHOR_ID": user.get("pk", "") or profile.get("id", ""),
        "AUTHOR_USERNAME": user.get("username", "") or profile.get("username", ""),
        "AUTHOR_DISPLAY_NAME": user.get("full_name", "") or profile.get("full_name", ""),
        "AUTHOR_VERIFIED": user.get("is_verified", profile.get("is_verified", "")),
        "AUTHOR_AVATAR_URL": user.get("profile_pic_url", "") or profile.get("profile_pic_url", ""),
        "AUTHOR_FOLLOWERS": profile.get("follower_count", ""),
        "AUTHOR_PROFILE_URL": f"https://www.instagram.com/{profile.get('username','')}/",
        "LANGUAGE": "",
    }


def _ts_to_iso(ts):
    if not ts:
        return ""
    try:
        return datetime.fromtimestamp(int(ts)).isoformat()
    except Exception:
        return ""


# === 프로필 + 게시물 수집 ===
# web_profile_info 는 throttle이 빡세 429가 잘 남 → 쓰지 않는다.
# username → user_id 는 topsearch(정확 매칭)로 해석하고, 프로필은 users/{id}/info/ 로 조회.
def resolve_user_id(session, username):
    """username → user_id 해석 (web_profile_info 429 회피). state에 캐시.

    1순위 topsearch (username 정확 매칭 → pk, 가장 신뢰)
    2순위 프로필 HTML "profile_id" 패턴 (에러 셸에도 들어있음)
    ※ generic "id" 패턴은 엉뚱한 계정을 잡으므로 쓰지 않는다.
    """
    state = load_session_state() or {}
    cache = state.get("user_ids") or {}
    if cache.get(username):
        return cache[username]

    uid = None
    try:
        r = session.get("https://www.instagram.com/web/search/topsearch/",
                        params={"context": "blended", "query": username}, timeout=25)
        if r.status_code == 200:
            for u in (r.json().get("users") or []):
                uu = u.get("user") or {}
                if uu.get("username", "").lower() == username.lower() and uu.get("pk"):
                    uid = str(uu["pk"])
                    break
        else:
            print(f"    ! topsearch status={r.status_code}")
    except Exception as e:
        print(f"    ! topsearch 실패: {str(e)[:50]}")

    if not uid:
        try:
            r = session.get(f"https://www.instagram.com/{username}/", timeout=25)
            m = re.search(r'"profile_id":"(\d+)"', r.text)
            if m:
                uid = m.group(1)
        except Exception as e:
            print(f"    ! 프로필 HTML fallback 실패: {str(e)[:50]}")

    if uid:
        cache[username] = uid
        save_session_state(user_ids=cache)
    else:
        print(f"    ! user_id 해석 실패 (topsearch/HTML 모두)")
    return uid


def fetch_profile(session, username):
    """user_id 해석 후 users/{id}/info/ 로 프로필 조회. 실패 시 None."""
    uid = resolve_user_id(session, username)
    if not uid:
        return None
    data = get_json(session, f"https://www.instagram.com/api/v1/users/{uid}/info/")
    user = (data or {}).get("user") or {}
    if not user:
        return {"id": uid, "username": username, "full_name": "", "is_verified": "",
                "is_private": "", "biography": "", "profile_pic_url": "",
                "follower_count": "", "following_count": "", "media_count": ""}
    return {
        "id": uid,
        "username": user.get("username", username),
        "full_name": user.get("full_name", ""),
        "is_verified": user.get("is_verified", ""),
        "is_private": user.get("is_private", ""),
        "biography": user.get("biography", ""),
        "profile_pic_url": user.get("profile_pic_url_hd") or user.get("profile_pic_url", ""),
        "follower_count": user.get("follower_count", ""),
        "following_count": user.get("following_count", ""),
        "media_count": user.get("media_count", ""),
    }


def fetch_feed(session, user_id, count):
    """/api/v1/feed/user/{id}/ → item 리스트. 실패 시 None."""
    data = get_json(
        session, f"https://www.instagram.com/api/v1/feed/user/{user_id}/",
        params={"count": count})
    if not data:
        return None
    return data.get("items") or []


# === CSV 저장 (finally용, 파일잠김 fallback) ===
def save_csv(rows, now):
    os.makedirs(OUTPUT_DIR, exist_ok=True)
    out_path = os.path.join(OUTPUT_DIR, f"instagram_posts_{now.strftime('%Y%m%d')}.csv")

    def _write(path):
        # 한국어 환경 Excel이 UTF-8 BOM을 무시하고 cp949로 읽어 한글이 깨지는 문제 →
        # UTF-16(BOM) + 탭 구분자로 저장하면 더블클릭 시 Excel이 자동으로 올바르게 읽음.
        # (이모지/일본어 섞여 cp949 저장은 불가) pandas 등은 sep='\t', encoding='utf-16'로 읽기.
        with open(path, "w", encoding="utf-16", newline="") as f:
            w = csv.DictWriter(f, fieldnames=COLUMNS, delimiter="\t")
            w.writeheader()
            w.writerows(rows)

    try:
        _write(out_path)
    except PermissionError:
        out_path = os.path.join(OUTPUT_DIR, f"instagram_posts_{now.strftime('%Y%m%d_%H%M%S')}.csv")
        print(f"  ⚠ 기존 CSV 잠김 — 새 파일로 저장: {os.path.basename(out_path)}")
        _write(out_path)
    return out_path


def rotate_proxy_ip():
    """프록시 IP 세션(sessid) 새로 발급 → 매 실행 다른 출구 IP. 인스타 로그인 쿠키는 무관."""
    new = f"ig_{secrets.token_hex(4)}"
    save_session_state(sessid=new, last_ip=None)
    print(f"[ip] 프록시 IP 세션 초기화 → sessid={new} (새 출구 IP)")
    return new


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--account", help="단일 username (accounts.txt 무시)")
    ap.add_argument("--limit", type=int, default=10, help="계정당 최근 게시물 수 (기본 10)")
    ap.add_argument("--login", action="store_true", help="강제 수동 로그인 (세션 갱신)")
    ap.add_argument("--no-proxy", action="store_true")
    ap.add_argument("--keep-ip", action="store_true",
                    help="프록시 IP 세션 유지 (기본은 매 실행 새 IP로 초기화)")
    ap.add_argument("--delay", type=float, default=2.0, help="계정 간 딜레이(초)")
    args = ap.parse_args()

    if args.account:
        accounts = [args.account.strip()]
    else:
        if not os.path.exists(ACCOUNTS_FILE):
            print(f"[FAIL] {ACCOUNTS_FILE} 없음")
            sys.exit(1)
        accounts = load_accounts(ACCOUNTS_FILE)
    if not accounts:
        print("[FAIL] 대상 계정 없음 (accounts.txt 확인)")
        sys.exit(1)

    use_proxy = not args.no_proxy

    # 프록시 IP 세션 초기화 (기본) — 매 실행 새 IP로 429 누적 회피. --keep-ip로 끄기.
    if use_proxy and not args.keep_ip:
        rotate_proxy_ip()

    # === 0단계: 세션 확보 ===
    cookies = load_cookies()
    if args.login or not has_valid_session(cookies):
        if args.login:
            print("[세션] --login → 수동 로그인 진행")
        else:
            print("[세션] 유효한 sessionid 없음 → 수동 로그인 진행 (최초 1회)")
        cookies = asyncio.run(playwright_login(use_proxy))
    else:
        print(f"[세션] 저장된 sessionid 재사용 (length={len(cookies.get('sessionid',''))})")

    if not has_valid_session(cookies):
        print("[FAIL] sessionid 확보 실패 — 로그인이 완료되지 않았습니다.")
        sys.exit(1)

    proxies = build_proxies_requests() if use_proxy else None
    session = make_session(cookies, proxies)

    now = datetime.now()
    fetched_at = now.isoformat()
    all_rows = []
    try:
        for username in accounts:
            print(f"\n=== [{username}] ===")
            profile = fetch_profile(session, username)
            if not profile:
                print(f"  ⚠ 프로필 조회 실패 — username/세션 확인 필요")
                continue
            print(f"  프로필 OK: id={profile['id']} 팔로워={profile['follower_count']} "
                  f"게시물={profile['media_count']} 비공개={profile['is_private']}")

            rows = []
            items = fetch_feed(session, profile["id"], max(args.limit, 12))
            if items:
                for it in items[:args.limit]:
                    rows.append(extract_post_from_feed_item(it, profile, fetched_at))
                print(f"  게시물 {len(rows)}개 (feed API)")
            else:
                print(f"  ⚠ 게시물 0개 (비공개 계정이거나 API 제한)")
            all_rows.extend(rows)
            time.sleep(args.delay)
    except KeyboardInterrupt:
        print("\n[중단] Ctrl+C — 여기까지 수집분 저장")
    except Exception as e:
        print(f"\n[오류] {type(e).__name__}: {str(e)[:120]} — 수집분 저장")
    finally:
        if all_rows:
            out_path = save_csv(all_rows, now)
            print(f"\n[완료] {len(all_rows)}개 행 → {out_path}")
            liked = sum(1 for r in all_rows if r["LIKE_COUNT"] != "")
            cmtd = sum(1 for r in all_rows if r["COMMENT_COUNT"] != "")
            print(f"  채움률 — LIKE {liked}, COMMENT_COUNT {cmtd} / {len(all_rows)}")
        else:
            print("\n[결과] 수집된 행 없음")


if __name__ == "__main__":
    main()
