"""XHS 크롤링 — red_crawler 패턴 + 회사 IP 보호 통합 헬퍼.

구성:
  - 시스템 Chrome (channel="chrome") → fingerprint 자연스러움 (Playwright 번들 X)
  - Oxylabs KR 프록시 → 회사 IP 숨김
  - 자동화 마커 제거 + WebRTC IP 누수 차단
  - 3-way 추출: 페이지 내 fetch API + __INITIAL_STATE__ + DOM a[href] 정규식
  - 진짜 로그인 판별 (loggedIn._value + web_session 길이 200+ + placeholder 登录)
  - sessid + cookie 영속 (만료 시 자동 QR)

사용법:
    # 단일 계정
    python runners/grab_xhs.py 5a8cf39111be10466d285d6b

    # 여러 계정 (콤마)
    python runners/grab_xhs.py 5a8cf39111be10466d285d6b,5842afd75e87e7332ea90fda

    # cookie 만료 → QR 다시 (또는 --reset-session으로 명시)
    python runners/grab_xhs.py <id> --reset-session

출력:
    output/xhs_notes_<user_id>.csv  (19컬럼 schema)
"""
import argparse
import asyncio
import csv
import json
import os
import random
import re
import secrets
import shutil
import sys
from datetime import datetime, timedelta
from urllib.parse import parse_qs, quote, urlparse

import requests
import urllib3
from playwright.async_api import async_playwright

# 이미지 CDN cert chain 가끔 이슈 — verify=False 사용 시 경고 억제
urllib3.disable_warnings(urllib3.exceptions.InsecureRequestWarning)

# .env 로드 (Oxylabs 자격증명 등) — 없어도 silent fail
try:
    from dotenv import load_dotenv
    load_dotenv()
except ImportError:
    pass


# === 회사 IP 보호 정책 (fail-closed) ===
def require_proxy_creds():
    """OXYLABS_USERNAME / OXYLABS_PASSWORD 없으면 즉시 종료. 코드 기본값 X."""
    user = os.getenv("OXYLABS_USERNAME")
    pwd = os.getenv("OXYLABS_PASSWORD")
    if not user or not pwd:
        print("[FAIL] OXYLABS_USERNAME / OXYLABS_PASSWORD 환경변수 필수.")
        print("       .env 또는 셸에서 설정 후 재실행.")
        print("       (회사 IP 보호 정책 — 코드 기본값 사용 X)")
        sys.exit(1)
    return user, pwd


# === 경로 ===
BASE_DIR = os.path.dirname(os.path.abspath(__file__))
USER_DATA_DIR = os.path.abspath(os.path.join(
    BASE_DIR, "..", "crawlers", "MediaCrawler", "browser_data", "xhs_user_data_dir"
))
OUTPUT_DIR = os.path.abspath(os.path.join(BASE_DIR, "..", "output"))
COOKIE_FILE = os.path.join(OUTPUT_DIR, "xhs_logged_in_cookies.json")
SESSION_STATE_FILE = os.path.join(OUTPUT_DIR, "xhs_session_state.json")

# === XHS URL/도메인 상수 ===
# 한국 IP에서 xiaohongshu.com 접속 시 rednote.com으로 자동 redirect.
# 두 도메인 모두 처리해야 cookie/로그인 판별이 일관됨.
XHS_HOME_URL = "https://www.xiaohongshu.com/explore"
XHS_PROFILE_BASE_URL = "https://www.xiaohongshu.com/user/profile/"
XHS_POST_BASE_URL = "https://www.xiaohongshu.com/explore/"
XHS_COOKIE_DOMAINS = ("xiaohongshu", "rednote")


def is_xhs_cookie(cookie):
    """cookie가 xhs 또는 rednote 도메인 소속인지 판별."""
    domain = (cookie.get("domain") or "").lower()
    return any(d in domain for d in XHS_COOKIE_DOMAINS)


POST_COLUMNS = [
    "keyword", "author", "content", "likes", "stars", "comments",
    "images_captured", "post_date", "location", "post_type", "recommendations",
    "shares", "key", "timestamp", "note_title", "note_text", "unique_hash",
    "thumbnail_path", "post_url",
    # 검증/이미지 다운로드용 추가 컬럼 (운영 schema 외)
    "cover_url", "image_urls", "video_url",
]


# === user_id → nickname 매핑 (xhs_config.py 주석에서 추출) ===
# 검색 박스 진입 시 검색어로 사용. xhs WAF가 직접 URL 입력을 차단하므로
# 닉네임으로 검색 → 결과 클릭 흐름이 필수.
def load_xhs_creator_map(config_path=None):
    """xhs_config.py의 'URL  # nickname' 주석에서 {user_id: nickname} 추출.

    형식: "https://www.xiaohongshu.com/user/profile/<uid>",  # <nickname>
    """
    if config_path is None:
        config_path = os.path.abspath(os.path.join(
            os.path.dirname(os.path.abspath(__file__)),
            "..", "crawlers", "mediacrawler-config", "xhs_config.py"
        ))
    if not os.path.isfile(config_path):
        print(f"[creator-map] 파일 없음 — {config_path}")
        return {}

    pattern = re.compile(r'/user/profile/([a-f0-9]+)[^#]*#\s*(.+?)\s*$')
    mapping = {}
    try:
        with open(config_path, "r", encoding="utf-8") as f:
            for line in f:
                m = pattern.search(line.rstrip())
                if m:
                    uid = m.group(1)
                    nickname = m.group(2).strip()
                    if uid and nickname:
                        mapping[uid] = nickname
    except Exception as e:
        print(f"[creator-map] 로드 실패: {e}")
    return mapping


# === 시스템 Chrome 경로 ===
def find_system_chrome():
    candidates = [
        r"C:\Program Files\Google\Chrome\Application\chrome.exe",
        r"C:\Program Files (x86)\Google\Chrome\Application\chrome.exe",
        os.path.expandvars(r"%LOCALAPPDATA%\Google\Chrome\Application\chrome.exe"),
        "/Applications/Google Chrome.app/Contents/MacOS/Google Chrome",
        "/usr/bin/google-chrome",
    ]
    for p in candidates:
        if p and os.path.exists(p):
            return p
    return None


# === sessid + 진단 메타 영속 (xhs_session_state.json 통째 사용) ===
def load_session_state():
    """전체 state dict 반환 (sessid / last_ip / updated_at 등). 첫 실행이면 None."""
    if not os.path.exists(SESSION_STATE_FILE):
        return None
    try:
        with open(SESSION_STATE_FILE, "r", encoding="utf-8") as f:
            return json.load(f)
    except Exception:
        return None


def save_session_state(**updates):
    """기존 state에 updates만 merge. 다른 키 안 건드림."""
    state = load_session_state() or {}
    state.update(updates)
    state["updated_at"] = datetime.now().isoformat()
    try:
        with open(SESSION_STATE_FILE, "w", encoding="utf-8") as f:
            json.dump(state, f, ensure_ascii=False, indent=2)
    except Exception:
        pass


def load_persisted_sessid():
    state = load_session_state()
    return state.get("sessid") if state else None


def save_persisted_sessid(sessid):
    save_session_state(sessid=sessid)


def build_proxy():
    """Oxylabs 자격증명 fail-closed. 코드 기본값 사용 X."""
    user, pwd = require_proxy_creds()
    country = os.getenv("OXYLABS_COUNTRY", "kr")
    if "-cc-" in user:
        username_base = user
    else:
        username_base = f"{user}-cc-{country}"

    sessid = os.getenv("OXYLABS_SESSID")
    mode = "STICKY (env)"
    if not sessid:
        sessid = load_persisted_sessid()
        if sessid:
            mode = "STICKY (persisted)"
        else:
            sessid = f"auto_{secrets.token_hex(4)}"
            mode = "STICKY (new)"
            save_persisted_sessid(sessid)
        os.environ["OXYLABS_SESSID"] = sessid

    sesstime = os.getenv("OXYLABS_SESSTIME", "30")
    username = f"{username_base}-sessid-{sessid}-sesstime-{sesstime}"
    print(f"[proxy] {mode} sessid={sessid} country={country}")
    return {
        "server": f"http://{os.getenv('OXYLABS_HOST', 'pr.oxylabs.io')}:"
                  f"{os.getenv('OXYLABS_PORT', '7777')}",
        "username": username,
        "password": pwd,
    }


# === Proxy IP 검증 (회사 IP 차단 보장) ===
async def verify_proxy_ip(page, ctx, args):
    """xhs 접속 전 IP 검증. 회사 IP면 즉시 종료 (fail-closed). True/False 반환."""
    print(f"\n[ip-check] proxy 출구 IP 검증")
    ip = ""
    try:
        await page.goto("https://api.ipify.org?format=json",
                        wait_until="domcontentloaded", timeout=20000)
        body = await page.evaluate("() => document.body.innerText")
        ip = json.loads(body).get("ip", "")
        print(f"  proxy 출구 IP: {ip}")
    except Exception as e:
        print(f"[FAIL] IP 검증 실패: {e}")
        print(f"       Oxylabs 연결 안 됨 또는 ipify 차단.")
        await shutdown(ctx, args, reason="IP 검증 실패")
        sys.exit(1)

    if not ip:
        print(f"[FAIL] IP 비어있음.")
        await shutdown(ctx, args, reason="IP 비어있음")
        sys.exit(1)

    # 회사 IP 패턴 매칭 (.env의 COMPANY_IP_PREFIX 또는 COMPANY_IP_LIST)
    blocked_prefixes = []
    prefix_env = os.getenv("COMPANY_IP_PREFIX", "").strip()
    if prefix_env:
        blocked_prefixes.extend([p.strip() for p in prefix_env.split(",") if p.strip()])
    list_env = os.getenv("COMPANY_IP_LIST", "").strip()
    if list_env:
        blocked_prefixes.extend([p.strip() for p in list_env.split(",") if p.strip()])

    for prefix in blocked_prefixes:
        if ip == prefix or ip.startswith(prefix.rstrip(".") + "."):
            print(f"[FAIL] 출구 IP({ip})가 회사 IP 패턴({prefix})에 매칭.")
            print(f"       Oxylabs proxy 적용 실패 또는 우회됨.")
            await shutdown(ctx, args, reason="회사 IP 매칭 — 출구 IP 위험")
            sys.exit(1)

    if not blocked_prefixes:
        print(f"  ⚠ COMPANY_IP_PREFIX 미설정 — 매칭 검증 skip. .env에 회사 IP 박으면 자동 차단.")
    else:
        print(f"  ✓ 회사 IP 패턴({len(blocked_prefixes)}개)과 다름")

    # [diag] 이전 실행 IP와 비교 — cookie/IP 미스매치 진단용
    prev_state = load_session_state() or {}
    prev_ip = prev_state.get("last_ip")
    prev_at = prev_state.get("updated_at", "")
    if prev_ip:
        same = prev_ip == ip
        icon = "✓ 같음 (cookie 재사용 OK 기대)" if same else "✗ 다름 (cookie/IP 미스매치 위험)"
        print(f"  [diag] 이전 실행 IP: {prev_ip} ({prev_at}) → {icon}")
    else:
        print(f"  [diag] 이전 실행 IP 기록 없음 (첫 실행)")
    save_session_state(last_ip=ip)
    return ip


# === 진짜 로그인 판별 ===
# 임계값: 게스트 web_session ~38자, 진짜 로그인 = 더 김.
# rednote는 본토와 다른 길이일 수 있어 임계를 50으로 완화 (안전 마진).
WEB_SESSION_MIN_LEN = 50


async def is_real_login(page, ctx):
    """엄격한 로그인 판별 — `loggedIn._value`가 최우선 신호.

    우선순위:
      1. URL `/login` redirect → 비로그인 확정
      2. `__INITIAL_STATE__.user.loggedIn._value` (양쪽 방향 모두 신뢰)
         - True → 로그인 확정
         - False → 비로그인 확정 (★ unread/web_session 잔재 cookie 무시)
         - undefined/없음 → 3번 fallback
      3. cookie fallback (state 못 받을 때만)
         - id_token = anonymous 못 받음 → 단독 True
         - 그 외는 AND 조합으로만 (unread alone은 절대 X)
      4. placeholder `登录` 키워드 negative 최종 검사

    5/13 진단 확인: xhs가 anonymous에게도 unread cookie 발급 → 기존 OR 로직이 거짓
    True 반환 → QR 모달 안 띄움 → 익명 상태로 검색 → 검색 결과 제한 → SKIP.
    이 버그가 운영 자체를 불가능하게 만들어서 엄격화 필수.
    """
    # 1) URL /login redirect = 비로그인 확정
    try:
        if "/login" in page.url and "redirectPath" in page.url:
            return False
    except Exception:
        pass

    # 2) loggedIn._value 직접 확인 — 가장 강한 신호 (양쪽 방향)
    logged_in_value = None
    try:
        result = await page.evaluate("""() => {
            const u = window.__INITIAL_STATE__?.user;
            if (!u) return null;
            const v = u.loggedIn;
            if (v === undefined) return null;
            // Vue ref unwrap: v._value 우선, 없으면 v를 bool
            if (v && typeof v === 'object' && '_value' in v) return v._value;
            return !!v;
        }""")
        if result is True:
            logged_in_value = True
        elif result is False:
            logged_in_value = False
    except Exception:
        pass

    if logged_in_value is True:
        return True
    if logged_in_value is False:
        return False  # ★ 강한 negative — unread/web_session 잔재 무시

    # 3) state 못 받음 → cookie 신호 fallback
    # id_token은 anonymous 발급 못 받음 → 단독 True OK
    # 다른 신호는 잔재 위험 → AND 조합으로만
    try:
        cookies = await ctx.cookies()
        xhs_cookies = [c for c in cookies if is_xhs_cookie(c)]
        has_id_token = False
        has_unread = False
        web_session_val = ""
        for c in xhs_cookies:
            name = c.get("name")
            if name == "id_token":
                has_id_token = True
            elif name == "web_session":
                web_session_val = c.get("value", "")
            elif name == "unread":
                has_unread = True
        if has_id_token:
            return True
        if has_unread and len(web_session_val) >= WEB_SESSION_MIN_LEN:
            return True
    except Exception:
        pass

    # 4) placeholder negative 최종 검사
    try:
        has_login_placeholder = await page.evaluate("""() => {
            const inputs = document.querySelectorAll('input');
            for (const i of inputs) {
                if ((i.placeholder || '').includes('登录')) return true;
            }
            return false;
        }""")
        if has_login_placeholder:
            return False
    except Exception:
        pass

    return False


async def verify_login_stable(page, ctx, timeout=30, stable_count=2, interval=3):
    """QR 로그인 직후 state 안정화 대기.

    `is_real_login`이 transient하게 True ↔ False 깜빡이는 케이스 방어.
    예: QR 스캔 직후 → cookie 발급 OK but rednote redirect 진행 중 →
        __INITIAL_STATE__.user.loggedIn._value가 잠시 False → 몇 초 뒤 True 갱신.

    stable_count회 연속 True 관찰되면 안정화 확정. timeout까지 못 받으면 False 반환.
    """
    start = asyncio.get_event_loop().time()
    consecutive_true = 0
    last_print = 0
    while asyncio.get_event_loop().time() - start < timeout:
        if await is_real_login(page, ctx):
            consecutive_true += 1
            if consecutive_true >= stable_count:
                elapsed = asyncio.get_event_loop().time() - start
                print(f"  ✓ 로그인 안정화 ({elapsed:.1f}초, {stable_count}회 연속 True)")
                return True
        else:
            consecutive_true = 0
        elapsed = int(asyncio.get_event_loop().time() - start)
        if elapsed - last_print >= 10:
            print(f"     안정화 대기... 남은 {timeout-elapsed}초")
            last_print = elapsed
        await asyncio.sleep(interval)
    return False


async def save_cookies_to_file(ctx, label=""):
    """xhs/rednote 도메인 cookie를 COOKIE_FILE에 저장 (다른 PC 이식용).

    자동 로그인이든 QR 로그인이든 둘 다 호출 — user_data_dir과 별개로 명시 영속.
    """
    try:
        cookies = await ctx.cookies()
        xhs_cookies = [c for c in cookies if is_xhs_cookie(c)]
        if not xhs_cookies:
            print(f"  ⚠ {label}저장할 xhs/rednote cookie 없음")
            return
        with open(COOKIE_FILE, "w", encoding="utf-8") as f:
            json.dump(xhs_cookies, f, ensure_ascii=False, indent=2)
        ws_len = next((len(c["value"]) for c in xhs_cookies if c["name"] == "web_session"), 0)
        has_unread = any(c.get("name") == "unread" for c in xhs_cookies)
        has_id_token = any(c.get("name") == "id_token" for c in xhs_cookies)
        domains = sorted(set(c.get("domain", "") for c in xhs_cookies))
        print(f"  💾 {label}cookie 저장 ({len(xhs_cookies)}개, "
              f"web_session={ws_len}자, "
              f"id_token={'O' if has_id_token else 'X'}, "
              f"unread={'O' if has_unread else 'X'}, "
              f"domains={domains})")
    except Exception as e:
        print(f"  ⚠ cookie 저장 실패: {e}")


async def diag_login_signals(page, ctx, label=""):
    """로그인 판별에 쓰는 모든 신호를 한 번에 출력 — 1차/2차 비교 진단용.

    호출 시점:
      - explore 진입 직후 (2차 실행에서 자동 로그인 가능한지 보는 시점)
      - QR 로그인 직후 + 12초 대기 후 (1차 실행에서 cookie 완전체 확인)
    """
    print(f"\n  [diag {label}]")
    print(f"    URL: {page.url}")
    print(f"    /login redirect: {'O ⚠️' if '/login' in page.url else 'X'}")

    try:
        cookies = await ctx.cookies()
        xhs_cookies = [c for c in cookies if is_xhs_cookie(c)]
        ws = next((c for c in xhs_cookies if c.get("name") == "web_session"), None)
        ws_len = len(ws.get("value", "")) if ws else 0
        has_unread = any(c.get("name") == "unread" for c in xhs_cookies)
        has_id_token = any(c.get("name") == "id_token" for c in xhs_cookies)
        domains = sorted(set(c.get("domain", "") for c in xhs_cookies))
        print(f"    cookies: {len(xhs_cookies)}개")
        print(f"      web_session: {ws_len}자 ({'O' if ws_len >= WEB_SESSION_MIN_LEN else 'X 임계 50자 미달'})")
        print(f"      id_token: {'O' if has_id_token else 'X'}")
        print(f"      unread:   {'O' if has_unread else 'X'}  (진짜 로그인 시만 발급)")
        print(f"      domains:  {domains}")
    except Exception as e:
        print(f"    cookies 조회 실패: {e}")

    try:
        state_info = await page.evaluate("""() => {
            const u = window.__INITIAL_STATE__?.user;
            if (!u) return {state: '없음', loggedIn: null};
            const v = u.loggedIn;
            let val;
            if (v === undefined) val = 'loggedIn-필드-없음';
            else if (v && typeof v === 'object' && '_value' in v) val = v._value;
            else val = v;
            return {state: 'O', loggedIn: val};
        }""")
        print(f"    __INITIAL_STATE__.user: {state_info.get('state')}")
        print(f"    __INITIAL_STATE__.user.loggedIn._value: {state_info.get('loggedIn')}")
    except Exception as e:
        print(f"    INITIAL_STATE 조회 실패: {e}")


async def wait_for_qr_login(page, ctx, timeout=300):
    print(f"\n  ★ Chrome 창에서 폰 QR 스캔으로 로그인. 최대 {timeout}초 대기.\n")
    start = asyncio.get_event_loop().time()
    last_print = 0
    while asyncio.get_event_loop().time() - start < timeout:
        if await is_real_login(page, ctx):
            elapsed = int(asyncio.get_event_loop().time() - start)
            print(f"  ✓ 로그인 감지 ({elapsed}초)")
            return True
        elapsed = int(asyncio.get_event_loop().time() - start)
        if elapsed - last_print >= 30:
            print(f"     대기 중... 남은 {timeout-elapsed}초")
            last_print = elapsed
        await asyncio.sleep(2)
    return False


# === 검색 박스 진입 — 사용자 행동 모방으로 xhs WAF 통과 ===
# xhs WAF가 직접 URL 입력(/user/profile/<uid>)을 봇으로 차단함 (첫 진입은 free pass).
# 사람처럼 [홈 → 검색 → 결과 클릭] 흐름으로 진입하면 URL에 xsec_token + xsec_source=pc_search
# 자동으로 박혀서 정상 인증된 진입으로 처리됨.
async def navigate_via_search(page, user_id, nickname):
    """검색 박스 + Enter → 검색 결과 페이지에서 user 카드 href 추출 → goto. 반환: (success, msg).

    흐름 (사용자 수동 검증):
      검색박스 타이핑 → Enter → 검색 결과 페이지 → user 카드의 href 추출
      → 현재 탭에서 page.goto (click은 새 탭 열어서 X)

    핵심: click 대신 href 추출 + goto — xhs link가 target="_blank"라서
    click 시 새 탭 열림 → 원래 탭은 search_result에 머무름 → 추출 실패.
    """
    # 1. 홈 진입
    home_ok = False
    for home_url in ("https://www.rednote.com/explore",
                     "https://www.xiaohongshu.com/explore"):
        try:
            await page.goto(home_url, wait_until="domcontentloaded", timeout=20000)
            home_ok = True
            break
        except Exception:
            continue
    if not home_ok:
        return False, "홈 진입 실패"

    await asyncio.sleep(2)

    # 2. 검색 박스 찾기
    search_selectors = [
        "input[placeholder*='搜索小红书']",
        "input[placeholder*='搜索']",
        "input[type='search']",
        ".search-input input",
        "[class*='search-input'] input",
        "input[class*='search']",
    ]
    search_input = None
    for sel in search_selectors:
        try:
            elem = await page.wait_for_selector(sel, timeout=3000, state="visible")
            if elem:
                search_input = elem
                break
        except Exception:
            continue
    if not search_input:
        return False, "검색 박스 selector 못 찾음"

    # 3. 검색 박스에 닉네임 입력 + Enter
    # 패턴: click(활성화) → keyboard.type(focused element에 직접) → Enter
    # 이유: xhs가 click 시 search overlay 모달 띄움 → 그 안에 NEW input이 focus 받음
    #       search_input.fill()은 underlying header input을 가리키는 stale 참조라 작동 X
    #       page.keyboard.type()은 현재 focused element에 입력 → overlay 모달 input에 들어감
    # click 3단계 fallback — pointer event 차단 환경 대비
    try:
        try:
            await search_input.click(timeout=5000)
        except Exception:
            try:
                await search_input.click(force=True, timeout=5000)
            except Exception:
                # 마지막: JS focus
                await search_input.evaluate("el => el.focus()")
        await asyncio.sleep(0.5)  # overlay 모달 뜰 시간 확보
        # Ctrl+A → Backspace로 기존 텍스트 클리어 (fill 대신)
        await page.keyboard.press("Control+A")
        await asyncio.sleep(0.1)
        await page.keyboard.press("Backspace")
        await asyncio.sleep(0.2)
        # 키보드로 직접 타이핑 — focused element (overlay input)에 들어감
        await page.keyboard.type(nickname, delay=50)
        await asyncio.sleep(0.3)
        await page.keyboard.press("Enter")
    except Exception as e:
        return False, f"검색 입력/Enter 실패: {e}"

    # 4. 검색 결과 페이지 로딩 대기
    await asyncio.sleep(3)

    # 5. 검색 결과에서 user_id 정확 매칭 link 찾기 (동명이인 방지)
    user_link = page.locator(f"a[href*='/user/profile/{user_id}']").first
    try:
        await user_link.wait_for(state="visible", timeout=5000)
    except Exception:
        return False, f"검색 결과에 user_id={user_id[:10]}... 없음"

    # 6. href 추출 → 현재 탭에서 navigate
    # 클릭하면 target="_blank"라 새 탭 열려서 우리 page 변수가 못 따라감 → href + goto
    # href에는 이미 ?xsec_token=...&xsec_source=pc_search 박혀있음
    href = await user_link.get_attribute("href")
    if not href:
        return False, "user_link href 추출 실패"

    # 상대 경로 절대 경로화
    if href.startswith("/"):
        base = "https://www.rednote.com" if "rednote.com" in page.url else "https://www.xiaohongshu.com"
        href = f"{base}{href}"
    elif not href.startswith("http"):
        return False, f"href 형식 이상: {href[:60]}"

    # ★ &tab=note 제거 — 수동 클릭 URL엔 이 파라미터 없음.
    # page.goto에 박힌 채로 보내면 xhs가 "no posts" 뷰 반환하는 케이스 확인됨 (5/14).
    href = re.sub(r'[?&]tab=note(?=&|$)', '', href)
    href = href.replace("?&", "?").rstrip("?&")

    try:
        await page.goto(href, wait_until="domcontentloaded", timeout=20000)
    except Exception as e:
        return False, f"profile URL goto 실패: {e}"

    # 7. URL 전환 검증
    try:
        await page.wait_for_url(f"**/user/profile/{user_id}*", timeout=10000)
    except Exception:
        return False, f"URL 미전환 (현재: {page.url[:100]})"

    return True, f"OK ({nickname})"


# === 노트 데이터 추출 — Listener(페이지 진입 전 등록) + State + DOM ===
# 핵심: listener를 page.goto 전에 등록해야 페이지 자체 user_posted 첫 호출 캡처.
# 이전 회귀 원인: collect_notes 안에서 등록했더니 이미 호출 끝난 후라 캡처 0건.
async def collect_notes(page, user_id, max_pages=3, date_start=None, date_end=None, nickname=None):
    """페이지 진입 전 listener 등록 → user_posted 응답 캡처 + State/DOM 보완.

    date_start 지정 시: listener에서 연속 older 노트 카운트.
    MAX_CONSECUTIVE_OLDER 도달하면 스크롤 lazy-load 조기 중단 (효율).
    필터링 자체는 외부(main)에서 수행 — 여기선 캡처만.
    """
    profile_url = f"{XHS_PROFILE_BASE_URL}{user_id}"

    # 조기 종료 — listener에서 갱신, 스크롤 루프에서 읽음
    start_ts_for_early = None
    if date_start:
        try:
            start_ts_for_early = datetime.strptime(date_start, "%Y-%m-%d").timestamp()
        except ValueError:
            pass
    early_stop = {"consecutive_older": 0, "stop": False}

    # === listener 등록 (page.goto 전!) ===
    captured_notes = []
    captured_meta = {"hosts": set(), "responses": 0, "success": 0}

    async def on_response(resp):
        try:
            url = resp.url
            if "user_posted" not in url:
                return
            if user_id and user_id not in url:
                return
            captured_meta["responses"] += 1
            captured_meta["hosts"].add(urlparse(url).netloc)
            data = await resp.json()
            if data.get("success"):
                captured_meta["success"] += 1
                for n in (data.get("data", {}).get("notes") or []):
                    inter = n.get("interact_info") or {}
                    note_dict = {
                        "noteId": n.get("note_id", ""),
                        "xsec_token": n.get("xsec_token", ""),
                        "title": n.get("display_title", ""),
                        "type": n.get("type", ""),
                        "likes": inter.get("liked_count", ""),
                        "comments": inter.get("comment_count", ""),
                        "stars": inter.get("collected_count", ""),
                        "shares": inter.get("share_count", ""),
                        "cover": (n.get("cover") or {}).get("url_default", ""),
                        "time": n.get("last_update_time", ""),
                    }
                    captured_notes.append(note_dict)

                    # 조기 종료 카운트 — 연속 older 노트 누적 시 스크롤 중단
                    if start_ts_for_early is not None:
                        ts = _note_to_ts(note_dict)
                        if ts is not None:
                            if ts < start_ts_for_early:
                                early_stop["consecutive_older"] += 1
                                if early_stop["consecutive_older"] >= MAX_CONSECUTIVE_OLDER:
                                    early_stop["stop"] = True
                            else:
                                early_stop["consecutive_older"] = 0
        except Exception:
            pass

    page.on("response", on_response)
    print(f"  · listener 등록 (page.goto 전)")

    # === 페이지 진입 (listener 활성 상태) ===
    # 정책: nickname 없거나 검색 실패 시 SKIP. direct fallback 안 함.
    # 이유: /user/profile/{id} 직접 입력은 xhs WAF가 차단 + 로그인 모달 트리거 → 세션 위험 ↑
    if not nickname:
        print(f"  ⏭ SKIP: user_id={user_id[:10]}... — nickname 매핑 없음 (xhs_config.py 확인 필요)")
        try:
            page.remove_listener("response", on_response)
        except Exception:
            pass
        return {"notes": [], "author": "", "skipped": True, "skip_reason": "no_nickname"}

    ok, msg = await navigate_via_search(page, user_id, nickname)
    if not ok:
        print(f"  ⏭ SKIP: {nickname} (user_id={user_id[:10]}...) — 검색 진입 실패: {msg}")
        print(f"     direct fallback은 로그인 모달/세션 리스크라 안 함 — 다음 계정으로")
        try:
            page.remove_listener("response", on_response)
        except Exception:
            pass
        return {"notes": [], "author": nickname, "skipped": True,
                "skip_reason": "search_failed", "skip_msg": msg}

    print(f"  · 검색 진입 OK: {msg}")

    # 첫 user_posted 자체 호출 대기
    await asyncio.sleep(8)
    # 페이지 hydrate 대기 (user.notes에 데이터 박힐 때까지 polling)
    print(f"  · 페이지 hydrate 대기 (최대 20초)")
    nav_errors = 0
    for sec in range(20):
        try:
            has_data = await page.evaluate("""() => {
                const u = window.__INITIAL_STATE__?.user;
                if (!u || !u.notes) return false;
                const unwrap = (v) => (v && typeof v === 'object' && '_value' in v) ? v._value : v;
                const notes = unwrap(u.notes);
                if (!notes) return false;
                let pages = Array.isArray(notes) ? notes : Object.values(notes);
                for (const p of pages) {
                    const items = Array.isArray(p) ? p : (p && typeof p === 'object' ? Object.values(p) : []);
                    for (const n of items) {
                        if (!n || typeof n !== 'object') continue;
                        const nc = n.noteCard || {};
                        if ((nc.noteId || n.id || '').length > 0) return true;
                    }
                }
                return false;
            }""")
            if has_data:
                print(f"    ✓ noteId 데이터 감지 ({sec+1}초)")
                break
        except Exception as e:
            # navigation 등으로 context destroyed — 페이지 안정화 대기 후 재시도
            nav_errors += 1
            if nav_errors == 1:
                print(f"    · navigation 감지 — 안정화 대기 (현재 URL: {page.url})")
            if "/login" in page.url:
                print(f"    ✗ 로그인 페이지로 redirect됨 — cookie/IP 미스매치 추정. --reset-session 필요.")
                return {"notes": [], "author": ""}
        await asyncio.sleep(1)

    # 스크롤로 lazy-load 추가 user_posted 호출 트리거
    # 조기 종료: 연속 MAX_CONSECUTIVE_OLDER개 older 노트 감지 시 중단 (효율)
    print(f"  · 스크롤 lazy-load")
    for _ in range(max_pages * 2):
        if early_stop["stop"]:
            print(f"    ⏱ 조기 종료 — 연속 {MAX_CONSECUTIVE_OLDER}개 older 노트")
            break
        try:
            await page.evaluate("window.scrollBy(0, 1200)")
            await asyncio.sleep(1.5)
        except Exception:
            break
    await asyncio.sleep(3)

    # listener 제거 + 결과 출력
    try:
        page.remove_listener("response", on_response)
    except Exception:
        pass
    print(f"  · listener 캡처: 응답 {captured_meta['responses']}건, "
          f"success {captured_meta['success']}건, "
          f"hosts={captured_meta['hosts']}, 노트 {len(captured_notes)}개")

    # 디버그: 페이지 URL + author + state 첫 노트 (safe)
    try:
        debug = await page.evaluate("""() => {
        const u = window.__INITIAL_STATE__?.user;
        const unwrap = (v) => (v && typeof v === 'object' && '_value' in v) ? v._value : v;
        const upd = unwrap(u?.userPageData) || {};
        const notes = unwrap(u?.notes);
        let firstNote = null;
        if (notes) {
            let pages = Array.isArray(notes) ? notes : Object.values(notes);
            for (const p of pages) {
                const items = Array.isArray(p) ? p : (p && typeof p === 'object' ? Object.values(p) : []);
                if (items.length > 0) {
                    const n = items[0];
                    const nc = n.noteCard || {};
                    firstNote = {
                        n_id: n.id || '',
                        nc_noteId: nc.noteId || '',
                        title: (nc.displayTitle || '').slice(0, 30),
                    };
                    break;
                }
            }
        }
        return {
            url: location.href,
            author: upd.basicInfo?.nickname || '',
            firstNote,
        };
    }""")
    except Exception as e:
        print(f"  · debug evaluate 실패: {e}")
        debug = {"url": page.url, "author": "", "firstNote": None}
    print(f"  · page.url: {debug.get('url')}")
    print(f"  · author: {debug.get('author') or '(빈 값)'}")
    print(f"  · first state note: {debug.get('firstNote')}")

    # 로그인 페이지로 redirect됐으면 종료
    if "/login" in (debug.get("url") or ""):
        print(f"  ✗ 로그인 페이지로 redirect됨 — cookie/IP 미스매치. --reset-session 필요.")
        return {"notes": [], "author": ""}

    # listener에서 받은 데이터를 api_notes 형태로 wrap
    api_notes = {"notes": captured_notes, "pages": captured_meta["success"],
                 "host": ",".join(captured_meta["hosts"]), "errors": []}
    print(f"    API: {len(api_notes['notes'])}개 (listener)")

    # 방법 2: __INITIAL_STATE__ (array + object 둘 다 처리)
    state_notes = await page.evaluate("""() => {
        const out = [];
        const u = window.__INITIAL_STATE__?.user;
        if (!u || !u.notes) return out;
        const unwrap = (v) => (v && typeof v === 'object' && '_value' in v) ? v._value : v;
        const notes = unwrap(u.notes);
        if (!notes) return out;
        let pages = [];
        if (Array.isArray(notes)) {
            pages = notes;
        } else if (typeof notes === 'object') {
            pages = Object.values(notes);
        }
        for (const page of pages) {
            let items = [];
            if (Array.isArray(page)) {
                items = page;
            } else if (page && typeof page === 'object') {
                items = Object.values(page);
            }
            for (const n of items) {
                if (!n || typeof n !== 'object') continue;
                const nc = n.noteCard || {};
                const inter = nc.interactInfo || {};
                out.push({
                    noteId: nc.noteId || n.id || '',
                    xsec_token: n.xsecToken || '',
                    title: nc.displayTitle || '',
                    type: nc.type || '',
                    likes: inter.likedCount || '',
                    comments: inter.commentCount || '',
                    stars: inter.collectedCount || '',
                    shares: inter.shareCount || '',
                    cover: nc.cover?.urlDefault || '',
                });
            }
        }
        return out;
    }""")
    print(f"    State: {len(state_notes)}개")

    # 방법 3: DOM a[href] 정규식
    dom_ids = await page.evaluate("""() => {
        const ids = new Set();
        document.querySelectorAll('a[href]').forEach(a => {
            const m = (a.href || '').match(/\\/(explore|note|discovery\\/item)\\/([a-f0-9]{24})/);
            if (m) ids.add(m[2]);
        });
        return [...ids];
    }""")
    print(f"    DOM href: {len(dom_ids)}개")

    # 합치기 (API 우선, State 보완, DOM 보충)
    merged = []
    seen = set()
    for n in api_notes["notes"]:
        if n["noteId"] and n["noteId"] not in seen:
            seen.add(n["noteId"])
            merged.append(n)
    for n in state_notes:
        if n["noteId"] and n["noteId"] not in seen:
            seen.add(n["noteId"])
            merged.append(n)
    for nid in dom_ids:
        if nid not in seen:
            seen.add(nid)
            merged.append({"noteId": nid, "xsec_token": "", "title": "", "type": "",
                          "likes": "", "comments": "", "stars": "", "shares": "", "cover": ""})

    # 프로필 정보 추출 — basicInfo + interactions (팔로워/팔로잉/총좋아요/bio/avatar 등)
    # s3_upload_xhs_account.py가 기대하는 필드 다 채움
    profile_info = await page.evaluate("""() => {
        const u = window.__INITIAL_STATE__?.user;
        if (!u) return null;
        const unwrap = (v) => (v && typeof v === 'object' && '_value' in v) ? v._value : v;
        const upd = unwrap(u.userPageData);
        if (!upd) return null;
        const basic = upd.basicInfo || {};
        const inter = upd.interactions || [];
        // interactions는 보통 [{type, name, count}, ...] 형태 — type별 count 매핑
        const interMap = {};
        if (Array.isArray(inter)) {
            for (const i of inter) {
                if (i && i.type) interMap[i.type] = i.count;
            }
        }
        const tags = upd.tags || [];
        return {
            nickname: basic.nickname || '',
            desc: basic.desc || '',
            avatar: basic.imageb || basic.images || '',
            gender: basic.gender,  // 0=비공개, 1=남, 2=여 (xhs 관례)
            ip_location: basic.ipLocation || '',
            red_id: basic.redId || '',
            fans: interMap.fans || 0,
            follows: interMap.follows || 0,
            interaction: interMap.interaction || 0,
            tag_list: Array.isArray(tags) ? tags : [],
        };
    }""")

    author = (profile_info or {}).get("nickname", "")
    return {"notes": merged, "author": author, "profile": profile_info or {}}


# === 노트 상세 페이지 진입 — comments/stars/shares/content 채움 ===
# === 노트 상세 추출 — JS hydrate state에서 detail dict 파싱 ===
# 새 탭 / 같은 탭 modal / 같은 탭 navigate 모두 동일 로직으로 추출 가능.
# noteDetailMap은 xhs가 detail 페이지 로드 시 채우는 state.
_NOTE_DETAIL_EXTRACT_JS = """(noteId) => {
    const state = window.__INITIAL_STATE__;
    if (!state?.note?.noteDetailMap) return null;
    const unwrap = (v) => (v && typeof v === 'object' && '_value' in v) ? v._value : v;
    const map = unwrap(state.note.noteDetailMap);
    if (!map) return null;
    let entry = map[noteId];
    if (!entry) {
        const keys = Object.keys(map);
        if (keys.length > 0) entry = map[keys[0]];
    }
    entry = unwrap(entry);
    const note = entry?.note ? unwrap(entry.note) : entry;
    if (!note) return null;
    const inter = note.interactInfo || {};
    const imgList = (note.imageList || []).map(img => {
        return img.urlDefault || img.url || (img.infoList && img.infoList[0] && img.infoList[0].url) || '';
    }).filter(u => u && u.length > 0);
    let videoUrl = '';
    if (note.video) {
        videoUrl = note.video.media?.stream?.h264?.[0]?.masterUrl
            || note.video.url || '';
    }
    return {
        desc: note.desc || '',
        title: note.title || '',
        time: note.time || note.createTime || 0,
        ip_location: note.ipLocation || note.ip_location || '',
        type: note.type || '',
        likes: inter.likedCount || '',
        comments: inter.commentCount || '',
        stars: inter.collectedCount || '',
        shares: inter.shareCount || '',
        image_count: imgList.length,
        image_urls: imgList,
        video_url: videoUrl,
        user_nickname: note.user?.nickname || '',
    };
}"""

_NOTE_DETAIL_HYDRATE_CHECK_JS = """(noteId) => {
    const state = window.__INITIAL_STATE__;
    if (!state?.note?.noteDetailMap) return false;
    const unwrap = (v) => (v && typeof v === 'object' && '_value' in v) ? v._value : v;
    const map = unwrap(state.note.noteDetailMap);
    if (!map) return false;
    let entry = map[noteId];
    if (!entry) {
        const keys = Object.keys(map);
        if (keys.length === 0) return false;
        entry = map[keys[0]];
    }
    entry = unwrap(entry);
    const note = entry?.note ? unwrap(entry.note) : entry;
    return !!(note && (note.desc !== undefined || note.interactInfo));
}"""


async def _extract_note_detail_from(target_page, note_id, hydrate_timeout=15):
    """target_page (새 탭 또는 프로필 페이지)에서 노트 상세 추출.
    noteDetailMap hydrate 폴링 후 evaluate.
    """
    # hydrate 폴링 (state에 detail 데이터 박힐 때까지)
    for _ in range(hydrate_timeout):
        try:
            if await target_page.evaluate(_NOTE_DETAIL_HYDRATE_CHECK_JS, note_id):
                break
        except Exception:
            pass
        await asyncio.sleep(1)

    try:
        detail = await target_page.evaluate(_NOTE_DETAIL_EXTRACT_JS, note_id)
        return detail or {"error": "noteDetailMap 비어있음 (hydrate 실패)"}
    except Exception as e:
        return {"error": f"evaluate 실패: {e}"}


async def collect_note_detail(page, note_id, xsec_token=""):
    """새 탭에서 detail URL 직접 진입 → state 추출 → 탭 닫기 (5/14 패턴).

    이전 시도들의 한계:
      - page.goto(xhs.com/explore/...): xhs WAF가 xiaohongshu.com 도메인 차단
      - 프로필 thumbnail click: lazy-load로 visible 실패 + 모달 처리 복잡
    새 방식:
      - context.new_page() — 같은 context의 새 탭 (cookies/auth 공유)
      - rednote.com/explore/<note_id>?xsec_token=...&xsec_source=pc_user
      - 사용자 F12 검증으로 동작 확인됨

    핵심 정보 출처:
      - note_id, xsec_token: user_posted listener 응답에서 받은 것 (note별 토큰)
      - 프로필 page는 영향 받지 않음 (별도 탭)
    """
    if not xsec_token:
        return {"error": "no xsec_token (DOM fallback note — skip detail)"}

    # 같은 context의 새 탭 — cookies/auth 공유, 프로필 page에 영향 X
    new_page = await page.context.new_page()
    try:
        # xsec_token에 +, /, & 같은 특수문자 섞일 수 있어서 URL encoding 필수.
        # quote(safe='') = 모든 reserved 문자 인코딩 (=, +, /, & 등 다 안전).
        # note_id는 24자 hex라 인코딩 불필요하지만 일관성 위해 quote 적용.
        encoded_note_id = quote(note_id, safe="")
        encoded_token = quote(xsec_token, safe="")
        detail_url = (
            f"https://www.rednote.com/explore/{encoded_note_id}"
            f"?xsec_token={encoded_token}&xsec_source=pc_user"
        )
        try:
            await new_page.goto(detail_url, wait_until="domcontentloaded", timeout=20000)
        except Exception as e:
            return {"error": f"goto: {e}"}

        # /login redirect = 세션 죽음
        if "/login" in new_page.url:
            return {"error": "session lost — redirected to /login"}

        await asyncio.sleep(2)  # hydrate 초기 대기

        # 추출 (_extract_note_detail_from이 hydrate 폴링 + evaluate 처리)
        return await _extract_note_detail_from(new_page, note_id, hydrate_timeout=15)

    finally:
        # 새 탭 close — 메모리/리소스 정리 (반드시)
        try:
            await new_page.close()
        except Exception:
            pass


# === 유틸 ===
# 한자 unicode escape — 파일 encoding 깨져도 문자 보장
# 万 (만) = U+4E07, 亿 (억) = U+4EBF
_WAN = "万"
_YI = "亿"


def format_post_date(time_val):
    """xhs note.time → yyyy-MM-dd. ms 또는 sec timestamp."""
    if not time_val:
        return ""
    try:
        ts = int(time_val)
        if ts > 10**12:  # ms
            ts = ts // 1000
        return datetime.fromtimestamp(ts).strftime("%Y-%m-%d")
    except Exception:
        return ""


def parse_cn_number(text):
    if text is None or text == "":
        return 0
    s = str(text).replace("+", "").replace(",", "").strip()
    try:
        if _WAN in s:
            return int(float(s.replace(_WAN, "")) * 10000)
        if _YI in s:
            return int(float(s.replace(_YI, "")) * 100000000)
        return int(float(s))
    except (ValueError, TypeError):
        return 0


# === 날짜 범위 계산 + 필터 ===
# "지난주" = 가장 최근에 끝난 주(월~일). 오늘이 무슨 요일이든 같은 주차를 가리킴.
MAX_CONSECUTIVE_OLDER = 10  # 스크롤 lazy-load 조기 종료 임계


def previous_week_range():
    """지난주 범위 (월요일 ~ 일요일) yyyy-mm-dd 문자열 튜플."""
    today = datetime.now().date()
    # weekday: Mon=0 ... Sun=6
    days_back = (today.weekday() + 1) % 7
    if days_back == 0:
        days_back = 7  # today is Sunday → 지난 일요일은 7일 전
    last_sunday = today - timedelta(days=days_back)
    last_monday = last_sunday - timedelta(days=6)
    return last_monday.strftime("%Y-%m-%d"), last_sunday.strftime("%Y-%m-%d")


def compute_date_range(args):
    """우선순위: --all > --date-start/end > --week > --days > 기본(지난주).

    반환: (date_start, date_end, label)
        - date_start/end: yyyy-mm-dd 문자열 또는 None(필터 OFF)
        - label: 콘솔용 설명 문자열
    """
    if getattr(args, "all", False):
        return None, None, "전체 (필터 OFF)"

    ds = getattr(args, "date_start", None)
    de = getattr(args, "date_end", None)
    if ds or de:
        if not (ds and de):
            raise ValueError("--date-start와 --date-end는 같이 지정해야 함")
        return ds, de, f"명시 범위: {ds} ~ {de}"

    if getattr(args, "week", None):
        wk = args.week
        try:
            # MMDD (4자) → 올해 년도 사용 / YYMMDD (6자) → 명시된 년도 사용
            if len(wk) == 4:
                mm, dd = int(wk[:2]), int(wk[2:4])
                year = datetime.now().year
            elif len(wk) == 6:
                yy, mm, dd = int(wk[:2]), int(wk[2:4]), int(wk[4:6])
                year = 2000 + yy
            else:
                raise ValueError(f"--week 형식 오류: {wk} (MMDD 4자리 또는 YYMMDD 6자리)")
            start_dt = datetime(year, mm, dd).date()
            end_dt = start_dt + timedelta(days=6)
            return (start_dt.strftime("%Y-%m-%d"),
                    end_dt.strftime("%Y-%m-%d"),
                    f"주차 명시: {wk} → {start_dt} ~ {end_dt}")
        except (ValueError, IndexError):
            raise ValueError(f"--week 형식 오류: {wk} (MMDD 4자리 또는 YYMMDD 6자리)")

    if getattr(args, "days", 0):
        end_dt = datetime.now().date()
        start_dt = end_dt - timedelta(days=args.days - 1)
        return (start_dt.strftime("%Y-%m-%d"),
                end_dt.strftime("%Y-%m-%d"),
                f"최근 {args.days}일: {start_dt} ~ {end_dt}")

    # 기본: 지난주
    start, end = previous_week_range()
    return start, end, f"지난주 자동: {start} ~ {end} (월~일)"


def _note_to_ts(n):
    """노트의 시간 신호를 unix timestamp(float)로 변환. 없거나 파싱 불가 시 None.

    우선순위:
      1. post_date(yyyy-mm-dd) — detail에서 받은 정확한 게시일 (가장 신뢰)
      2. time(last_update_time, listener) — listener API 응답 (xhs가 줄 때만)
      3. note_id 앞 8자리 hex — fallback (★ xhs note_id가 ObjectId 패턴이라 가정)
          예: 69fd8676...000 → 0x69fd8676 = 1778652278 sec = 2026-04-22
          listener time 빈 값일 때 마지막 안전망. 단 정확도는 createTime 대비
          몇 시간/몇 일 오차 가능 (note_id 생성 시점 vs 게시 시점)
    """
    pd = n.get("post_date", "")
    if pd:
        try:
            return datetime.strptime(pd, "%Y-%m-%d").timestamp()
        except ValueError:
            pass

    t = n.get("time")
    if t not in (None, ""):
        try:
            t_int = int(t)
            return t_int / 1000.0 if t_int > 10**12 else float(t_int)
        except (ValueError, TypeError):
            pass

    # note_id hex decode fallback — listener time이 빈 값일 때 필터 살려주는 핵심
    nid = n.get("noteId", "")
    if nid and len(nid) >= 8:
        try:
            ts = float(int(nid[:8], 16))
            # 합리적 범위 검증 (2020~2030 사이만 OK — 잘못된 hex 차단)
            if 1577836800 <= ts <= 1893456000:  # 2020-01-01 ~ 2030-01-01
                return ts
        except ValueError:
            pass

    return None


def filter_notes_by_date(notes, date_start, date_end):
    """note 시간 기준 필터. 시간 없는 노트는 keep (보수적 — 신규 안 빠뜨림).

    반환: (filtered_notes, stats_dict)
        stats_dict: {in_range, out_range, no_time}
    """
    if not (date_start and date_end):
        return notes, {"in_range": len(notes), "out_range": 0, "no_time": 0}

    start_ts = datetime.strptime(date_start, "%Y-%m-%d").timestamp()
    end_ts = datetime.strptime(date_end, "%Y-%m-%d").timestamp() + 86400  # inclusive

    filtered, in_r, out_r, no_t = [], 0, 0, 0
    for n in notes:
        ts = _note_to_ts(n)
        if ts is None:
            filtered.append(n)
            no_t += 1
            continue
        if start_ts <= ts < end_ts:
            filtered.append(n)
            in_r += 1
        else:
            out_r += 1
    return filtered, {"in_range": in_r, "out_range": out_r, "no_time": no_t}


def make_output_base_dir(week=None):
    """red-weekly-YYMMDD/ 경로 생성. week 미지정 시 오늘 날짜.

    uploaders/s3_upload_xhs_post.py가 폴더명에서 \\d{6} 패턴으로 날짜 추출.
    """
    if week:
        week_str = week
    else:
        week_str = datetime.now().strftime("%y%m%d")
    base = os.path.join(OUTPUT_DIR, f"red-weekly-{week_str}")
    os.makedirs(base, exist_ok=True)
    return base


def write_mediacrawler_output(base_dir, user_id, author, notes, profile_info=None):
    """MediaCrawler 호환 포맷으로 저장 — uploaders/s3_upload_xhs_post.py가 그대로 읽음.

    구조:
        <base_dir>/<user_id>/notes.json
        <base_dir>/<user_id>/creator.json
        <base_dir>/<user_id>/<note_id>/                ← 이미지 폴더 (현재 미구현,
                                                          uploader가 없으면 skip)

    notes.json 키 매핑 (uploader가 build_post_parquet에서 기대):
        note_id, user_id, nickname, title, desc, type,
        liked_count, collected_count, comment_count, share_count,
        time (yyyy-mm-dd 문자열), ip_location, image_list (콤마 join), note_url

    creator.json 필드 (s3_upload_xhs_account.py가 기대):
        user_id, nickname, desc, avatar, gender, ip_location, red_id,
        fans, follows, interaction, tag_list
        profile_info=None이면 빈약한 creator.json (user_id+nickname만).
    """
    user_dir = os.path.join(base_dir, user_id)
    os.makedirs(user_dir, exist_ok=True)

    notes_json = []
    for n in notes:
        note_id = n.get("noteId", "")
        if not note_id:
            continue  # 익명 함정 (빈 ID) — uploader에 의미 없음, skip

        # post_date — detail에서 받은 yyyy-mm-dd 우선, 없으면 listener의 last_update_time fallback
        post_date_str = n.get("post_date", "")
        if not post_date_str and n.get("time"):
            post_date_str = format_post_date(n["time"])

        # image_list — detail의 image_urls 우선, 없으면 cover 한 장
        image_urls = n.get("image_urls") or []
        if not image_urls and n.get("cover"):
            image_urls = [n["cover"]]
        image_list_str = ",".join(image_urls)

        # note_url 재구성
        xsec = n.get("xsec_token", "")
        if xsec:
            note_url = f"{XHS_POST_BASE_URL}{note_id}?xsec_token={xsec}&xsec_source=pc_user"
        else:
            note_url = f"{XHS_POST_BASE_URL}{note_id}"

        notes_json.append({
            "note_id": note_id,
            "user_id": user_id,
            "nickname": author,
            "title": n.get("title", ""),
            "desc": n.get("desc", ""),
            "type": n.get("type", ""),
            # 숫자는 원본 형태 (int 또는 "1.3万" 문자열) 그대로 — uploader가 parse_chinese_number 처리
            "liked_count": n.get("likes", ""),
            "collected_count": n.get("stars", ""),
            "comment_count": n.get("comments", ""),
            "share_count": n.get("shares", ""),
            "time": post_date_str,
            "ip_location": n.get("location", ""),
            "image_list": image_list_str,
            "note_url": note_url,
        })

    notes_path = os.path.join(user_dir, "notes.json")
    with open(notes_path, "w", encoding="utf-8") as f:
        json.dump(notes_json, f, ensure_ascii=False, indent=2)

    creator_json = {
        "user_id": user_id,
        "nickname": author,
    }
    # profile_info 있으면 풍부한 creator.json (s3_upload_xhs_account.py 호환)
    if profile_info:
        creator_json.update({
            "desc": profile_info.get("desc", ""),
            "avatar": profile_info.get("avatar", ""),
            "gender": profile_info.get("gender", 0),
            "ip_location": profile_info.get("ip_location", ""),
            "red_id": profile_info.get("red_id", ""),
            "fans": profile_info.get("fans", 0),
            "follows": profile_info.get("follows", 0),
            "interaction": profile_info.get("interaction", 0),
            "tag_list": profile_info.get("tag_list", []),
        })
    creator_path = os.path.join(user_dir, "creator.json")
    with open(creator_path, "w", encoding="utf-8") as f:
        json.dump(creator_json, f, ensure_ascii=False, indent=2)

    return user_dir, len(notes_json)


# === 이미지 다운로드 — Oxylabs 경유 (회사 IP 노출 X) ===
# uploader는 <note_id>/0.jpg, 1.jpg... 같은 숫자 prefix 기대.
# image_urls 순서대로 0, 1, 2... 로 저장.
_IMAGE_HEADERS = {
    "Referer": "https://www.xiaohongshu.com/",
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/120.0.0.0 Safari/537.36"
    ),
}


def _proxy_to_requests_url(proxy):
    """Playwright proxy dict → requests proxies URL 변환."""
    if not proxy:
        return None
    host = proxy["server"].replace("http://", "").replace("https://", "")
    return f"http://{proxy['username']}:{proxy['password']}@{host}"


def _download_image_sync(url, save_path, proxy_url=None, timeout=30):
    """단일 이미지 동기 다운로드. 성공 True / 실패 False.
    성공 조건: HTTP 200 + 컨텐츠 ≥ 1KB.
    """
    try:
        kwargs = {
            "timeout": timeout,
            "headers": _IMAGE_HEADERS,
            "verify": False,
        }
        if proxy_url:
            kwargs["proxies"] = {"http": proxy_url, "https": proxy_url}
        resp = requests.get(url, **kwargs)
        if resp.status_code != 200:
            print(f"    ⚠ img status {resp.status_code}: {url[:60]}")
            return False
        if len(resp.content) < 1024:
            print(f"    ⚠ img too small ({len(resp.content)}B): {url[:60]}")
            return False
        with open(save_path, "wb") as f:
            f.write(resp.content)
        return True
    except Exception as e:
        print(f"    ⚠ img error: {type(e).__name__}: {url[:60]}")
        return False


async def download_note_images(note_dir, image_urls, proxy, concurrency=5, timeout=30):
    """노트 이미지 병렬 다운로드 — 0.jpg, 1.jpg, ... 순서 저장.
    asyncio.Semaphore로 동시성 제한 + asyncio.to_thread로 requests 비차단.
    반환: (saved_count, failed_count)
    """
    if not image_urls:
        return 0, 0
    # 중복 제거 — 순서 보존
    seen, unique = set(), []
    for u in image_urls:
        if u and u not in seen:
            seen.add(u)
            unique.append(u)
    if not unique:
        return 0, 0

    os.makedirs(note_dir, exist_ok=True)
    proxy_url = _proxy_to_requests_url(proxy)
    sem = asyncio.Semaphore(max(1, concurrency))

    async def one(idx, url):
        async with sem:
            base = url.split("?")[0]
            ext = os.path.splitext(base)[-1].lower()
            if ext not in (".jpg", ".jpeg", ".png", ".webp"):
                ext = ".jpg"
            save_path = os.path.join(note_dir, f"{idx}{ext}")
            return await asyncio.to_thread(
                _download_image_sync, url, save_path, proxy_url, timeout
            )

    results = await asyncio.gather(*[one(i, u) for i, u in enumerate(unique)])
    saved = sum(1 for r in results if r)
    return saved, len(unique) - saved


def write_csv(user_id, author, notes):
    timestamp_str = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    rows = []
    for n in notes:
        note_id = n.get("noteId", "")
        post_type = {"normal": "이미지", "video": "동영상"}.get(n.get("type", ""), n.get("type", ""))
        desc = n.get("desc", "")
        cover = n.get("cover") or ""
        image_urls = n.get("image_urls") or []
        # 상세 안 받은 노트는 cover만이라도 image_urls에 넣어줌
        if not image_urls and cover:
            image_urls = [cover]
        rows.append({
            "keyword": user_id,
            "author": author,
            "content": desc,
            "likes": parse_cn_number(n.get("likes", 0)),
            "stars": parse_cn_number(n.get("stars", 0)),
            "comments": parse_cn_number(n.get("comments", 0)),
            "images_captured": n.get("images_captured", 0),
            "post_date": n.get("post_date", ""),
            "location": n.get("location", ""),
            "post_type": post_type,
            "recommendations": 0,
            "shares": parse_cn_number(n.get("shares", 0)),
            "key": f"{author}__{n.get('likes', '')}",
            "timestamp": timestamp_str,
            "note_title": n.get("title", ""),
            "note_text": desc,
            "unique_hash": note_id,
            "thumbnail_path": f"xiaohongshu/profile/image/{user_id}/{note_id}/{note_id}_1.jpg" if note_id else "",
            "post_url": (f"{XHS_POST_BASE_URL}{note_id}?xsec_token={n.get('xsec_token', '')}&xsec_source=pc_user"
                         if note_id and n.get("xsec_token")
                         else (f"{XHS_POST_BASE_URL}{note_id}" if note_id else "")),
            "cover_url": cover,
            "image_urls": "|".join(image_urls),
            "video_url": n.get("video_url", ""),
        })
    csv_path = os.path.join(OUTPUT_DIR, f"xhs_notes_{user_id}.csv")
    with open(csv_path, "w", encoding="utf-8-sig", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=POST_COLUMNS)
        writer.writeheader()
        writer.writerows(rows)
    return csv_path


# === 메인 ===
def parse_args():
    p = argparse.ArgumentParser(
        description="XHS 크롤러. 인자 없이 실행하면 지난주(월~일) 자동 필터.",
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    p.add_argument("user_ids", help="user_id (콤마로 여러 명)")
    p.add_argument("--reset-session", action="store_true", help="user_data_dir + cookie 리셋 (QR 재발급)")
    p.add_argument("--max-pages", type=int, default=3)
    p.add_argument("--detail-count", type=int, default=0,
                   help="노트 상세 진입 개수 (comments/stars/shares/content 채우기). "
                        "기본 0(목록만), -1이면 전체. 검증 시 5 권장")
    p.add_argument("--keep-open", action="store_true",
                   help="에러/완료 후에도 브라우저 안 닫음 (F12 Network 분석용). Ctrl+C 또는 Enter로 종료")

    # === 날짜 필터 (우선순위: --all > --date-start/end > --week > --days > 기본=지난주) ===
    p.add_argument("--all", action="store_true",
                   help="날짜 필터 OFF — 모든 노트 (기존 동작)")
    p.add_argument("--date-start", default=None,
                   help="시작일 yyyy-mm-dd (--date-end와 짝)")
    p.add_argument("--date-end", default=None,
                   help="종료일 yyyy-mm-dd (--date-start와 짝)")
    p.add_argument("--week", default=None,
                   help="주차 시작 월요일. MMDD (예: 0504 = 올해 5/4) 또는 "
                        "YYMMDD (예: 260504 = 2026/5/4). 출력 폴더명에도 사용")
    p.add_argument("--days", type=int, default=0,
                   help="최근 N일 (예: 7 = 오늘 포함 최근 7일)")

    # === 배치 + 지터 (xhs 봇 감지 회피) ===
    p.add_argument("--batch-size", type=int, default=10,
                   help="배치 당 계정 수 (기본 10)")
    p.add_argument("--batch-rest", type=int, default=1800,
                   help="배치 사이 휴식 (초, 기본 1800=30분). 0이면 휴식 없음")
    p.add_argument("--gap-min", type=float, default=4.0,
                   help="계정 간 최소 지터 (초, 기본 4)")
    p.add_argument("--gap-max", type=float, default=7.0,
                   help="계정 간 최대 지터 (초, 기본 7)")

    # === 이미지 다운로드 (기본 ON — S3 적재용) ===
    p.add_argument("--no-images", action="store_true",
                   help="이미지 다운로드 OFF (메타데이터만 빠르게)")
    p.add_argument("--image-concurrency", type=int, default=5,
                   help="이미지 동시 다운로드 수 (기본 5)")
    p.add_argument("--image-timeout", type=int, default=30,
                   help="이미지 1장당 timeout (초, 기본 30)")

    return p.parse_args()


async def keep_browser_open(ctx, reason="검증/디버깅"):
    """브라우저 살려두고 사용자 입력 대기. F12 Network 분석 등 수동 검증용."""
    print(f"\n{'='*60}")
    print(f"  [keep-open] {reason}")
    print(f"  브라우저 살아있음. F12 → Network에서 API host 직접 확인 가능.")
    print(f"  종료: Enter 키 또는 Ctrl+C")
    print(f"{'='*60}")
    try:
        loop = asyncio.get_event_loop()
        await loop.run_in_executor(None, input, "> ")
    except (KeyboardInterrupt, EOFError):
        pass


async def shutdown(ctx, args, reason=""):
    """공통 종료 — keep_open이면 대기, 아니면 즉시 닫음."""
    if args.keep_open:
        await keep_browser_open(ctx, reason=reason or "종료 직전")
    try:
        await ctx.close()
    except Exception:
        pass


async def main():
    args = parse_args()
    os.makedirs(OUTPUT_DIR, exist_ok=True)
    user_ids = [u.strip() for u in args.user_ids.split(",") if u.strip()]

    # 날짜 범위 결정 (--all / --date-start/end / --week / --days / 기본=지난주)
    try:
        date_start, date_end, date_label = compute_date_range(args)
    except ValueError as e:
        print(f"[FAIL] {e}")
        sys.exit(1)
    print(f"[date-filter] {date_label}")

    # 출력 폴더 — 항상 YYMMDD 6자리 (uploader \d{6} 호환)
    # --week MMDD면 올해 prefix, --week YYMMDD면 그대로, 없으면 date_start로
    if args.week:
        if len(args.week) == 4:
            folder_week = datetime.now().strftime("%y") + args.week
        else:
            folder_week = args.week
    elif date_start:
        folder_week = datetime.strptime(date_start, "%Y-%m-%d").strftime("%y%m%d")
    else:
        folder_week = datetime.now().strftime("%y%m%d")
    output_base = make_output_base_dir(folder_week)
    print(f"[output] MediaCrawler 포맷 폴더: {output_base}")
    print(f"[batch ] {args.batch_size}명/배치, 휴식 {args.batch_rest//60}분, "
          f"지터 {args.gap_min:.1f}~{args.gap_max:.1f}초")

    # creator nickname 매핑 로드 (xhs_config.py 주석에서) — 검색 진입용
    creator_map = load_xhs_creator_map()
    print(f"[creator-map] {len(creator_map)}개 닉네임 로드됨 "
          f"({sum(1 for u in user_ids if u in creator_map)}/{len(user_ids)} 매칭)\n")

    # reset 옵션 처리
    if args.reset_session:
        if os.path.exists(USER_DATA_DIR):
            shutil.rmtree(USER_DATA_DIR)
            print(f"[reset] user_data_dir 삭제")
        if os.path.exists(COOKIE_FILE):
            os.remove(COOKIE_FILE)
            print(f"[reset] cookie 파일 삭제")
    os.makedirs(USER_DATA_DIR, exist_ok=True)

    proxy = build_proxy()

    # 시스템 Chrome fail-closed (회사 IP 보호 정책 — 번들 Chromium fallback X)
    chrome_path = find_system_chrome()
    if not chrome_path:
        print(f"[FAIL] 시스템 Chrome 못 찾음. 즉시 종료.")
        print(f"       Playwright 번들 Chromium fallback은 봇 감지에 약함 + 정책 위배.")
        print(f"       Chrome 설치 또는 CHROME_PATH 환경변수로 지정.")
        sys.exit(1)
    chrome_env = os.getenv("CHROME_PATH")
    if chrome_env and os.path.exists(chrome_env):
        chrome_path = chrome_env
    print(f"[chrome] 시스템 Chrome: {chrome_path}")

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
                # 주의: <-loopback> 박으면 Playwright 디버그 포트(127.0.0.1:9222)도
                # 프록시로 가서 차단됨 → 페이지 hydrate 실패. 절대 박지 말 것.
                "--lang=zh-CN",
                "--no-first-run",
                "--no-default-browser-check",
            ],
        }

        ctx = await pw.chromium.launch_persistent_context(**launch_kwargs)
        page = ctx.pages[0] if ctx.pages else await ctx.new_page()

        # 1) IP 검증 (xhs 접속 전, fail-closed)
        await verify_proxy_ip(page, ctx, args)

        # 2) 저장된 cookie 로드
        if os.path.exists(COOKIE_FILE):
            try:
                with open(COOKIE_FILE, "r", encoding="utf-8") as f:
                    saved = json.load(f)
                await ctx.add_cookies(saved)
                print(f"[cookie] 저장본 로드 ({len(saved)}개)")
            except Exception as e:
                print(f"[cookie] 로드 실패: {e}")

        # 메인 진입
        print(f"\n[1] xhs.com 진입")
        try:
            await page.goto(XHS_HOME_URL,
                            wait_until="domcontentloaded", timeout=30000)
        except Exception as e:
            print(f"[ERROR] goto 실패: {e}")
            await shutdown(ctx, args, reason="메인 페이지 진입 실패")
            sys.exit(1)
        await asyncio.sleep(4)

        # [diag] explore 진입 직후 — 2차 실행에서 자동 로그인 가능한지 진단
        await diag_login_signals(page, ctx, label="explore 진입 직후")

        # 로그인 확인 — 자동/QR 두 경로 모두 COOKIE_FILE 저장 (다른 PC 이식 + stale 갱신)
        if await is_real_login(page, ctx):
            print(f"  ✓ 자동 로그인 (user_data_dir cookie 유효)")
            await save_cookies_to_file(ctx, label="(refresh) ")
        else:
            ok = await wait_for_qr_login(page, ctx)
            if not ok:
                print(f"[ERROR] QR 시간 초과")
                await shutdown(ctx, args, reason="QR 로그인 시간 초과")
                sys.exit(1)
            # QR 직후 — state 안정화 대기 (rednote redirect 중간이면 loggedIn 잠시 False 깜빡)
            # 단순 12초 sleep 대신 loggedIn._value 연속 True 관찰로 확실히 안정화 확인
            stable = await verify_login_stable(page, ctx, timeout=30, stable_count=2, interval=3)
            if not stable:
                print(f"  ⚠ 안정화 timeout — state 늦게 갱신될 수 있음. 그래도 진행")
            # cookie 후속 발급 시간 + state hydrate 확보
            await asyncio.sleep(5)
            # [diag] 안정화 후 — 1차 실행에서 cookie 완전체 확인
            await diag_login_signals(page, ctx, label="QR 로그인 안정화 후")
            await save_cookies_to_file(ctx, label="(new login) ")

        # === 배치 + 지터 순회 ===
        # 한 배치 안에서 N명 처리 → 휴식 → 다음 배치. 봇 감지 회피 패턴.
        results = {}
        total = len(user_ids)
        batch_size = max(1, args.batch_size)
        n_batches = (total + batch_size - 1) // batch_size
        session_invalid = False

        for batch_idx in range(n_batches):
            b_start = batch_idx * batch_size
            b_end = min(b_start + batch_size, total)
            batch_uids = user_ids[b_start:b_end]
            now_str = datetime.now().strftime("%H:%M")
            print(f"\n[배치 {batch_idx+1}/{n_batches}] ({now_str}) 계정 {b_start+1}-{b_end} ({len(batch_uids)}명)")

            for inner_idx, uid in enumerate(batch_uids):
                global_idx = b_start + inner_idx + 1

                # 첫 계정 외에는 랜덤 지터로 봇 패턴 회피
                if global_idx > 1:
                    gap = random.uniform(args.gap_min, args.gap_max)
                    await asyncio.sleep(gap)

                nickname = creator_map.get(uid)
                nick_str = f" ({nickname})" if nickname else " (nickname 미등록)"
                print(f"\n  [{global_idx}/{total}] user_id={uid}{nick_str}")
                data = await collect_notes(page, uid, max_pages=args.max_pages,
                                            date_start=date_start, date_end=date_end,
                                            nickname=nickname)

                # SKIP 처리 — nickname 없음 또는 검색 실패 시 collect_notes가 일찍 반환
                if data.get("skipped"):
                    reason = data.get("skip_reason", "unknown")
                    skip_nick = data.get("author") or nickname or "(unknown)"
                    results[uid] = {
                        "skipped": True,
                        "reason": reason,
                        "nickname": skip_nick,
                        "msg": data.get("skip_msg", ""),
                    }
                    print(f"  → 건너뜀: {skip_nick} ({reason})")
                    continue  # 다음 계정으로

                # 진입 후 가벼운 세션 체크 — /login URL redirect만 검사
                # is_real_login 호출 X (state가 transient False일 수 있어 오판 위험)
                # state까지 보는 엄격한 검사는 초기 verify_login_stable에서 1회만 수행.
                if "/login" in page.url:
                    print(f"  ⚠ /login redirect 감지 — 세션 끊김. --reset-session 필요")
                    results[uid] = {"error": "session_invalid"}
                    session_invalid = True
                    break

                # 날짜 필터 적용 (--all 아니면)
                all_notes = data.get("notes") or []
                if date_start and date_end:
                    notes_list, fstats = filter_notes_by_date(all_notes, date_start, date_end)
                    print(f"  → 캡처 {len(all_notes)}개 → 필터 후 {len(notes_list)}개 "
                          f"(in={fstats['in_range']}, out={fstats['out_range']}, no_time={fstats['no_time']})")
                else:
                    notes_list = all_notes

                # === 노트 상세 진입 — comments/stars/shares/content 채움 (필터 통과한 것만) ===
                if args.detail_count != 0 and notes_list:
                    target_count = len(notes_list) if args.detail_count == -1 else min(args.detail_count, len(notes_list))
                    print(f"\n  · 노트 상세 진입 ({target_count}개)")
                    for i, n in enumerate(notes_list[:target_count]):
                        nid = n.get("noteId") or ""
                        if not nid:
                            continue
                        print(f"    [{i+1}/{target_count}] {nid[:10]}... ", end="", flush=True)
                        detail = await collect_note_detail(page, nid, n.get("xsec_token", ""))
                        if detail and "error" not in detail:
                            n["desc"] = detail.get("desc", "")
                            n["post_date"] = format_post_date(detail.get("time"))
                            n["location"] = detail.get("ip_location", "")
                            n["images_captured"] = detail.get("image_count", 0)
                            n["image_urls"] = detail.get("image_urls") or []
                            n["video_url"] = detail.get("video_url", "")
                            if detail.get("likes"):
                                n["likes"] = detail["likes"]
                            if detail.get("comments"):
                                n["comments"] = detail["comments"]
                            if detail.get("stars"):
                                n["stars"] = detail["stars"]
                            if detail.get("shares"):
                                n["shares"] = detail["shares"]
                            img_count = len(n.get("image_urls") or [])
                            print(f"✓ 댓글={detail.get('comments', 0)} 별={detail.get('stars', 0)} 공유={detail.get('shares', 0)} 이미지={img_count}")
                        else:
                            err = (detail or {}).get("error", "unknown")
                            print(f"✗ {err[:60]}")
                        # 새 탭 패턴은 빠름 — 봇 감지 회피 위해 3-7초 랜덤 sleep
                        if i + 1 < target_count:
                            detail_gap = random.uniform(3.0, 7.0)
                            await asyncio.sleep(detail_gap)
                notes = notes_list
                author = data["author"]
                print(f"  → 총 {len(notes)}개 노트 (author: {author})")

                # 진짜 ID 채워졌는지 검증
                empty_ids = sum(1 for n in notes if not n["noteId"])
                if empty_ids == len(notes) and len(notes) > 0:
                    print(f"  ⚠ 노트 ID 전부 빈 값 — 익명 추정 (검증 실패)")
                else:
                    # 1) 이미지 다운로드 (옵션 — S3 업로더가 <note_id>/N.jpg 기대)
                    # CSV/JSON 저장 전에 해야 n["images_captured"]에 실제 성공 수 반영
                    if not args.no_images:
                        user_dir_for_imgs = os.path.join(output_base, uid)
                        total_saved, total_failed, notes_with_img = 0, 0, 0
                        for n in notes:
                            note_id = n.get("noteId")
                            if not note_id:
                                continue
                            img_urls = n.get("image_urls") or []
                            # detail 안 받은 노트 — cover 1장으로 fallback
                            if not img_urls and n.get("cover"):
                                img_urls = [n["cover"]]
                            if not img_urls:
                                continue
                            note_dir = os.path.join(user_dir_for_imgs, note_id)
                            saved, failed = await download_note_images(
                                note_dir, img_urls, proxy,
                                concurrency=args.image_concurrency,
                                timeout=args.image_timeout,
                            )
                            n["images_captured"] = saved
                            total_saved += saved
                            total_failed += failed
                            if saved > 0:
                                notes_with_img += 1
                        print(f"  🖼  이미지: {notes_with_img}/{len(notes)} 노트 → "
                              f"{total_saved}장 성공, {total_failed}장 실패")

                    # 2) 기존 CSV (검증/디버그용 — 19+3컬럼)
                    csv_path = write_csv(uid, author, notes)
                    print(f"  💾 CSV: {csv_path}")
                    # 2) MediaCrawler 호환 포맷 (S3 uploader 입력용) + 프로필 메타데이터
                    user_dir, n_written = write_mediacrawler_output(
                        output_base, uid, author, notes,
                        profile_info=data.get("profile"),
                    )
                    profile_info = data.get("profile") or {}
                    fans = profile_info.get("fans", 0)
                    print(f"  📁 MediaCrawler: {user_dir} ({n_written}개 노트, "
                          f"익명 {empty_ids}개 skip, fans={fans})")
                    # 샘플
                    for r_idx, n in enumerate(notes[:3]):
                        title = (n.get("title") or "")[:30]
                        print(f"    [{r_idx}] {n['noteId'][:10]}... | {title} | likes={n.get('likes', '')}")
                    results[uid] = {"count": len(notes), "csv": csv_path, "dir": user_dir}

            if session_invalid:
                break

            # 배치 휴식 (마지막 배치는 휴식 안 함)
            if batch_idx + 1 < n_batches and args.batch_rest > 0:
                rest_min = args.batch_rest / 60
                done_at = datetime.now().strftime("%H:%M")
                resume_at = (datetime.now() + timedelta(seconds=args.batch_rest)).strftime("%H:%M")
                print(f"\n  ✓ 배치 {batch_idx+1} 완료 ({done_at}) → {rest_min:.0f}분 휴식 (재개 {resume_at})")
                await asyncio.sleep(args.batch_rest)

        # 요약
        print(f"\n{'='*50}\n  요약\n{'='*50}")
        success_count = 0
        skip_count = 0
        error_count = 0
        for uid, r in results.items():
            nick = r.get("nickname") or creator_map.get(uid, "")
            uid_label = f"{uid[:10]}..."
            nick_label = f" ({nick})" if nick else ""
            if r.get("skipped"):
                skip_count += 1
                reason = r.get("reason", "unknown")
                msg = r.get("msg", "")
                detail = f" — {msg}" if msg else ""
                print(f"  {uid_label}{nick_label}: ⏭ SKIP — {reason}{detail}")
            elif "error" in r:
                error_count += 1
                print(f"  {uid_label}{nick_label}: ❌ {r['error']}")
            else:
                success_count += 1
                print(f"  {uid_label}{nick_label}: ✅ {r.get('count', 0)}개")

        print(f"\n  성공 {success_count} / 건너뜀 {skip_count} / 실패 {error_count}")

        # S3 업로드 명령 힌트
        if success_count > 0:
            print(f"\n  S3 업로드 (dry-run 먼저 권장):")
            print(f"    python uploaders/s3_upload_xhs_post.py {output_base} --dry-run")
            print(f"    python uploaders/s3_upload_xhs_post.py {output_base}")

        # 정상 완료 — keep_open이면 대기 (F12 Network 검증 등)
        await shutdown(ctx, args, reason="정상 완료 — F12 Network에서 API host 확인 가능")


if __name__ == "__main__":
    asyncio.run(main())
