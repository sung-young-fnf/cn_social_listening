"""노트 상세 페이지 SSR 진입 가능 여부 + 데이터 구조 진단.

흐름:
1. 프로필 페이지 진입해서 첫 노트의 note_id + xsec_token 추출
2. /explore/{note_id}?xsec_token=...&xsec_source=... 진입 (비로그인)
3. 로그인 모달 떴는지 확인
4. __INITIAL_STATE__ 구조 진단 + 가능한 데이터 추출
5. 결과: 콘솔 + output/note_detail_diag.json

사용법:
    python runners/grab_note_detail.py 5a16311de8ac2b349577ec8e

(reset-session 안 함 — 직전 grab_notes_from_page의 영속 세션 활용)
"""
import argparse
import asyncio
import json
import os
import secrets
import shutil
import sys

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
    print(f"[detail] {mode} sessid={sessid} country={country}")
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


async def main():
    p = argparse.ArgumentParser()
    p.add_argument("user_id")
    p.add_argument("--reset-session", action="store_true")
    args = p.parse_args()

    profile_url = f"https://www.xiaohongshu.com/user/profile/{args.user_id}"
    os.makedirs(OUTPUT_DIR, exist_ok=True)
    if args.reset_session and os.path.exists(USER_DATA_DIR):
        shutil.rmtree(USER_DATA_DIR)
        print(f"[reset] user_data_dir 삭제됨")
    os.makedirs(USER_DATA_DIR, exist_ok=True)
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

        # === 1단계: 프로필 페이지에서 첫 노트 id + xsec_token 추출 ===
        print(f"\n[1/3] 프로필 페이지: {profile_url}")
        await page.goto(profile_url, wait_until="domcontentloaded", timeout=30000)

        # 실제 로그인 확인 — anonymous web_session 무시, user_id 있어야 통과
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
            print(f"\n  ★ 폰 샤오홍슈 앱으로 QR 스캔. 익명 세션 무시 — 진짜 로그인 필요. 최대 180초.")
            for sec in range(180):
                if await is_real_login():
                    print(f"  ✓ 실제 로그인 감지 ({sec+1}초)")
                    break
                if sec % 20 == 0 and sec > 0:
                    print(f"     대기 중... 남은 {180-sec}초")
                await asyncio.sleep(1)
            else:
                print(f"[ERROR] QR 시간 초과")
                await ctx.close()
                sys.exit(1)
            await asyncio.sleep(5)
            await page.goto(profile_url, wait_until="domcontentloaded", timeout=30000)

        # 데이터 hydrate polling (최대 30초)
        print(f"      ... 페이지 데이터 hydrate 대기")
        for sec in range(30):
            has_data = await page.evaluate("""() => {
                const u = window.__INITIAL_STATE__?.user;
                if (!u) return false;
                const unwrap = (v) => (v && typeof v === 'object' && '_value' in v) ? v._value : v;
                const notes = unwrap(u.notes);
                return Array.isArray(notes) && notes.length > 0
                    && Array.isArray(notes[0]) && notes[0].length > 0;
            }""")
            if has_data:
                print(f"      ✓ 데이터 감지 ({sec+1}초 후)")
                break
            await asyncio.sleep(1)
        else:
            html = await page.content()
            print(f"      ✗ hydrate 미감지. HTML {len(html)}자")
            dump = os.path.join(OUTPUT_DIR, "note_detail_profile_dump.html")
            with open(dump, "w", encoding="utf-8") as f:
                f.write(html)
            print(f"      dump: {dump}")
            await ctx.close()
            sys.exit(1)

        first_note = await page.evaluate("""() => {
            if (!window.__INITIAL_STATE__) return null;
            const u = window.__INITIAL_STATE__.user;
            if (!u) return null;
            const unwrap = (v) => (v && typeof v === 'object' && '_value' in v) ? v._value : v;
            const notes = unwrap(u.notes);
            if (!Array.isArray(notes) || notes.length === 0) return null;
            const first = notes[0];
            if (!Array.isArray(first) || first.length === 0) return null;
            const n = first[0];
            const nc = n.noteCard || n;
            return {
                note_id: n.id || nc.noteId || nc.note_id || '',
                xsec_token: n.xsecToken || nc.xsecToken || '',
                title: nc.displayTitle || nc.title || '',
            };
        }""")
        if not first_note or not first_note.get("note_id"):
            print(f"[FAIL] 프로필 페이지에서 첫 노트 정보 못 받음")
            print(f"      first_note raw: {first_note}")
            # 노트 ID 후보들 직접 출력 + 첫 3개 노트의 ID 위치 확인
            raw_dump = await page.evaluate("""() => {
                const u = window.__INITIAL_STATE__?.user;
                if (!u) return null;
                const unwrap = (v) => (v && typeof v === 'object' && '_value' in v) ? v._value : v;
                const notes = unwrap(u.notes);
                if (!Array.isArray(notes) || notes.length === 0) return null;
                const first = notes[0];
                if (!Array.isArray(first) || first.length === 0) return null;
                const out = { len: first.length, notes: [] };
                for (let i = 0; i < Math.min(3, first.length); i++) {
                    const n = first[i];
                    const nc = n.noteCard || {};
                    out.notes.push({
                        idx: i,
                        'n.id': n.id,
                        'nc.noteId': nc.noteId,
                        'nc.note_id': nc.note_id,
                        'nc.id': nc.id,
                        'n.noteId': n.noteId,
                        'sticky': nc.interactInfo?.sticky,
                        'displayTitle': nc.displayTitle?.slice(0, 40),
                    });
                }
                return out;
            }""")
            print(f"      total notes : {raw_dump.get('len') if raw_dump else None}")
            for n_info in (raw_dump.get("notes", []) if raw_dump else []):
                print(f"      note[{n_info['idx']}]:")
                for k, v in n_info.items():
                    if k != "idx":
                        print(f"        {k:20s}: {v}")
            await ctx.close()
            sys.exit(1)
        print(f"      note_id   : {first_note['note_id']}")
        print(f"      xsec_token: {first_note['xsec_token'][:50]}...")
        print(f"      title     : {first_note['title']}")

        # === 2단계: 노트 상세 페이지 진입 ===
        detail_url = (
            f"https://www.xiaohongshu.com/explore/{first_note['note_id']}"
            f"?xsec_token={first_note['xsec_token']}&xsec_source=pc_user"
        )
        print(f"\n[2/3] 노트 상세 진입: {detail_url[:120]}...")
        await page.goto(detail_url, wait_until="domcontentloaded", timeout=30000)
        await asyncio.sleep(5)

        # 로그인 모달 떴는지
        html = await page.content()
        login_modal = "登录" in html and ("扫码登录" in html or "手机号登录" in html)
        print(f"      HTML 길이      : {len(html)}자")
        print(f"      로그인 모달    : {'YES' if login_modal else 'NO'}")

        # === 3단계: __INITIAL_STATE__ 구조 진단 + 데이터 추출 시도 ===
        print(f"\n[3/3] state 구조 진단 + 추출")
        diag = await page.evaluate("""() => {
            const out = { has_state: false, top_keys: [], note_keys: [] };
            if (!window.__INITIAL_STATE__) return out;
            out.has_state = true;
            const state = window.__INITIAL_STATE__;
            out.top_keys = Object.keys(state);
            const unwrap = (v) => (v && typeof v === 'object' && '_value' in v) ? v._value : v;
            // note 또는 noteData 같은 키 후보 탐색
            for (const k of ['note', 'noteData', 'noteDetail', 'detail']) {
                if (state[k]) {
                    const v = unwrap(state[k]);
                    if (v && typeof v === 'object') {
                        out.note_keys.push({key: k, subkeys: Object.keys(v).slice(0, 20)});
                        // noteDetailMap 패턴
                        if (v.noteDetailMap) {
                            const map = unwrap(v.noteDetailMap);
                            if (map && typeof map === 'object') {
                                const first_id = Object.keys(map)[0];
                                if (first_id) {
                                    const entry = unwrap(map[first_id]);
                                    if (entry && entry.note) {
                                        out.found_note = entry.note;
                                    } else if (entry) {
                                        out.found_note = entry;
                                    }
                                }
                            }
                        }
                        // 직접 v 안에 데이터
                        if (!out.found_note && (v.title || v.desc)) {
                            out.found_note = v;
                        }
                    }
                }
            }
            return out;
        }""")

        print(f"      __INITIAL_STATE__ exists: {diag['has_state']}")
        print(f"      top keys                : {diag.get('top_keys', [])[:15]}")
        for nk in diag.get("note_keys", []):
            print(f"      key '{nk['key']}' subkeys : {nk['subkeys']}")

        found_note = diag.get("found_note")
        if found_note:
            print(f"\n      [발견] note 데이터 키: {list(found_note.keys())[:25]}")
            for col in ["title", "desc", "time", "ipLocation", "ip_location",
                        "interactInfo", "interact_info", "user", "imageList", "image_list"]:
                if col in found_note:
                    val = found_note[col]
                    preview = json.dumps(val, ensure_ascii=False)[:120] if isinstance(val, (dict, list)) else str(val)[:120]
                    print(f"        {col:18s}: {preview}")

        # 결과 dump
        dump = {
            "first_note": first_note,
            "detail_url": detail_url,
            "html_length": len(html),
            "login_modal": login_modal,
            "diag": diag,
        }
        diag_path = os.path.join(OUTPUT_DIR, "note_detail_diag.json")
        with open(diag_path, "w", encoding="utf-8") as f:
            json.dump(dump, f, ensure_ascii=False, indent=2, default=str)
        print(f"\n[저장] 진단 dump → {diag_path}")

        # HTML도 저장 (구조 확인용)
        html_path = os.path.join(OUTPUT_DIR, "note_detail_dump.html")
        with open(html_path, "w", encoding="utf-8") as f:
            f.write(html)
        print(f"       HTML dump  → {html_path}")

        if login_modal:
            print(f"\n[결론] 로그인 모달 떴음 → 비로그인 진입 안 됨. QR 필요.")
        elif found_note:
            print(f"\n[결론] ✅ 비로그인 + 토큰으로 노트 상세 SSR 데이터 받음!")
        else:
            print(f"\n[결론] ⚠ 모달은 없는데 노트 데이터 못 찾음 — state 구조 더 분석 필요")

        await ctx.close()


if __name__ == "__main__":
    asyncio.run(main())
