"""도우인 account 데이터를 S3에 업로드하는 스크립트

업로드 대상:
1. 프로필 parquet → douyin/account/p_year=YYYY/p_month=MM/p_day=DD/p_keyword={nickname}/{nickname}.parquet
2. 프로필 이미지  → douyin/account/image/{nickname}/{nickname}.png

사용법:
    python s3_upload_douyin_account.py output/douyin-weekly-0216-v2              # 전체 업로드
    python s3_upload_douyin_account.py output/douyin-weekly-0223-v4 --dry-run   # 확인만
"""
import os
import sys
import json
import io
import re
import requests
import pyarrow as pa
import pyarrow.parquet as pq
from datetime import datetime, timedelta
from dotenv import load_dotenv

load_dotenv()

API_KEY = os.getenv("S3_API_KEY")
BASE_URL = "https://aviyup1kyk.execute-api.ap-northeast-2.amazonaws.com/prod"
BUCKET = "svc-fnf-cn-mkt-s3"
BASE_DIR = os.path.dirname(os.path.abspath(__file__))
REPO_ROOT = os.path.dirname(BASE_DIR)  # cn-social-listening/
SECUID_MAP_PATH = os.path.join(REPO_ROOT, "data", "douyin-secuid-map.json")

DRY_RUN = "--dry-run" in sys.argv


def parse_args():
    """폴더 경로 인자 파싱"""
    args = [a for a in sys.argv[1:] if not a.startswith("--")]
    if not args:
        print("사용법: python s3_upload_douyin_account.py <폴더경로> [--dry-run]")
        print("예시:   python s3_upload_douyin_account.py output/douyin-weekly-0216-v2")
        sys.exit(1)
    data_dir = os.path.join(BASE_DIR, args[0]) if not os.path.isabs(args[0]) else args[0]
    if not os.path.isdir(data_dir):
        print(f"오류: 폴더를 찾을 수 없습니다 — {data_dir}")
        sys.exit(1)
    return data_dir


def parse_date_from_dirname(data_dir):
    """폴더명에서 주차 시작일 추출.

    지원 패턴:
    - YYMMDD 6자리 (예: `douyin-weekly-260511-v5`, `_douyin_local_260511`) ← 우선
    - MMDD 4자리  (예: `douyin-weekly-0511-v5`) — 레거시, year=2026 가정
    """
    dir_name = os.path.basename(data_dir)

    m6 = re.search(r"(?<!\d)(\d{6})(?!\d)", dir_name)
    if m6:
        s = m6.group(1)
        return "20" + s[:2], s[2:4], s[4:6]

    m4 = re.search(r"(?<!\d)(\d{4})(?!\d)", dir_name)
    if m4:
        s = m4.group(1)
        return "2026", s[:2], s[2:]

    print(f"오류: 폴더명에서 날짜를 추출할 수 없습니다 — {dir_name}")
    sys.exit(1)


def load_secuid_map():
    """sec_uid 매핑 로드 (nickname → sec_uid)"""
    with open(SECUID_MAP_PATH, "r", encoding="utf-8") as f:
        data = json.load(f)
    return {k: v for k, v in data.items() if not k.startswith("_")}


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


def build_parquet(profile, timestamp_str, image_path, sec_uid):
    """data.json의 profile을 S3 parquet 스키마로 변환 (10컬럼).
    timestamp_str 은 caller(main)가 "데이터 주차의 운영 트리거 날짜(다음 월요일 06:00)"
    형식으로 만들어서 넘김 — SP_DM_PROFILE_W 윈도우와 매칭되도록.
    """
    data = {
        "nickname": [profile.get("nickname", "")],
        "douyin_id": [profile.get("uniqueId", "")],
        "fans": [profile.get("followerCount", 0)],
        "following": [profile.get("followingCount", 0)],
        "likes": [profile.get("totalFavorited", 0)],
        "ip_address": [""],
        "timestamp": [timestamp_str],
        "search_keyword": [profile.get("nickname", "")],
        "profile_image_path": [image_path],
        "sec_uid": [sec_uid],
    }

    schema = pa.schema([
        ("nickname", pa.string()),
        ("douyin_id", pa.string()),
        ("fans", pa.int64()),
        ("following", pa.int64()),
        ("likes", pa.int64()),
        ("ip_address", pa.string()),
        ("timestamp", pa.string()),
        ("search_keyword", pa.string()),
        ("profile_image_path", pa.string()),
        ("sec_uid", pa.string()),
    ])

    table = pa.table(data, schema=schema)
    buf = io.BytesIO()
    pq.write_table(table, buf)
    return buf.getvalue()


def download_avatar(url):
    """도우인 avatarUrl에서 이미지 다운로드"""
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

    secuid_map = load_secuid_map()
    print(f"sec_uid 매핑: {len(secuid_map)}개 로드\n")

    folders = sorted([
        f for f in os.listdir(data_dir)
        if os.path.isdir(os.path.join(data_dir, f))
    ])

    print(f"총 {len(folders)}개 계정 처리 예정\n")

    success = 0
    fail = 0
    skipped = 0

    for i, folder in enumerate(folders, 1):
        data_path = os.path.join(data_dir, folder, "data.json")
        if not os.path.isfile(data_path):
            print(f"[{i}/{len(folders)}] {folder} — data.json 없음, 건너뜀")
            skipped += 1
            continue

        with open(data_path, "r", encoding="utf-8") as f:
            data = json.load(f)

        profile = data.get("profile", {})
        avatar_url = profile.get("avatarUrl", "")
        sec_uid = secuid_map.get(folder, "")

        # timestamp 는 데이터 주차의 운영 트리거 날짜(다음 월요일 06:00) — SP_DM_PROFILE_W 윈도우와 매칭
        _data_start = datetime(int(p_year), int(p_month), int(p_day))
        _trigger = _data_start + timedelta(days=7)
        timestamp_str = _trigger.strftime("%Y-%m-%d 06:00:00")

        # S3 경로
        parquet_key = f"douyin/account/p_year={p_year}/p_month={p_month}/p_day={p_day}/p_keyword={folder}/{folder}.parquet"
        image_key = f"douyin/account/image/{folder}/{folder}.png"

        print(f"[{i}/{len(folders)}] {folder}")

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
            parquet_data = build_parquet(profile, timestamp_str, image_key, sec_uid)
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
