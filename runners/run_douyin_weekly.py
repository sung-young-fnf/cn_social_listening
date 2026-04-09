"""도우인 주간 크롤링 + S3 업로드 래퍼 스크립트

1단계: node crawlers/douyin-weekly-v5.js 실행 (크롤링 → 로컬 저장)
2단계: python uploaders/s3_upload_douyin_account.py (프로필 parquet + 이미지 → S3)
3단계: python uploaders/s3_upload_douyin_post.py (게시물 parquet + 썸네일 → S3)

사용법:
    python run_douyin_weekly.py --week 0323                        # 크롤링 + 업로드
    python run_douyin_weekly.py --week 0323 --upload-only          # 업로드만 (이미 크롤링 완료 시)
    python run_douyin_weekly.py --week 0323 --dry-run              # 전체 미리보기
    python run_douyin_weekly.py --week 0323 --start 2026-03-23 --end 2026-03-29  # 날짜 직접 지정
"""
import argparse
import subprocess
import sys
import os
import re
import json
from datetime import datetime, timedelta

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
REPO_ROOT = os.path.dirname(BASE_DIR)  # cn-social-listening/
DOUYIN_CRAWLER = os.path.join(REPO_ROOT, "crawlers", "douyin-weekly-v5.js")
UPLOAD_ACCOUNT = os.path.join(REPO_ROOT, "uploaders", "s3_upload_douyin_account.py")
UPLOAD_POST = os.path.join(REPO_ROOT, "uploaders", "s3_upload_douyin_post.py")


def parse_args():
    parser = argparse.ArgumentParser(description="도우인 주간 크롤링 + S3 업로드")
    parser.add_argument("--week", required=True, help="주차 시작일 MMDD (예: 0323)")
    parser.add_argument("--version", default="v5", help="크롤러 버전 (기본: v5)")
    parser.add_argument("--start", help="수집 시작일 yyyy-mm-dd (미지정 시 --week에서 자동 계산)")
    parser.add_argument("--end", help="수집 종료일 yyyy-mm-dd (미지정 시 시작일+6일)")
    parser.add_argument("--upload-only", action="store_true", help="크롤링 건너뛰고 업로드만 실행")
    parser.add_argument("--dry-run", action="store_true", help="실제 실행 없이 미리보기")
    return parser.parse_args()


def week_to_dates(week_str):
    """MMDD → (start_date, end_date) 자동 계산 (2026년 기준)"""
    month = int(week_str[:2])
    day = int(week_str[2:])
    start = datetime(2026, month, day)
    end = start + timedelta(days=6)
    return start.strftime("%Y-%m-%d"), end.strftime("%Y-%m-%d")


def update_crawler_config(week_str, version, date_start, date_end):
    """test-douyin-weekly-v5.js의 CONFIG 값을 업데이트"""
    with open(DOUYIN_CRAWLER, "r", encoding="utf-8") as f:
        content = f.read()

    folder_name = f"douyin-weekly-{week_str}-{version}"

    # outputDir 업데이트
    content = re.sub(
        r'outputDir:\s*"[^"]*"',
        f'outputDir: "../output/{folder_name}"',
        content,
    )

    # dateStart 업데이트
    content = re.sub(
        r'dateStart:\s*"[^"]*"',
        f'dateStart: "{date_start}T00:00:00+08:00"',
        content,
    )

    # dateEnd 업데이트
    content = re.sub(
        r'dateEnd:\s*"[^"]*"',
        f'dateEnd: "{date_end}T23:59:59+08:00"',
        content,
    )

    with open(DOUYIN_CRAWLER, "w", encoding="utf-8") as f:
        f.write(content)

    return folder_name


def run_step(step_name, cmd, dry_run=False, cwd=None):
    """단계 실행"""
    print(f"\n{'='*60}")
    print(f"  {step_name}")
    print(f"{'='*60}")
    print(f"  명령어: {' '.join(cmd)}")
    if cwd:
        print(f"  작업 디렉토리: {cwd}")
    print()

    if dry_run:
        print("  [DRY RUN] 건너뜀\n")
        return True

    result = subprocess.run(cmd, cwd=cwd or REPO_ROOT)
    if result.returncode != 0:
        print(f"\n  [ERROR] {step_name} 실패 (exit code: {result.returncode})")
        return False

    print(f"\n  [OK] {step_name} 완료")
    return True


def main():
    args = parse_args()

    # 날짜 계산
    if args.start and args.end:
        date_start, date_end = args.start, args.end
    else:
        date_start, date_end = week_to_dates(args.week)

    folder_name = f"douyin-weekly-{args.week}-{args.version}"
    data_dir = os.path.join(REPO_ROOT, "output", folder_name)

    print(f"""
╔══════════════════════════════════════════════════╗
║         도우인 주간 크롤링 + S3 업로드           ║
╠══════════════════════════════════════════════════╣
║  주차: {args.week}                                      ║
║  폴더: output/{folder_name:<33s}║
║  기간: {date_start} ~ {date_end}              ║
║  모드: {'업로드만' if args.upload_only else 'DRY RUN' if args.dry_run else '크롤링 + 업로드':<40s}║
╚══════════════════════════════════════════════════╝""")

    # 1단계: 크롤링
    if not args.upload_only:
        # 크롤러 설정 업데이트
        if not args.dry_run:
            folder_name = update_crawler_config(args.week, args.version, date_start, date_end)
            print(f"\n  크롤러 설정 업데이트 완료: {folder_name}")

        ok = run_step(
            "1단계: 도우인 크롤링",
            ["node", DOUYIN_CRAWLER],
            dry_run=args.dry_run,
            cwd=REPO_ROOT,
        )
        if not ok:
            print("\n크롤링 실패. 로컬 데이터 확인 후 --upload-only로 업로드만 재시도 가능합니다.")
            sys.exit(1)
    else:
        if not os.path.isdir(data_dir):
            print(f"\n  [ERROR] 폴더가 없습니다: {data_dir}")
            sys.exit(1)
        print(f"\n  크롤링 건너뜀 (--upload-only)")

    # 2단계: account 업로드 (절대 경로로 전달)
    output_abs = os.path.join(REPO_ROOT, "output", folder_name)
    upload_args = [sys.executable, "-X", "utf8", UPLOAD_ACCOUNT, output_abs]
    if args.dry_run:
        upload_args.append("--dry-run")

    ok = run_step("2단계: 프로필(account) S3 업로드", upload_args)
    if not ok:
        print(f"\naccount 업로드 실패. 재시도: python uploaders/s3_upload_douyin_account.py {output_abs}")

    # 3단계: post 업로드 (절대 경로로 전달)
    upload_args = [sys.executable, "-X", "utf8", UPLOAD_POST, output_abs]
    if args.dry_run:
        upload_args.append("--dry-run")

    ok = run_step("3단계: 게시물(post) S3 업로드", upload_args)
    if not ok:
        print(f"\npost 업로드 실패. 재시도: python uploaders/s3_upload_douyin_post.py {output_abs}")

    print(f"\n{'='*60}")
    print(f"  전체 완료!")
    print(f"{'='*60}\n")


if __name__ == "__main__":
    main()
