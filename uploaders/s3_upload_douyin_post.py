"""도우인 profile/post 데이터를 S3에 업로드하는 스크립트

업로드 대상:
1. 게시물 parquet → douyin/profile/post/p_year=YYYY/p_month=MM/p_day=DD/p_keyword={sec_uid}/{sec_uid}.parquet
2. 게시물 썸네일  → douyin/profile/image/{sec_uid}/{aweme_id}/{aweme_id}_1.jpg

사용법:
    python s3_upload_douyin_post.py output/douyin-weekly-0216-v2              # 전체 업로드
    python s3_upload_douyin_post.py output/douyin-weekly-0316-v5 --dry-run   # 확인만
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
        print("사용법: python s3_upload_douyin_post.py <폴더경로> [--dry-run]")
        print("예시:   python s3_upload_douyin_post.py output/douyin-weekly-0216-v2")
        sys.exit(1)
    data_dir = os.path.join(BASE_DIR, args[0]) if not os.path.isabs(args[0]) else args[0]
    if not os.path.isdir(data_dir):
        print(f"오류: 폴더를 찾을 수 없습니다 — {data_dir}")
        sys.exit(1)
    return data_dir


def parse_date_from_dirname(data_dir):
    """폴더명에서 주차 시작일 추출 (douyin-weekly-MMDD-vN → year, month, day)"""
    dir_name = os.path.basename(data_dir)
    match = re.search(r"(\d{4})", dir_name)
    if not match:
        print(f"오류: 폴더명에서 날짜를 추출할 수 없습니다 — {dir_name}")
        sys.exit(1)
    date_part = match.group(1)
    return "2026", date_part[:2], date_part[2:]



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


def download_image(url):
    """URL에서 이미지 다운로드"""
    try:
        resp = requests.get(url, timeout=30)
        resp.raise_for_status()
        return resp.content
    except Exception as e:
        return None


def build_post_parquet(posts, profile_id, timestamp_str):
    """posts를 S3 parquet 스키마(19컬럼)로 변환. keyword=PROFILE_ID(uniqueId)"""
    rows = []
    for post in posts:
        stats = post.get("statistics", {})
        aweme_id = post.get("awemeId", "")

        # post_date — createDate 또는 createTime에서 추출
        create_date = post.get("createDate", "")
        if create_date:
            post_date = create_date[:10]  # "2026-02-17T07:33:58.000Z" → "2026-02-17"
        else:
            ct = post.get("createTime", 0)
            post_date = datetime.fromtimestamp(ct).strftime("%Y-%m-%d") if ct else ""

        # thumbnail_path
        thumbnail_path = f"douyin/profile/image/{profile_id}/{aweme_id}/{aweme_id}_1.jpg" if aweme_id else ""

        # post_url
        video_url = post.get("videoUrl", "")
        post_url = video_url.split("?")[0] if video_url else ""

        rows.append({
            "keyword": profile_id,
            "author": post.get("author", ""),
            "content": post.get("desc", ""),
            "likes": stats.get("likes", 0),
            "stars": stats.get("favorites", 0),
            "comments": stats.get("comments", 0),
            "images_captured": 0,
            "post_date": post_date,
            "location": "",
            "post_type": "동영상",
            "recommendations": 0,
            "shares": stats.get("shares", 0),
            "key": f"{post.get('author', '')}__{stats.get('likes', 0)}",
            "timestamp": timestamp_str,
            "note_title": "",
            "note_text": post.get("desc", ""),
            "unique_hash": aweme_id,
            "thumbnail_path": thumbnail_path,
            "post_url": post_url,
        })

    if not rows:
        return None

    schema = pa.schema([
        ("keyword", pa.string()),
        ("author", pa.string()),
        ("content", pa.string()),
        ("likes", pa.int64()),
        ("stars", pa.int64()),
        ("comments", pa.int64()),
        ("images_captured", pa.int64()),
        ("post_date", pa.string()),
        ("location", pa.string()),
        ("post_type", pa.string()),
        ("recommendations", pa.int64()),
        ("shares", pa.int64()),
        ("key", pa.string()),
        ("timestamp", pa.string()),
        ("note_title", pa.string()),
        ("note_text", pa.string()),
        ("unique_hash", pa.string()),
        ("thumbnail_path", pa.string()),
        ("post_url", pa.string()),
    ])

    table = pa.table({k: [r[k] for r in rows] for k in rows[0]}, schema=schema)
    buf = io.BytesIO()
    pq.write_table(table, buf)
    return buf.getvalue()


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

    success = 0
    fail = 0
    skipped = 0
    total_thumbs = 0

    for i, folder in enumerate(folders, 1):
        data_path = os.path.join(data_dir, folder, "data.json")
        if not os.path.isfile(data_path):
            print(f"[{i}/{len(folders)}] {folder} — data.json 없음, 건너뜀")
            skipped += 1
            continue

        with open(data_path, "r", encoding="utf-8") as f:
            data = json.load(f)

        posts = data.get("posts", [])
        if not posts:
            print(f"[{i}/{len(folders)}] {folder} — 게시물 0개, 건너뜀")
            skipped += 1
            continue

        # PROFILE_ID = uniqueId (MST_PROFILE 엑셀과 일치)
        profile = data.get("profile", {})
        unique_id = profile.get("uniqueId", "")
        if not unique_id:
            unique_id = folder  # fallback: 폴더명
        profile_id = unique_id

        timestamp_str = datetime.now().strftime("%Y-%m-%d %H:%M:%S")

        # S3 경로 — p_keyword는 PROFILE_ID(uniqueId) 기준
        parquet_key = f"douyin/profile/post/p_year={p_year}/p_month={p_month}/p_day={p_day}/p_keyword={profile_id}/{profile_id}.parquet"

        print(f"[{i}/{len(folders)}] {folder} → {profile_id} ({len(posts)} posts)")

        try:
            # 1. 썸네일 이미지 업로드
            thumb_count = 0
            for post in posts:
                aweme_id = post.get("awemeId", "")
                cover_url = post.get("coverUrl", "")
                if not aweme_id or not cover_url:
                    continue

                image_key = f"douyin/profile/image/{profile_id}/{aweme_id}/{aweme_id}_1.jpg"

                if DRY_RUN:
                    thumb_count += 1
                else:
                    img_data = download_image(cover_url)
                    if img_data:
                        upload_file(image_key, img_data)
                        thumb_count += 1

            total_thumbs += thumb_count

            # 2. Parquet 업로드
            parquet_data = build_post_parquet(posts, profile_id, timestamp_str)
            if parquet_data is None:
                print(f"    parquet 생성 실패, 건너뜀")
                skipped += 1
                continue

            if DRY_RUN:
                print(f"    [DRY] parquet → {parquet_key} ({len(parquet_data):,} bytes, {len(posts)} rows)")
                print(f"    [DRY] 썸네일 {thumb_count}개")
            else:
                upload_file(parquet_key, parquet_data)
                print(f"    parquet 업로드 완료 ({len(parquet_data):,} bytes, {len(posts)} rows)")
                print(f"    썸네일 {thumb_count}개 업로드 완료")

            success += 1

        except Exception as e:
            print(f"    [ERROR] {e}")
            fail += 1

    print(f"\n=== 완료: 성공 {success}, 실패 {fail}, 건너뜀 {skipped}, 썸네일 총 {total_thumbs}개 ===")


if __name__ == "__main__":
    main()
