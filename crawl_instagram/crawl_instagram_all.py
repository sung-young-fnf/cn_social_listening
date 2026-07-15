"""인스타그램 통합 크롤러 — 계정마다 게시물 + 릴스를 한 번에 수집.

게시물 크롤러(crawl_instagram_post)와 릴스 크롤러(crawl_instagram_reels)의 로직을
그대로 재사용한다. 로그인은 1회만 하고, 세션을 공유해 계정별로:
  1. 게시물 최근 N개 → instagram_<account>_posts_YYYYMMDD.csv
  2. 릴스   최근 N개 → instagram_<account>_reels_YYYYMMDD.csv
를 각각 저장한다. 봇 감지 회피로 기본 5계정마다 5분 휴식.

대상 계정: accounts_list.py 의 ACCOUNTS 우선, 비면 accounts.txt.

사용법:
    python crawl_instagram/crawl_instagram_all.py --login   # 최초/만료 시 수동 로그인
    python crawl_instagram/crawl_instagram_all.py           # 세션 재사용, 전체 계정 게시물+릴스
    python crawl_instagram/crawl_instagram_all.py --account youra_ch0i --limit 10
    python crawl_instagram/crawl_instagram_all.py --batch-size 5 --batch-rest 300
    python crawl_instagram/crawl_instagram_all.py --no-proxy

출력 (계정별 파일 2개씩):
    crawl_instagram/output/instagram_<account>_posts_YYYYMMDD.csv
    crawl_instagram/output/instagram_<account>_reels_YYYYMMDD.csv
"""
import argparse
import asyncio
import os
import time
from datetime import datetime

# 게시물/릴스 크롤러의 함수 재사용. 두 모듈 모두 import 시 stdout UTF-8 래핑을
# 1회만 하도록 가드돼 있어 함께 불러도 안전하다.
import crawl_instagram_post as ig_post
import crawl_instagram_reels as ig_reels


def main():
    ap = argparse.ArgumentParser(description="인스타그램 통합 크롤러 (게시물+릴스)")
    ap.add_argument("--account", help="단일 username (accounts_list 무시)")
    ap.add_argument("--limit", type=int, default=10, help="계정당 게시물/릴스 각 수 (기본 10)")
    ap.add_argument("--login", action="store_true", help="강제 수동 로그인 (세션 갱신)")
    ap.add_argument("--no-proxy", action="store_true")
    ap.add_argument("--keep-ip", action="store_true",
                    help="프록시 IP 세션 유지 (기본은 매 실행 새 IP로 초기화)")
    ap.add_argument("--delay", type=float, default=2.0, help="단계 간 딜레이(초)")
    ap.add_argument("--batch-size", type=int, default=5,
                    help="봇 감지 회피 — 이 계정 수마다 휴식 (기본 5)")
    ap.add_argument("--batch-rest", type=int, default=300,
                    help="배치 사이 휴식 초 (기본 300=5분)")
    ap.add_argument("--skip-posts", action="store_true", help="게시물 생략(릴스만)")
    ap.add_argument("--skip-reels", action="store_true", help="릴스 생략(게시물만)")
    args = ap.parse_args()

    # === 대상 계정 (게시물 크롤러의 로더 재사용) ===
    if args.account:
        accounts = [args.account.strip()]
    else:
        accounts = ig_post.load_account_targets()
    if not accounts:
        print("[FAIL] 대상 계정 없음 (accounts_list.py 또는 accounts.txt 확인)")
        return

    use_proxy = not args.no_proxy
    if use_proxy and not args.keep_ip:
        ig_post.rotate_proxy_ip()

    # === 세션 확보 (로그인 1회, 게시물 크롤러 로직 재사용) ===
    cookies = ig_post.load_cookies()
    if args.login or not ig_post.has_valid_session(cookies):
        if args.login:
            print("[세션] --login → 수동 로그인 진행")
        else:
            print("[세션] 유효한 sessionid 없음 → 수동 로그인 진행 (최초 1회)")
        cookies = asyncio.run(ig_post.playwright_login(use_proxy))
    else:
        print(f"[세션] 저장된 sessionid 재사용 (length={len(cookies.get('sessionid',''))})")

    if not ig_post.has_valid_session(cookies):
        print("[FAIL] sessionid 확보 실패 — 로그인이 완료되지 않았습니다.")
        return

    proxies = ig_post.build_proxies_requests() if use_proxy else None
    session = ig_post.make_session(cookies, proxies)

    now = datetime.now()
    fetched_at = now.isoformat()
    total = len(accounts)
    batch_size = max(1, args.batch_size)
    print(f"\n대상 계정 {total}개 — 계정마다 게시물+릴스, "
          f"{batch_size}명마다 {args.batch_rest//60}분 휴식 (봇 감지 회피)")

    post_outputs, reel_outputs = [], []
    for idx, username in enumerate(accounts):
        # 배치 경계 휴식 (5명 처리 후 다음 배치 진입 전)
        if idx > 0 and idx % batch_size == 0:
            print(f"\n[배치 휴식] {idx}/{total} 완료 — {args.batch_rest//60}분 대기...")
            try:
                time.sleep(args.batch_rest)
            except KeyboardInterrupt:
                print("\n[중단] 휴식 중 Ctrl+C — 종료")
                break

        print(f"\n========== [{idx+1}/{total}] {username} ==========")
        try:
            profile = ig_post.fetch_profile(session, username)
            if not profile:
                print("  ⚠ 프로필 조회 실패 — username/세션 확인 필요")
                continue
            print(f"  프로필 OK: id={profile['id']} 팔로워={profile['follower_count']} "
                  f"게시물={profile['media_count']} 비공개={profile['is_private']}")

            # 1) 게시물
            if not args.skip_posts:
                items = ig_post.fetch_feed(session, profile["id"], max(args.limit, 12))
                prows = [ig_post.extract_post_from_feed_item(it, profile, fetched_at)
                         for it in (items or [])[:args.limit]]
                if prows:
                    po = ig_post.save_csv(prows, now, username)
                    post_outputs.append((username, len(prows)))
                    print(f"  [게시물] {len(prows)}개 → {os.path.basename(po)}")
                else:
                    print("  [게시물] 0개 (비공개/API 제한)")
                time.sleep(args.delay)

            # 2) 릴스
            if not args.skip_reels:
                reels = ig_reels.fetch_reels(session, profile["id"], args.limit, args.delay)
                rrows = [ig_reels.extract_reel(it, profile, fetched_at) for it in reels]
                if rrows:
                    ro = ig_reels.save_csv(rrows, now, username)
                    reel_outputs.append((username, len(rrows)))
                    print(f"  [릴스] {len(rrows)}개 → {os.path.basename(ro)}")
                else:
                    print("  [릴스] 0개 (릴스 없음/비공개/API 제한)")
        except KeyboardInterrupt:
            print("\n[중단] Ctrl+C — 종료 (여기까지 계정별 저장 완료)")
            break
        except Exception as e:
            print(f"  [오류] {type(e).__name__}: {str(e)[:120]} — 이 계정 건너뜀")
        time.sleep(args.delay)

    print(f"\n[전체 완료] 게시물 {len(post_outputs)}계정 / 릴스 {len(reel_outputs)}계정")
    for u, n in post_outputs:
        print(f"  게시물 {u}: {n}개")
    for u, n in reel_outputs:
        print(f"  릴스   {u}: {n}개")


if __name__ == "__main__":
    main()
