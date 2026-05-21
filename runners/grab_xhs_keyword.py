"""XHS 키워드 검색 크롤러 — search/notes listener 캡처 + UI 정렬(最热 + 一周内).

CREATOR 모드(grab_xhs.py)와 다른 점:
  - 검색박스에 user_id가 아닌 *키워드* 입력
  - 검색 결과 페이지에 머묾 (프로필 진입 X)
  - listener URL: user_posted 대신 /search/notes
  - 응답 구조: items[].note_card.* (note_card wrapping)
  - 정렬: UI 클릭 (排序: 最热, 时间: 一周内)
  - detail-count default = -1 (정렬 一周内 결과 모두 detail)

공통 부분은 grab_xhs.py에서 import.

사용법 (검증용):
    python runners/grab_xhs_keyword.py 鞋
    python runners/grab_xhs_keyword.py 鞋,包,运动鞋 --reset-session
    python runners/grab_xhs_keyword.py 鞋 --reset-session --keep-open
    python runners/grab_xhs_keyword.py 鞋 --reset-session --detail-count 0  # 목록만

출력:
    output/red-keyword-YYMMDD/<keyword>/
      ├── notes.json
      └── <note_id>/0.jpg, 1.jpg, ..., video.mp4
"""
import argparse
import asyncio
import json
import os
import random
import re
import shutil
import sys
from datetime import datetime, timedelta
from urllib.parse import quote

from playwright.async_api import async_playwright

try:
    from dotenv import load_dotenv
    load_dotenv()
except ImportError:
    pass

# grab_xhs의 헬퍼 재사용 — 같은 디렉토리
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from grab_xhs import (  # noqa: E402
    BASE_DIR, USER_DATA_DIR, OUTPUT_DIR, COOKIE_FILE,
    XHS_POST_BASE_URL, is_xhs_cookie,
    require_proxy_creds, build_proxy, find_system_chrome,
    verify_proxy_ip, is_real_login, verify_login_stable,
    wait_for_qr_login, save_cookies_to_file, diag_login_signals,
    _input_search_keyword,
    collect_note_detail, _download_via_page,
    download_note_images, download_note_video,
    format_post_date, parse_cn_number,
    shutdown, keep_browser_open,
)


# === 검색 결과 페이지 진입 + 정렬 ===
# 사용자 확정 정렬 정책 (2026-05-21 갱신):
#   排序依据: 最新 (최신순)
#   发布时间: 一周内 (일주일 안)
# HTML 구조 (확정):
#   선택 전: <div class="filter"><span>筛选</span><svg/><div class="filter-panel">...</div></div>
#   선택 후: <div class="filter active"><span>已筛选</span><svg class="...active"/>...</div>
# 동작: trigger hover → 패널 자동 열림. mouse가 영역 벗어나면 닫힘.
SORT_LABEL_NEW = "最新"
SORT_LABEL_WEEK = "一周内"


async def navigate_via_keyword(page, keyword):
    """검색박스 keyboard.type → 결과 페이지 도달 → 정렬 UI hover + click(最新 + 一周内).
    반환: (success, msg).
    """
    # 1-4단계: 홈 → 검색박스 → keyboard.type → Enter (creator와 공통)
    ok, msg = await _input_search_keyword(page, keyword)
    if not ok:
        return False, msg

    # 5. 검색 결과 페이지 URL 검증
    try:
        await page.wait_for_url("**/search_result*", timeout=10000)
    except Exception:
        return False, f"검색 결과 URL 미전환 (현재: {page.url[:100]})"

    # 6. 정렬 UI 적용 — 排序(最新) + 时间(一周内)
    await asyncio.sleep(1)
    sort_applied = await _apply_sort_filter(page)
    if sort_applied:
        # 정렬 적용 후 응답 새로고침 대기
        await asyncio.sleep(2)
        print(f"  · 정렬 적용: {SORT_LABEL_NEW} + {SORT_LABEL_WEEK}")
    else:
        print(f"  ⚠ 정렬 UI 적용 실패 — 기본 정렬(综合)로 진행")

    return True, "OK"


async def _apply_sort_filter(page):
    """筛选 트리거 hover → 패널 자동 펼침 → 最新 click → '已筛选' 검증.

    HTML 구조 (스크린샷 확정):
      <div class="filter">
          <span>筛选</span>
          <svg class="... [rotate]"/>     ← hover 시 rotate 추가
          <div class="filter-panel">...</div>   ← 패널이 trigger의 자식
      </div>

    핵심: click이 아닌 **hover**로 패널이 열림.
    mouse가 filter 영역 (트리거 또는 패널) 안에 있어야 패널 유지.
    """
    # 1. 筛选 트리거 — div.filter 중 텍스트 "筛选" 또는 "已筛选" 포함
    trigger = page.locator("div.filter").filter(has_text="筛选").first
    try:
        await trigger.wait_for(state="visible", timeout=5000)
    except Exception:
        try:
            trigger = page.locator("div.filter").filter(has_text="已筛选").first
            await trigger.wait_for(state="visible", timeout=2000)
            print(f"    · 이미 '已筛选' 상태 — 기존 필터 유지 추정")
        except Exception:
            print(f"    ⚠ 筛选 트리거 못 찾음")
            return False

    # 2. 트리거 hover — 패널 자동 열림 (click 아님)
    hovered = False
    for try_method in (
        lambda: trigger.hover(timeout=3000),
        lambda: trigger.hover(force=True, timeout=3000),
    ):
        try:
            await try_method()
            hovered = True
            break
        except Exception:
            continue
    if not hovered:
        print(f"    ⚠ 筛选 트리거 hover 실패")
        return False

    # 3. 패널 열림 대기 + 검증 — .filter-panel이 visible이면 OK
    await asyncio.sleep(1)
    panel = trigger.locator(".filter-panel").first
    panel_open = False
    try:
        await panel.wait_for(state="visible", timeout=3000)
        panel_open = True
    except Exception:
        # fallback: '排序依据' 헤더 매칭
        try:
            cnt = await page.locator('text="排序依据"').count()
            panel_open = cnt > 0
        except Exception:
            pass
    if not panel_open:
        print(f"    ⚠ 패널 열기 실패 — .filter-panel/排序依据 못 찾음")
        return False
    print(f"    · 패널 열림 OK")

    # 4. 패널 안 옵션 click — 排序(最新) + 时间(一周内)
    # panel 한 번 열고 두 옵션 차례로 mouse click. mouse는 매 click 후 그 옵션 위치
    # (= trigger 자손 영역) 에 머무름 → panel 자동 유지. trigger.hover 재진입 불필요.
    async def _click_option_once(label):
        """패널 열린 상태에서 label 옵션 mouse click. 성공 True / 실패 False."""
        option = panel.get_by_text(label, exact=True).first
        try:
            await option.wait_for(state="visible", timeout=3000)
        except Exception:
            return False
        box = await option.bounding_box()
        if not box or box["width"] < 1 or box["height"] < 1:
            return False
        cx = box["x"] + box["width"] / 2
        cy = box["y"] + box["height"] / 2
        try:
            await page.mouse.move(cx, cy)
            await asyncio.sleep(0.15)
            await page.mouse.down()
            await asyncio.sleep(0.08)
            await page.mouse.up()
            return True
        except Exception:
            return False

    async def _verify_active(label):
        """label 옵션 element 또는 ancestor에 active class 있는지 확인."""
        try:
            option = panel.get_by_text(label, exact=True).first
            cls = await option.evaluate(
                "el => { let cur = el; for (let i = 0; i < 3; i++) "
                "{ if (cur.className && cur.className.includes('active')) return cur.className; "
                "cur = cur.parentElement; if (!cur) break; } return ''; }"
            )
            return cls and "active" in cls
        except Exception:
            return False

    # 두 옵션 차례로 click (panel은 유지된 상태)
    if not await _click_option_once(SORT_LABEL_NEW):
        print(f"    ⚠ '{SORT_LABEL_NEW}' click 실패")
        return False
    await asyncio.sleep(0.6)  # panel 안정화 + active class 적용 대기

    if not await _click_option_once(SORT_LABEL_WEEK):
        print(f"    ⚠ '{SORT_LABEL_WEEK}' click 실패")
        return False
    await asyncio.sleep(1)

    # 5. 검증 — 두 옵션 모두 active 인지
    new_active = await _verify_active(SORT_LABEL_NEW)
    week_active = await _verify_active(SORT_LABEL_WEEK)

    if new_active and week_active:
        print(f"    ✓ 정렬 완료 — '{SORT_LABEL_NEW}' + '{SORT_LABEL_WEEK}' 둘 다 active")
        return True
    if new_active and not week_active:
        print(f"    ⚠ '{SORT_LABEL_WEEK}' 미적용 — '{SORT_LABEL_NEW}'만 active")
        return False
    if not new_active and week_active:
        print(f"    ⚠ '{SORT_LABEL_NEW}' 미적용 — '{SORT_LABEL_WEEK}'만 active")
        return False
    print(f"    ⚠ 둘 다 미적용 — click 효과 없음")
    return False


# === listener 캡처 + items 매핑 ===
def _map_search_item(item):
    """items[]의 한 항목을 우리 표준 dict로 매핑."""
    note_card = item.get("note_card") or {}
    user = note_card.get("user") or {}
    inter = note_card.get("interact_info") or {}
    cover = note_card.get("cover") or {}

    # xsec_token 우선순위: items[].xsec_token > note_card.xsec_token > note_card.user.xsec_token
    xsec_token = (
        item.get("xsec_token")
        or note_card.get("xsec_token")
        or user.get("xsec_token", "")
    )

    return {
        "noteId": item.get("id", ""),
        "xsec_token": xsec_token,
        "title": note_card.get("display_title", ""),
        "type": note_card.get("type", ""),
        "likes": inter.get("liked_count", ""),
        "comments": inter.get("comment_count", ""),
        "stars": inter.get("collected_count", ""),
        "shares": inter.get("shared_count", ""),  # ★ search는 shared_count (d 붙음)
        "cover": cover.get("url_default", ""),
        "time": "",  # search 응답에 작성 시간 없음 — note_id hex로 fallback
        "user_id": user.get("user_id", ""),
        "user_nickname": user.get("nickname", "") or user.get("nick_name", ""),
        "user_avatar": user.get("avatar", ""),
    }


async def collect_notes_by_keyword(page, keyword, max_scrolls=10):
    """검색 결과 listener 캡처 + 스크롤 lazy-load. 반환: notes list."""
    captured_notes = []
    seen_ids = set()
    captured_meta = {"responses": 0, "success": 0, "has_more": False}

    async def on_response(resp):
        try:
            url = resp.url
            if "/search/notes" not in url:
                return
            captured_meta["responses"] += 1
            try:
                data = await resp.json()
            except Exception:
                return
            if not data.get("success"):
                return
            captured_meta["success"] += 1
            payload = data.get("data") or {}
            captured_meta["has_more"] = bool(payload.get("has_more"))
            for item in (payload.get("items") or []):
                if (item.get("model_type") or "") != "note":
                    continue
                mapped = _map_search_item(item)
                nid = mapped["noteId"]
                if nid and nid not in seen_ids:
                    seen_ids.add(nid)
                    captured_notes.append(mapped)
        except Exception:
            pass

    page.on("response", on_response)
    print(f"  · listener 등록 (/search/notes)")

    # 검색 + 정렬 진입
    ok, msg = await navigate_via_keyword(page, keyword)
    if not ok:
        page.remove_listener("response", on_response)
        return [], {"error": msg}

    # 첫 응답 대기
    await asyncio.sleep(4)

    # 스크롤 lazy-load — has_more 신호 활용
    print(f"  · 스크롤 lazy-load (최대 {max_scrolls}회)")
    last_count = 0
    stagnant = 0
    for i in range(max_scrolls):
        try:
            await page.evaluate("window.scrollBy(0, 1500)")
            await asyncio.sleep(2)
        except Exception:
            break
        # 변동 없으면 has_more 검증 + 조기 종료
        if len(captured_notes) == last_count:
            stagnant += 1
            if stagnant >= 2 and not captured_meta["has_more"]:
                print(f"    · 스크롤 {i+1}회 후 정체 + has_more=False → 조기 종료")
                break
        else:
            stagnant = 0
            last_count = len(captured_notes)

    await asyncio.sleep(2)
    page.remove_listener("response", on_response)
    print(f"  · listener 캡처: 응답 {captured_meta['responses']}건, "
          f"success {captured_meta['success']}건, 노트 {len(captured_notes)}개")
    return captured_notes, captured_meta


# === 출력 ===
def make_keyword_output_dir(week=None):
    week_str = week or datetime.now().strftime("%y%m%d")
    base = os.path.join(OUTPUT_DIR, f"red-keyword-{week_str}")
    os.makedirs(base, exist_ok=True)
    return base


def write_keyword_output(base_dir, keyword, notes):
    """red-keyword-YYMMDD/<keyword>/notes.json 저장.

    파일 시스템 안전 위해 keyword를 dir-safe로 변환 (특수문자 제거).
    """
    safe_keyword = re.sub(r'[\\/:*?"<>|]', "_", keyword).strip() or "unknown"
    keyword_dir = os.path.join(base_dir, safe_keyword)
    os.makedirs(keyword_dir, exist_ok=True)

    notes_json = []
    for n in notes:
        note_id = n.get("noteId", "")
        if not note_id:
            continue
        xsec = n.get("xsec_token", "")
        if xsec:
            note_url = f"{XHS_POST_BASE_URL}{note_id}?xsec_token={quote(xsec, safe='')}&xsec_source=pc_search"
        else:
            note_url = f"{XHS_POST_BASE_URL}{note_id}"

        post_date = n.get("post_date", "")
        if not post_date and n.get("time"):
            post_date = format_post_date(n["time"])

        image_urls = n.get("image_urls") or []
        if not image_urls and n.get("cover"):
            image_urls = [n["cover"]]
        image_list_str = ",".join(image_urls)

        notes_json.append({
            "note_id": note_id,
            "keyword": keyword,
            "user_id": n.get("user_id", ""),
            "nickname": n.get("user_nickname", ""),
            "title": n.get("title", ""),
            "desc": n.get("desc", ""),
            "type": n.get("type", ""),
            "liked_count": n.get("likes", ""),
            "collected_count": n.get("stars", ""),
            "comment_count": n.get("comments", ""),
            "share_count": n.get("shares", ""),
            "time": post_date,
            "ip_location": n.get("location", ""),
            "image_list": image_list_str,
            "video_url": n.get("video_url", ""),
            "note_url": note_url,
        })

    notes_path = os.path.join(keyword_dir, "notes.json")
    with open(notes_path, "w", encoding="utf-8") as f:
        json.dump(notes_json, f, ensure_ascii=False, indent=2)
    return keyword_dir, len(notes_json)


# === 메인 ===
def load_xhs_all_keywords():
    """xhs_config.py에서 SEARCH_KEYWORDS + BRAND_KEYWORDS 합쳐서 반환.
    중복 제거 + 순서 보존. 외부 적재 시스템이 type 구분 없이 한 폴더에 박는 구조라
    같은 흐름으로 통합 처리.
    """
    config_path = os.path.abspath(os.path.join(
        os.path.dirname(os.path.abspath(__file__)),
        "..", "crawlers", "mediacrawler-config", "xhs_config.py"
    ))
    if not os.path.isfile(config_path):
        print(f"[FAIL] xhs_config.py 못 찾음 — {config_path}")
        return []

    # xhs_config 직접 import (mediacrawler-config 폴더 sys.path 추가)
    import importlib.util
    spec = importlib.util.spec_from_file_location("_xhs_config", config_path)
    mod = importlib.util.module_from_spec(spec)
    try:
        spec.loader.exec_module(mod)
    except Exception as e:
        print(f"[FAIL] xhs_config.py 로드 실패: {e}")
        return []

    search_kw = getattr(mod, "SEARCH_KEYWORDS", []) or []
    brand_kw = getattr(mod, "BRAND_KEYWORDS", []) or []
    merged = list(dict.fromkeys(list(search_kw) + list(brand_kw)))  # dedup + 순서 보존
    print(f"[keyword-list] SEARCH {len(search_kw)}개 + BRAND {len(brand_kw)}개 "
          f"→ 합쳐서 {len(merged)}개 (중복 제거)")
    return merged


def parse_args():
    p = argparse.ArgumentParser(
        description=(
            "XHS 키워드 검색 크롤러 — 검색 결과 페이지에서 listener 캡처.\n"
            "  자동 전체: python runners/grab_xhs_keyword.py --reset-session\n"
            "             (xhs_config.py SEARCH_KEYWORDS + BRAND_KEYWORDS 합쳐서)\n"
            "  검증/특정: python runners/grab_xhs_keyword.py 鞋 --reset-session\n"
            "  콤마 여러: python runners/grab_xhs_keyword.py 鞋,包 --reset-session"
        ),
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    p.add_argument("keywords", nargs="?", default=None,
                   help="검색 키워드 (콤마로 여러 개). 미지정 시 xhs_config.py 전체 자동")
    p.add_argument("--reset-session", action="store_true")
    p.add_argument("--detail-count", type=int, default=10,
                   help="노트당 detail 진입 개수 (0=skip, -1=전체, 기본 10). "
                        "-1은 키워드당 캡처된 모든 노트(보통 100~300개) detail 진입 — 운영 시간 매우 김")
    p.add_argument("--keep-open", action="store_true")
    p.add_argument("--no-images", action="store_true")
    p.add_argument("--image-concurrency", type=int, default=5)
    p.add_argument("--max-scrolls", type=int, default=10)
    p.add_argument("--gap-min", type=float, default=4.0)
    p.add_argument("--gap-max", type=float, default=7.0)
    # 배치 + 휴식 — grab_xhs.py와 동일 정책 (10명/배치, 10분 휴식)
    p.add_argument("--batch-size", type=int, default=10,
                   help="배치 당 키워드 수 (기본 10)")
    p.add_argument("--batch-rest", type=int, default=600,
                   help="배치 사이 휴식 (초, 기본 600=10분). 0이면 휴식 없음")
    return p.parse_args()


async def main():
    args = parse_args()
    os.makedirs(OUTPUT_DIR, exist_ok=True)

    # 키워드 결정: 인자 명시 > xhs_config.py 자동 로드
    if args.keywords:
        keywords = [k.strip() for k in args.keywords.split(",") if k.strip()]
        print(f"[keyword-list] 명시값 {len(keywords)}개: {keywords}")
    else:
        keywords = load_xhs_all_keywords()

    if not keywords:
        print("[FAIL] 키워드가 비어있음.")
        sys.exit(1)

    output_base = make_keyword_output_dir()
    print(f"[keyword ] 대상 {len(keywords)}개: {keywords}")
    print(f"[output  ] {output_base}")
    print(f"[detail  ] {'전체(-1)' if args.detail_count == -1 else f'명시값 {args.detail_count}'}")

    if args.reset_session:
        if os.path.exists(USER_DATA_DIR):
            shutil.rmtree(USER_DATA_DIR)
            print(f"[reset] user_data_dir 삭제")
        if os.path.exists(COOKIE_FILE):
            os.remove(COOKIE_FILE)
            print(f"[reset] cookie 파일 삭제")
    os.makedirs(USER_DATA_DIR, exist_ok=True)

    proxy = build_proxy()
    chrome_path = find_system_chrome()
    if not chrome_path:
        print(f"[FAIL] 시스템 Chrome 못 찾음.")
        sys.exit(1)
    chrome_env = os.getenv("CHROME_PATH")
    if chrome_env and os.path.exists(chrome_env):
        chrome_path = chrome_env
    print(f"[chrome ] {chrome_path}")

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

        await verify_proxy_ip(page, ctx, args)

        if os.path.exists(COOKIE_FILE):
            try:
                with open(COOKIE_FILE, "r", encoding="utf-8") as f:
                    saved = json.load(f)
                await ctx.add_cookies(saved)
                print(f"[cookie ] 저장본 로드 ({len(saved)}개)")
            except Exception as e:
                print(f"[cookie ] 로드 실패: {e}")

        print(f"\n[1] xhs.com 진입 + 로그인 확인")
        try:
            await page.goto("https://www.xiaohongshu.com/explore",
                            wait_until="domcontentloaded", timeout=30000)
        except Exception as e:
            print(f"[ERROR] goto 실패: {e}")
            await shutdown(ctx, args, reason="진입 실패")
            sys.exit(1)
        await asyncio.sleep(4)

        await diag_login_signals(page, ctx, label="explore 진입 직후")

        if await is_real_login(page, ctx):
            print(f"  ✓ 자동 로그인 (cookie 유효)")
            await save_cookies_to_file(ctx, label="(refresh) ")
        else:
            ok = await wait_for_qr_login(page, ctx)
            if not ok:
                print(f"[ERROR] QR 시간 초과")
                await shutdown(ctx, args, reason="QR 로그인 시간 초과")
                sys.exit(1)
            await verify_login_stable(page, ctx, timeout=30, stable_count=2, interval=3)
            await asyncio.sleep(5)
            await diag_login_signals(page, ctx, label="QR 로그인 안정화 후")
            await save_cookies_to_file(ctx, label="(new login) ")

        # === 키워드 배치 순회 (grab_xhs.py와 동일 패턴) ===
        # 10개/배치, 10분 휴식, 키워드 사이 4-7초 지터
        results = {}
        total = len(keywords)
        batch_size = max(1, args.batch_size)
        n_batches = (total + batch_size - 1) // batch_size
        print(f"\n[batch ] {batch_size}개/배치, 휴식 {args.batch_rest//60}분, "
              f"지터 {args.gap_min:.1f}~{args.gap_max:.1f}초")

        for batch_idx in range(n_batches):
            b_start = batch_idx * batch_size
            b_end = min(b_start + batch_size, total)
            batch_kw = keywords[b_start:b_end]
            now_str = datetime.now().strftime("%H:%M")
            print(f"\n[배치 {batch_idx+1}/{n_batches}] ({now_str}) 키워드 {b_start+1}-{b_end} ({len(batch_kw)}개)")

            for inner_idx, keyword in enumerate(batch_kw):
                k_idx = b_start + inner_idx + 1
                print(f"\n  [{k_idx}/{total}] keyword='{keyword}'")

                if k_idx > 1:
                    gap = random.uniform(args.gap_min, args.gap_max)
                    await asyncio.sleep(gap)

                notes, meta = await collect_notes_by_keyword(page, keyword, max_scrolls=args.max_scrolls)
                if not notes:
                    err = meta.get("error") if isinstance(meta, dict) else None
                    print(f"  ⏭ SKIP: '{keyword}' — 노트 0개{f' ({err})' if err else ''}")
                    results[keyword] = {"count": 0, "skipped": True}
                    continue

                # detail 진입 (default -1 = 전체)
                if args.detail_count != 0:
                    target = len(notes) if args.detail_count == -1 else min(args.detail_count, len(notes))
                    print(f"\n  · 노트 상세 진입 ({target}개)")
                    for i, n in enumerate(notes[:target]):
                        nid = n.get("noteId") or ""
                        if not nid:
                            continue
                        print(f"    [{i+1}/{target}] {nid[:10]}... ", end="", flush=True)
                        detail = await collect_note_detail(page, nid, n.get("xsec_token", ""))
                        if detail and "error" not in detail:
                            n["desc"] = detail.get("desc", "")
                            n["post_date"] = format_post_date(detail.get("time"))
                            n["location"] = detail.get("ip_location", "")
                            n["images_captured"] = detail.get("image_count", 0)
                            n["image_urls"] = detail.get("image_urls") or []
                            n["video_url"] = detail.get("video_url", "")
                            for k_fld in ("likes", "comments", "stars", "shares"):
                                if detail.get(k_fld):
                                    n[k_fld] = detail[k_fld]
                            img_count = len(n.get("image_urls") or [])
                            print(f"✓ 댓글={detail.get('comments', 0)} 별={detail.get('stars', 0)} 이미지={img_count}")
                        else:
                            err = (detail or {}).get("error", "unknown")
                            print(f"✗ {err[:60]}")
                        if i + 1 < target:
                            await asyncio.sleep(random.uniform(3.0, 7.0))

                # 이미지/영상 다운로드
                safe_keyword = re.sub(r'[\\/:*?"<>|]', "_", keyword).strip() or "unknown"
                keyword_dir = os.path.join(output_base, safe_keyword)
                os.makedirs(keyword_dir, exist_ok=True)

                if not args.no_images:
                    total_saved, total_failed = 0, 0
                    for n in notes:
                        nid = n.get("noteId")
                        if not nid:
                            continue
                        img_urls = n.get("image_urls") or []
                        if not img_urls and n.get("cover"):
                            img_urls = [n["cover"]]
                        if not img_urls:
                            continue
                        note_dir = os.path.join(keyword_dir, nid)
                        saved, failed = await download_note_images(
                            page, note_dir, img_urls,
                            concurrency=args.image_concurrency,
                        )
                        n["images_captured"] = saved
                        total_saved += saved
                        total_failed += failed
                    print(f"  🖼  이미지: {total_saved}장 성공, {total_failed}장 실패")

                    video_saved, video_failed = 0, 0
                    for n in notes:
                        nid = n.get("noteId")
                        vurl = n.get("video_url") or ""
                        if not nid or not vurl:
                            continue
                        note_dir = os.path.join(keyword_dir, nid)
                        if await download_note_video(page, note_dir, vurl):
                            video_saved += 1
                        else:
                            video_failed += 1
                    if video_saved or video_failed:
                        print(f"  🎬 영상: {video_saved}개 성공, {video_failed}개 실패")

                # notes.json
                written_dir, n_written = write_keyword_output(output_base, keyword, notes)
                print(f"  📁 {written_dir} ({n_written}개 노트)")
                # 샘플
                for s_idx, n in enumerate(notes[:3]):
                    print(f"    [{s_idx}] {n['noteId'][:10]}... | {(n.get('title') or '')[:30]} | likes={n.get('likes', '')}")
                results[keyword] = {"count": n_written, "dir": written_dir}

            # ↑ for inner_idx (키워드) loop 끝 — batch loop 안

            # 배치 휴식 (마지막 배치는 휴식 안 함)
            if batch_idx + 1 < n_batches and args.batch_rest > 0:
                rest_min = args.batch_rest / 60
                done_at = datetime.now().strftime("%H:%M")
                resume_at = (datetime.now() + timedelta(seconds=args.batch_rest)).strftime("%H:%M")
                print(f"\n  ✓ 배치 {batch_idx+1} 완료 ({done_at}) → {rest_min:.0f}분 휴식 (재개 {resume_at})")
                await asyncio.sleep(args.batch_rest)

        # 요약 (batch loop 밖)
        print(f"\n{'='*50}\n  요약\n{'='*50}")
        success_count = sum(1 for r in results.values() if not r.get("skipped"))
        skip_count = sum(1 for r in results.values() if r.get("skipped"))
        for k, r in results.items():
            if r.get("skipped"):
                print(f"  '{k}': ⏭ SKIP")
            else:
                print(f"  '{k}': ✅ {r.get('count', 0)}개")
        print(f"\n  성공 {success_count} / 건너뜀 {skip_count}")

        await shutdown(ctx, args, reason="정상 완료")


if __name__ == "__main__":
    asyncio.run(main())
