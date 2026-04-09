"""샤오홍슈 account 데이터를 S3에 업로드하는 스크립트

업로드 대상:
1. 프로필 parquet → xiaohongshu/account/p_year=YYYY/p_month=MM/p_day=DD/p_keyword={user_id}/{user_id}.parquet
2. 프로필 이미지  → xiaohongshu/account/image/{user_id}/{user_id}.png

사용법:
    python s3_upload_xhs_account.py MediaCrawler/output/red-weekly-260316              # 전체 업로드
    python s3_upload_xhs_account.py MediaCrawler/output/red-weekly-260316 --dry-run    # 확인만
"""
import os
import sys
import json
import io
import re
import requests
import pyarrow as pa
import pyarrow.parquet as pq
from datetime import datetime
from dotenv import load_dotenv

load_dotenv()

API_KEY = os.getenv("S3_API_KEY")
BASE_URL = "https://aviyup1kyk.execute-api.ap-northeast-2.amazonaws.com/prod"
BUCKET = "svc-fnf-cn-mkt-s3"
BASE_DIR = os.path.dirname(os.path.abspath(__file__))

DRY_RUN = "--dry-run" in sys.argv


def parse_args():
    """폴더 경로 인자 파싱"""
    args = [a for a in sys.argv[1:] if not a.startswith("--")]
    if not args:
        print("사용법: python s3_upload_xhs_account.py <폴더경로> [--dry-run]")
        print("예시:   python s3_upload_xhs_account.py MediaCrawler/output/red-weekly-260316")
        sys.exit(1)
    data_dir = os.path.join(BASE_DIR, args[0]) if not os.path.isabs(args[0]) else args[0]
    if not os.path.isdir(data_dir):
        print(f"오류: 폴더를 찾을 수 없습니다 — {data_dir}")
        sys.exit(1)
    return data_dir


def parse_date_from_dirname(data_dir):
    """폴더명에서 날짜 추출 (red-weekly-260316 → 2026, 03, 16)"""
    dir_name = os.path.basename(data_dir)
    match = re.search(r"(\d{6})", dir_name)
    if not match:
        print(f"오류: 폴더명에서 날짜를 추출할 수 없습니다 — {dir_name}")
        sys.exit(1)
    date_str = match.group(1)
    p_year = "20" + date_str[:2]
    p_month = date_str[2:4]
    p_day = date_str[4:6]
    return p_year, p_month, p_day


def parse_chinese_number(text):
    """한자 숫자 표기를 정수로 변환 (10万+, 1.3万, 2345 등)"""
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


def get_presigned_url(s3_key):
    """PUT용 presigned URL 발급"""
    resp = requests.post(
        f"{BASE_URL}/sign",
        headers={"x-api-key": API_KEY, "content-type": "application/json"},
        json={"bucket": BUCKET, "key": s3_key, "action": "PUT_OBJECT"},
        timeout=30,
    )
    resp.raise_for_status()
    return resp.json()["url"]


def upload_file(s3_key, data):
    """presigned URL로 파일 업로드"""
    url = get_presigned_url(s3_key)
    resp = requests.put(url, data=data, timeout=120)
    resp.raise_for_status()


def build_parquet(creator, timestamp_str, image_path):
    """creator.json을 S3 parquet 스키마로 변환"""
    data = {
        "user_id": [creator.get("user_id", "")],
        "nickname": [creator.get("nickname", "")],
        "gender": [creator.get("gender", "")],
        "desc": [creator.get("desc", "")],
        "ip_location": [creator.get("ip_location", "")],
        "fans": [parse_chinese_number(creator.get("fans", 0))],
        "following": [parse_chinese_number(creator.get("follows", 0))],
        "interaction": [parse_chinese_number(creator.get("interaction", 0))],
        "tag_list": [creator.get("tag_list", "")],
        "timestamp": [timestamp_str],
        "profile_image_path": [image_path],
    }

    schema = pa.schema([
        ("user_id", pa.string()),
        ("nickname", pa.string()),
        ("gender", pa.string()),
        ("desc", pa.string()),
        ("ip_location", pa.string()),
        ("fans", pa.int64()),
        ("following", pa.int64()),
        ("interaction", pa.int64()),
        ("tag_list", pa.string()),
        ("timestamp", pa.string()),
        ("profile_image_path", pa.string()),
    ])

    table = pa.table(data, schema=schema)
    buf = io.BytesIO()
    pq.write_table(table, buf)
    return buf.getvalue()


def download_avatar(url):
    """샤오홍슈 avatar URL에서 이미지 다운로드"""
    try:
        resp = requests.get(url, timeout=30)
        resp.raise_for_status()
        return resp.content
    except Exception as e:
        print(f"    [WARN] 아바타 다운로드 실패: {e}")
        return None


def main():
    data_dir = parse_args()
    p_year, p_month, p_day = parse_date_from_dirname(data_dir)

    if DRY_RUN:
        print("=== DRY RUN 모드 (실제 업로드 없음) ===\n")

    print(f"대상 폴더: {data_dir}")
    print(f"파티션: p_year={p_year}/p_month={p_month}/p_day={p_day}\n")

    folders = sorted([
        f for f in os.listdir(data_dir)
        if os.path.isdir(os.path.join(data_dir, f))
    ])

    print(f"총 {len(folders)}개 계정 처리 예정\n")

    seen_user_ids = set()
    success = 0
    fail = 0
    skipped = 0

    for i, folder in enumerate(folders, 1):
        creator_path = os.path.join(data_dir, folder, "creator.json")
        if not os.path.isfile(creator_path):
            print(f"[{i}/{len(folders)}] {folder} — creator.json 없음, 건너뜀")
            skipped += 1
            continue

        with open(creator_path, "r", encoding="utf-8") as f:
            creator = json.load(f)

        user_id = creator.get("user_id", "")
        if not user_id:
            print(f"[{i}/{len(folders)}] {folder} — user_id 없음, 건너뜀")
            skipped += 1
            continue

        if user_id in seen_user_ids:
            print(f"[{i}/{len(folders)}] {folder} — user_id 중복({user_id}), 건너뜀")
            skipped += 1
            continue
        seen_user_ids.add(user_id)

        avatar_url = creator.get("avatar", "")
        timestamp_str = datetime.now().strftime("%Y-%m-%d %H:%M:%S")

        # S3 경로 — user_id 기준
        parquet_key = f"xiaohongshu/account/p_year={p_year}/p_month={p_month}/p_day={p_day}/p_keyword={user_id}/{user_id}.parquet"
        image_key = f"xiaohongshu/account/image/{user_id}/{user_id}.png"

        print(f"[{i}/{len(folders)}] {folder} → {user_id}")

        try:
            # 1. 프로필 이미지 업로드
            if avatar_url:
                if DRY_RUN:
                    print(f"    [DRY] 이미지 → {image_key}")
                else:
                    img_data = download_avatar(avatar_url)
                    if img_data:
                        upload_file(image_key, img_data)
                        print(f"    이미지 업로드 완료 ({len(img_data):,} bytes)")
                    else:
                        image_key = ""
            else:
                image_key = ""

            # 2. Parquet 업로드
            parquet_data = build_parquet(creator, timestamp_str, image_key)
            if DRY_RUN:
                print(f"    [DRY] parquet → {parquet_key} ({len(parquet_data):,} bytes)")
            else:
                upload_file(parquet_key, parquet_data)
                print(f"    parquet 업로드 완료 ({len(parquet_data):,} bytes)")

            success += 1

        except Exception as e:
            print(f"    [ERROR] {e}")
            fail += 1

    print(f"\n=== 완료: 성공 {success}, 실패 {fail}, 건너뜀 {skipped} ===")


if __name__ == "__main__":
    main()
