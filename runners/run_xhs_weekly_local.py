"""샤오홍슈 주간 크롤링 + 로컬 CSV 저장 (S3 업로드 없는 테스트용)

기존 run_xhs_weekly.py와 동일한 크롤링 흐름이지만 S3 업로드 대신
s3_upload_xhs_post.py / s3_upload_xhs_account.py와 똑같은 스키마로
로컬 CSV에 저장한다.

  - test_post.csv     19컬럼 (s3_upload_xhs_post.py 스키마와 동일)
  - test_account.csv  11컬럼 (s3_upload_xhs_account.py 스키마와 동일)

사용법:
    python runners/run_xhs_weekly_local.py --week 0323
    python runners/run_xhs_weekly_local.py --week 0323 --skip-crawl     # 이미 크롤링 완료 → 변환만
    python runners/run_xhs_weekly_local.py --week 0323 --start 2026-03-23 --end 2026-03-29
    python runners/run_xhs_weekly_local.py --week 0323 --out-dir ./test_csv
"""
import argparse
import csv
import json
import os
import re
import subprocess
import sys
from datetime import datetime, timedelta

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
REPO_ROOT = os.path.dirname(BASE_DIR)
MEDIACRAWLER_DIR = os.environ.get(
    "MEDIACRAWLER_DIR",
    os.path.join(REPO_ROOT, "crawlers", "MediaCrawler"),
)
BASE_CONFIG = os.path.join(MEDIACRAWLER_DIR, "config", "base_config.py")


def parse_args():
    p = argparse.ArgumentParser(description="샤오홍슈 주간 크롤링 + 로컬 CSV 저장")
    p.add_argument("--week", required=True, help="주차 시작일 MMDD (예: 0323)")
    p.add_argument("--start", help="수집 시작일 yyyy-mm-dd (미지정 시 --week에서 계산)")
    p.add_argument("--end", help="수집 종료일 yyyy-mm-dd (미지정 시 시작일+6일)")
    p.add_argument("--skip-crawl", action="store_true",
                   help="크롤링 건너뛰고 기존 output 폴더 → CSV 변환만")
    p.add_argument("--out-dir", default=None,
                   help="CSV 저장 폴더 (기본: <output폴더>/csv/)")
    return p.parse_args()


def week_to_dates(week_str):
    month, day = int(week_str[:2]), int(week_str[2:])
    start = datetime(2026, month, day)
    end = start + timedelta(days=6)
    return start.strftime("%Y-%m-%d"), end.strftime("%Y-%m-%d")


def update_crawler_config(week_str, date_start, date_end):
    """base_config.py 의 SAVE_DATA_PATH / 날짜 in-place 수정 (run_xhs_weekly.py와 동일)"""
    with open(BASE_CONFIG, "r", encoding="utf-8") as f:
        content = f.read()
    folder_name = f"red-weekly-26{week_str}"
    content = re.sub(r'SAVE_DATA_PATH\s*=\s*.*',
                     f'SAVE_DATA_PATH = "output/{folder_name}"', content)
    content = re.sub(r'CRAWLER_DATE_START\s*=\s*"[^"]*"',
                     f'CRAWLER_DATE_START = "{date_start}"', content)
    content = re.sub(r'CRAWLER_DATE_END\s*=\s*"[^"]*"',
                     f'CRAWLER_DATE_END = "{date_end}"', content)
    with open(BASE_CONFIG, "w", encoding="utf-8") as f:
        f.write(content)
    return folder_name


def parse_chinese_number(text):
    """한자 숫자 표기 → 정수 (s3_upload_xhs_post.py:60-73 동일)"""
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


# ===================== post 19컬럼 (S3 parquet 스키마와 1:1) =====================
POST_COLUMNS = [
    "keyword", "author", "content", "likes", "stars", "comments",
    "images_captured", "post_date", "location", "post_type", "recommendations",
    "shares", "key", "timestamp", "note_title", "note_text", "unique_hash",
    "thumbnail_path", "post_url",
]


def build_post_row(note, profile_id, timestamp_str):
    image_list = note.get("image_list", "")
    images_captured = len(image_list.split(",")) if image_list else 0

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
    note_url = note.get("note_url", "")
    post_url = note_url.split("?")[0] if note_url else ""

    return {
        "keyword": profile_id,
        "author": note.get("nickname", ""),
        "content": note.get("desc", ""),
        "likes": parse_chinese_number(note.get("liked_count", 0)),
        "stars": parse_chinese_number(note.get("collected_count", 0)),
        "comments": parse_chinese_number(note.get("comment_count", 0)),
        "images_captured": images_captured,
        "post_date": note.get("time", ""),
        "location": note.get("ip_location", ""),
        "post_type": post_type,
        "recommendations": 0,
        "shares": parse_chinese_number(note.get("share_count", 0)),
        "key": f"{note.get('nickname', '')}__{note.get('liked_count', '0')}",
        "timestamp": timestamp_str,
        "note_title": note.get("title", ""),
        "note_text": note.get("desc", ""),
        "unique_hash": note_id,
        "thumbnail_path": thumbnail_path,
        "post_url": post_url,
    }


# =================== account 11컬럼 (S3 parquet 스키마와 1:1) ===================
ACCOUNT_COLUMNS = [
    "user_id", "nickname", "gender", "desc", "ip_location",
    "fans", "following", "interaction", "tag_list",
    "timestamp", "profile_image_path",
]


def build_account_row(creator, timestamp_str, image_path):
    return {
        "user_id": creator.get("user_id", ""),
        "nickname": creator.get("nickname", ""),
        "gender": creator.get("gender", ""),
        "desc": creator.get("desc", ""),
        "ip_location": creator.get("ip_location", ""),
        "fans": parse_chinese_number(creator.get("fans", 0)),
        "following": parse_chinese_number(creator.get("follows", 0)),
        "interaction": parse_chinese_number(creator.get("interaction", 0)),
        "tag_list": creator.get("tag_list", ""),
        "timestamp": timestamp_str,
        "profile_image_path": image_path,
    }


def convert_to_csv(data_dir, out_dir):
    """MediaCrawler output 폴더를 읽어 test_post.csv / test_account.csv 생성"""
    os.makedirs(out_dir, exist_ok=True)
    post_csv_path = os.path.join(out_dir, "test_post.csv")
    account_csv_path = os.path.join(out_dir, "test_account.csv")

    folders = sorted([
        f for f in os.listdir(data_dir)
        if os.path.isdir(os.path.join(data_dir, f))
    ])

    timestamp_str = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    post_rows = []
    account_rows = []
    skipped = 0

    for folder in folders:
        folder_path = os.path.join(data_dir, folder)
        notes_path = os.path.join(folder_path, "notes.json")
        creator_path = os.path.join(folder_path, "creator.json")

        creator = None
        if os.path.isfile(creator_path):
            with open(creator_path, "r", encoding="utf-8") as f:
                creator = json.load(f)

        if creator:
            user_id = creator.get("user_id", folder)
            account_rows.append(
                build_account_row(
                    creator, timestamp_str,
                    f"xiaohongshu/account/image/{user_id}/{user_id}.png",
                )
            )

        if not os.path.isfile(notes_path):
            skipped += 1
            continue
        with open(notes_path, "r", encoding="utf-8") as f:
            notes = json.load(f)
        if not notes:
            skipped += 1
            continue

        if creator and creator.get("user_id"):
            profile_id = creator["user_id"]
        else:
            profile_id = notes[0].get("user_id", folder)

        for note in notes:
            post_rows.append(build_post_row(note, profile_id, timestamp_str))

    with open(post_csv_path, "w", encoding="utf-8-sig", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=POST_COLUMNS)
        writer.writeheader()
        writer.writerows(post_rows)

    with open(account_csv_path, "w", encoding="utf-8-sig", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=ACCOUNT_COLUMNS)
        writer.writeheader()
        writer.writerows(account_rows)

    print(f"\n{'='*60}")
    print(f"  CSV 변환 완료")
    print(f"{'='*60}")
    print(f"  계정(account): {len(account_rows):>4}행 → {account_csv_path}")
    print(f"  게시물(post):  {len(post_rows):>4}행 → {post_csv_path}")
    print(f"  notes 없는 계정: {skipped}개 (건너뜀)")


def main():
    args = parse_args()

    if args.start and args.end:
        date_start, date_end = args.start, args.end
    else:
        date_start, date_end = week_to_dates(args.week)

    folder_name = f"red-weekly-26{args.week}"
    data_dir = os.path.join(MEDIACRAWLER_DIR, "output", folder_name)
    out_dir = args.out_dir or os.path.join(data_dir, "csv")

    print(f"""
============================================================
  샤오홍슈 주간 크롤링 + 로컬 CSV (테스트, S3 업로드 없음)
============================================================
  주차       : {args.week}
  데이터폴더 : {data_dir}
  CSV 저장   : {out_dir}
  기간       : {date_start} ~ {date_end}
  모드       : {'CSV 변환만 (--skip-crawl)' if args.skip_crawl else '크롤링 + CSV 변환'}
============================================================""")

    if not args.skip_crawl:
        if not os.path.isfile(BASE_CONFIG):
            print(f"\n[ERROR] base_config.py 없음 — MediaCrawler 설치 필요")
            print(f"        예상 경로: {BASE_CONFIG}")
            sys.exit(1)

        update_crawler_config(args.week, date_start, date_end)
        print(f"\n[1/2] MediaCrawler 크롤링 실행...")
        result = subprocess.run([sys.executable, "main.py"], cwd=MEDIACRAWLER_DIR)
        if result.returncode != 0:
            print(f"\n[ERROR] 크롤링 실패 (exit code {result.returncode})")
            print(f"        --skip-crawl 로 변환만 재시도 가능")
            sys.exit(1)

    if not os.path.isdir(data_dir):
        print(f"\n[ERROR] 데이터 폴더 없음: {data_dir}")
        sys.exit(1)

    print(f"\n[2/2] CSV 변환...")
    convert_to_csv(data_dir, out_dir)


if __name__ == "__main__":
    main()
