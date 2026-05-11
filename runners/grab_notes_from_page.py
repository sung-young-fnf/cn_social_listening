"""샤오홍슈 노트 데이터를 페이지 SSR(__INITIAL_STATE__)에서 직접 추출 — B 방식.

user_posted API 호출 없이 프로필 페이지에 박힌 데이터만 사용.
→ 봇 감지 거의 안 받음 (정상 사용자가 페이지 보는 행위와 동일)
→ 단점: 첫 화면 분량(~20개)만 받을 수 있음. 그 이상은 스크롤 필요

사용법:
    python runners/grab_notes_from_page.py 5842afd75e87e7332ea90fda

결과:
    output/notes_<user_id>.csv  (19컬럼 schema 호환, 가능한 항목만 채움)

선택 옵션:
    --reset-session       user_data_dir 삭제 후 QR 새로
    --max-notes 10        최대 노트 수 (기본 20)
"""
import argparse
import asyncio
import csv
import os
import secrets
import shutil
import sys
from datetime import datetime

from playwright.async_api import async_playwright


def _build_oxylabs_proxy():
    """한국 IP + 세션 단위 sticky + 세션 간 rotation."""
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
    print(f"[grab_notes] {mode} sessid={sessid} country={country}")
    return {
        "server": f"http://{os.getenv('OXYLABS_HOST', 'pr.oxylabs.io')}:"
                  f"{os.getenv('OXYLABS_PORT', '7777')}",
        "username": username,
        "password": os.getenv("OXYLABS_PASSWORD", "Prcsdata_1234"),
    }


BASE_DIR = os.path.dirname(os.path.abspath(__file__))
USER_DATA_DIR = os.path.abspath(os.path.join(
    BASE_DIR, "..", "crawlers", "MediaCrawler", "browser_data", "xhs_user_data_dir"
))
OUTPUT_DIR = os.path.abspath(os.path.join(BASE_DIR, "..", "output"))

POST_COLUMNS = [
    "keyword", "author", "content", "likes", "stars", "comments",
    "images_captured", "post_date", "location", "post_type", "recommendations",
    "shares", "key", "timestamp", "note_title", "note_text", "unique_hash",
    "thumbnail_path", "post_url",
]


def parse_cn_number(text):
    if text is None or text == "":
        return 0
    s = str(text).replace("+", "").replace(",", "").strip()
    try:
        if "万" in s:
            return int(float(s.replace("万", "")) * 10000)
        if "亿" in s:
            return int(float(s.replace("亿", "")) * 100000000)
        return int(float(s))
    except (ValueError, TypeError):
        return 0


def parse_args():
    p = argparse.ArgumentParser()
    p.add_argument("user_id")
    p.add_argument("--max-notes", type=int, default=20)
    p.add_argument("--reset-session", action="store_true")
    return p.parse_args()


async def main():
    args = parse_args()
    profile_url = f"https://www.xiaohongshu.com/user/profile/{args.user_id}"

    if args.reset_session and os.path.exists(USER_DATA_DIR):
        shutil.rmtree(USER_DATA_DIR)
        print(f"[reset] user_data_dir 삭제됨")

    os.makedirs(USER_DATA_DIR, exist_ok=True)
    os.makedirs(OUTPUT_DIR, exist_ok=True)

    proxy = _build_oxylabs_proxy()

    async with async_playwright() as pw:
        ctx = await pw.chromium.launch_persistent_context(
            user_data_dir=USER_DATA_DIR,
            headless=False,
            proxy=proxy,
            viewport={"width": 1920, "height": 1080},
            user_agent=(
                "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                "AppleWebKit/537.36 (KHTML, like Gecko) "
                "Chrome/131.0.0.0 Safari/537.36"
            ),
            locale="zh-CN",
        )
        page = ctx.pages[0] if ctx.pages else await ctx.new_page()

        print(f"[goto] {profile_url}")
        try:
            await page.goto(profile_url, wait_until="domcontentloaded", timeout=30000)
        except Exception as e:
            print(f"[ERROR] goto 실패: {e}")
            await ctx.close()
            sys.exit(1)

        await asyncio.sleep(3)

        # 로그인 상태 확인 — anonymous web_session 무시, 실제 user_id 있어야 통과
        # (xhs가 페이지 진입 즉시 익명 web_session을 자동 발급함. 그것만으로는 노트 ID 못 받음)
        async def is_real_login():
            try:
                result = await page.evaluate("""() => {
                    const u = window.__INITIAL_STATE__?.user;
                    if (!u) return false;
                    const unwrap = (v) => (v && typeof v === 'object' && '_value' in v) ? v._value : v;
                    const loginUser = unwrap(u.loginUser);
                    if (loginUser && (loginUser.userId || loginUser.user_id)) return true;
                    const userInfo = unwrap(u.userInfo);
                    if (userInfo && (userInfo.userId || userInfo.user_id)) return true;
                    // 노트의 id가 실제 값으로 채워져 있는지 (익명이면 빈 문자열)
                    const notes = unwrap(u.notes);
                    if (Array.isArray(notes) && notes[0]) {
                        const first = notes[0];
                        if (Array.isArray(first) && first[0]) {
                            const id = first[0].id || first[0].noteCard?.noteId;
                            if (id && id.length > 0) return true;
                        }
                    }
                    return false;
                }""")
                return bool(result)
            except Exception:
                return False

        if not await is_real_login():
            print(f"\n  ★ 폰 샤오홍슈 앱으로 화면의 QR을 스캔해주세요.")
            print(f"     익명 세션은 노트 ID 못 받으므로 실제 로그인 필수.")
            print(f"     최대 180초 대기. 슬라이더 캡차 뜨면 직접 풀어주세요.\n")
            start = asyncio.get_event_loop().time()
            last_print = 0
            while asyncio.get_event_loop().time() - start < 180:
                if await is_real_login():
                    print(f"  ✓ 실제 로그인 감지 → 5초 후 페이지 reload")
                    break
                elapsed = int(asyncio.get_event_loop().time() - start)
                if elapsed - last_print >= 20:
                    print(f"     대기 중... 남은 {180 - elapsed}초")
                    last_print = elapsed
                await asyncio.sleep(1)
            else:
                print(f"[ERROR] QR 로그인 시간 초과")
                await ctx.close()
                sys.exit(1)
            await asyncio.sleep(5)
            # 로그인 후 프로필 페이지 재진입 + 데이터 박힐 때까지 polling
            try:
                await page.goto(profile_url, wait_until="domcontentloaded", timeout=30000)
            except Exception as e:
                print(f"[ERROR] reload 실패: {e}")
                await ctx.close()
                sys.exit(1)
            # user.notes 데이터가 박힐 때까지 polling (최대 30초)
            print(f"  ... 페이지 데이터 hydrate 대기")
            data_ready = False
            for sec in range(30):
                has_data = await page.evaluate("""() => {
                    const u = window.__INITIAL_STATE__?.user;
                    if (!u) return false;
                    const unwrap = (v) => (v && typeof v === 'object' && '_value' in v) ? v._value : v;
                    const notes = unwrap(u.notes);
                    if (!Array.isArray(notes)) return false;
                    if (notes.length === 0) return false;
                    const first = notes[0];
                    return Array.isArray(first) && first.length > 0;
                }""")
                if has_data:
                    print(f"  ✓ 데이터 감지 ({sec+1}초 후)")
                    data_ready = True
                    break
                await asyncio.sleep(1)
            if not data_ready:
                print(f"  ⚠ 30초 polling 후에도 데이터 미감지 — 추출 시도하지만 실패 가능")
            await asyncio.sleep(2)

        # 사람처럼 스크롤
        try:
            await page.mouse.wheel(0, 600)
            await asyncio.sleep(2)
            await page.mouse.wheel(0, 600)
            await asyncio.sleep(2)
        except Exception:
            pass

        html = await page.content()
        if "请通过验证" in html:
            print(f"[WARN] 캡차 페이지 — 브라우저에서 직접 풀어주세요. 30초 대기.")
            await asyncio.sleep(30)
            html = await page.content()

        # __INITIAL_STATE__.user.notes에서 데이터 추출
        notes_data = await page.evaluate("""() => {
            const out = { ok: false, user: null, notes: [] };
            if (!window.__INITIAL_STATE__) return out;
            const u = window.__INITIAL_STATE__.user;
            if (!u) return out;
            const unwrap = (v) => (v && typeof v === 'object' && '_value' in v) ? v._value : v;
            // userPageData에서 nickname
            const upd = unwrap(u.userPageData) || {};
            const basicInfo = upd.basicInfo || {};
            out.user = {
                nickname: basicInfo.nickname || '',
            };
            // notes 배열 (보통 notes[0] = 페이지 1)
            const notes = unwrap(u.notes);
            if (!Array.isArray(notes) || notes.length === 0) return out;
            const firstPage = notes[0];
            if (!Array.isArray(firstPage)) return out;
            for (const item of firstPage) {
                const nc = item.noteCard || item;
                const inter = nc.interactInfo || {};
                const cover = nc.cover || {};
                out.notes.push({
                    note_id: item.id || nc.noteId || nc.note_id || '',
                    xsec_token: item.xsecToken || nc.xsecToken || '',
                    type: nc.type || '',
                    title: nc.displayTitle || nc.title || '',
                    desc: nc.desc || '',
                    user_nickname: (nc.user && nc.user.nickname) || basicInfo.nickname || '',
                    liked_count: inter.likedCount || inter.liked_count || 0,
                    collected_count: inter.collectedCount || inter.collected_count || 0,
                    comment_count: inter.commentCount || inter.comment_count || 0,
                    share_count: inter.shareCount || inter.share_count || 0,
                    cover_url: cover.urlDefault || cover.url || '',
                    image_count: (nc.imageList || []).length || 0,
                });
            }
            out.ok = true;
            return out;
        }""")

        if not notes_data.get("ok") or len(notes_data.get("notes", [])) == 0:
            reason = "state 접근 불가" if not notes_data.get("ok") else "노트 0개 (봇 감지 or 무로그인 추정)"
            print(f"[FAIL] {reason}")
            print(f"       HTML 길이: {len(html)}자 (정상 ~570k, soft block ~450k)")
            print(f"       user nickname: '{notes_data.get('user', {}).get('nickname', '')}'")
            dump_path = os.path.join(OUTPUT_DIR, f"notes_dump_{args.user_id}.html")
            with open(dump_path, "w", encoding="utf-8") as f:
                f.write(html)
            print(f"       HTML dump: {dump_path}")
            await ctx.close()
            sys.exit(1)

        notes = notes_data["notes"][:args.max_notes]
        author = notes_data["user"]["nickname"]
        print(f"\n[OK] 노트 {len(notes)}개 추출 (author: {author})")

        timestamp_str = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        rows = []
        for n in notes:
            note_id = n["note_id"]
            post_type = {"normal": "이미지", "video": "동영상"}.get(n["type"], n["type"])
            rows.append({
                "keyword": args.user_id,
                "author": n["user_nickname"] or author,
                "content": n["desc"],
                "likes": parse_cn_number(n["liked_count"]),
                "stars": parse_cn_number(n["collected_count"]),
                "comments": parse_cn_number(n["comment_count"]),
                "images_captured": n["image_count"],
                "post_date": "",  # SSR에 timestamp 없음 → 노트 상세 필요
                "location": "",
                "post_type": post_type,
                "recommendations": 0,
                "shares": parse_cn_number(n["share_count"]),
                "key": f"{n['user_nickname']}__{n['liked_count']}",
                "timestamp": timestamp_str,
                "note_title": n["title"],
                "note_text": n["desc"],
                "unique_hash": note_id,
                "thumbnail_path": f"xiaohongshu/profile/image/{args.user_id}/{note_id}/{note_id}_1.jpg" if note_id else "",
                "post_url": f"https://www.xiaohongshu.com/explore/{note_id}" if note_id else "",
            })

        csv_path = os.path.join(OUTPUT_DIR, f"notes_{args.user_id}.csv")
        with open(csv_path, "w", encoding="utf-8-sig", newline="") as f:
            writer = csv.DictWriter(f, fieldnames=POST_COLUMNS)
            writer.writeheader()
            writer.writerows(rows)

        print(f"\n[저장] {csv_path}")
        print(f"\n샘플 (첫 3개):")
        for r in rows[:3]:
            print(f"  - {r['note_title'][:40]:40s} | likes={r['likes']:6d} comments={r['comments']:5d} type={r['post_type']}")

        await ctx.close()


if __name__ == "__main__":
    asyncio.run(main())
