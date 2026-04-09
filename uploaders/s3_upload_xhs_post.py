"""샤오홍슈 profile/post 데이터를 S3에 업로드하는 스크립트

업로드 대상:
1. 게시물 parquet → xiaohongshu/profile/post/p_year=YYYY/p_month=MM/p_day=DD/p_keyword={nickname}/{nickname}.parquet
2. 게시물 이미지  → xiaohongshu/profile/image/{nickname}/{note_id}/{note_id}_N.jpg

사용법:
    python s3_upload_xhs_post.py /path/to/red-weekly-260311              # 전체 업로드
    python s3_upload_xhs_post.py /path/to/red-weekly-260313 --dry-run   # 확인만
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

DRY_RUN = "--dry-run" in sys.argv


def parse_args():
    """폴더 경로 인자 파싱"""
    args = [a for a in sys.argv[1:] if not a.startswith("--")]
    if not args:
        print("사용법: python s3_upload_xhs_post.py <폴더경로> [--dry-run]")
        print("예시:   python s3_upload_xhs_post.py /path/to/red-weekly-260313")
        sys.exit(1)
    base_dir = os.path.dirname(os.path.abspath(__file__))
    data_dir = os.path.join(base_dir, args[0]) if not os.path.isabs(args[0]) else args[0]
    if not os.path.isdir(data_dir):
        print(f"오류: 폴더를 찾을 수 없습니다 — {data_dir}")
        sys.exit(1)
    return data_dir


def parse_date_from_dirname(data_dir):
    """폴더명에서 날짜 추출 (red-weekly-260311 → 2026, 03, 11)"""
    dir_name = os.path.basename(data_dir)
    match = re.search(r"(\d{6})", dir_name)
    if not match:
        print(f"오류: 폴더명에서 날짜를 추출할 수 없습니다 — {dir_name}")
        sys.exit(1)
    date_str = match.group(1)  # "260311"
    p_year = "20" + date_str[:2]  # "2026"
    p_month = date_str[2:4]       # "03"
    p_day = date_str[4:6]         # "11"
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


def build_post_parquet(notes, keyword, timestamp_str, profile_id):
    """notes.json을 S3 parquet 스키마(19컬럼)로 변환. keyword=PROFILE_ID(user_id)"""
    rows = []
    for note in notes:
        # 이미지 수 계산
        image_list = note.get("image_list", "")
        images_captured = len(image_list.split(",")) if image_list else 0

        # post_type 변환
        note_type = note.get("type", "")
        if note_type == "normal":
            post_type = "이미지"
        elif note_type == "video":
            post_type = "동영상"
        else:
            post_type = note_type

        # thumbnail_path — profile_id 기준 경로
        note_id = note.get("note_id", "")
        thumbnail_path = f"xiaohongshu/profile/image/{profile_id}/{note_id}/{note_id}_1.jpg" if note_id else ""

        # post_url
        note_url = note.get("note_url", "")
        post_url = note_url.split("?")[0] if note_url else ""

        rows.append({
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
    total_images = 0

    for i, folder in enumerate(folders, 1):
        notes_path = os.path.join(data_dir, folder, "notes.json")
        creator_path = os.path.join(data_dir, folder, "creator.json")
        if not os.path.isfile(notes_path):
            print(f"[{i}/{len(folders)}] {folder} — notes.json 없음, 건너뜀")
            skipped += 1
            continue

        with open(notes_path, "r", encoding="utf-8") as f:
            notes = json.load(f)

        if not notes:
            print(f"[{i}/{len(folders)}] {folder} — 게시물 0개, 건너뜀")
            skipped += 1
            continue

        # creator.json에서 user_id(PROFILE_ID) 추출
        profile_id = ""
        if os.path.isfile(creator_path):
            with open(creator_path, "r", encoding="utf-8") as f:
                creator = json.load(f)
            profile_id = creator.get("user_id", "")

        if not profile_id:
            # notes에서 user_id 가져오기 (fallback)
            profile_id = notes[0].get("user_id", folder)

        timestamp_str = datetime.now().strftime("%Y-%m-%d %H:%M:%S")

        # S3 경로 — p_keyword는 PROFILE_ID(user_id) 기준
        parquet_key = f"xiaohongshu/profile/post/p_year={p_year}/p_month={p_month}/p_day={p_day}/p_keyword={profile_id}/{profile_id}.parquet"

        print(f"[{i}/{len(folders)}] {folder} → {profile_id} ({len(notes)} posts)")

        try:
            # 1. 이미지 업로드 — 경로도 profile_id 기준
            img_count = 0
            for note in notes:
                note_id = note.get("note_id", "")
                note_dir = os.path.join(data_dir, folder, note_id)
                if not os.path.isdir(note_dir):
                    continue

                for fname in sorted(os.listdir(note_dir)):
                    if not fname.lower().endswith((".jpg", ".jpeg", ".png", ".webp")):
                        continue
                    # 파일명에서 번호 추출 (0.jpg → 1, 1.jpg → 2)
                    num_match = re.match(r"^(\d+)", fname)
                    if num_match:
                        idx = int(num_match.group(1)) + 1
                    else:
                        idx = img_count + 1

                    ext = os.path.splitext(fname)[1]
                    image_key = f"xiaohongshu/profile/image/{profile_id}/{note_id}/{note_id}_{idx}{ext}"

                    if DRY_RUN:
                        img_count += 1
                    else:
                        with open(os.path.join(note_dir, fname), "rb") as img_f:
                            upload_file(image_key, img_f.read())
                        img_count += 1

            total_images += img_count

            # 2. Parquet 업로드
            parquet_data = build_post_parquet(notes, folder, timestamp_str, profile_id)
            if parquet_data is None:
                print(f"    parquet 생성 실패, 건너뜀")
                skipped += 1
                continue

            if DRY_RUN:
                print(f"    [DRY] parquet → {parquet_key} ({len(parquet_data):,} bytes, {len(notes)} rows)")
                print(f"    [DRY] 이미지 {img_count}개")
            else:
                upload_file(parquet_key, parquet_data)
                print(f"    parquet 업로드 완료 ({len(parquet_data):,} bytes, {len(notes)} rows)")
                print(f"    이미지 {img_count}개 업로드 완료")

            success += 1

        except Exception as e:
            print(f"    [ERROR] {e}")
            fail += 1

    print(f"\n=== 완료: 성공 {success}, 실패 {fail}, 건너뜀 {skipped}, 이미지 총 {total_images}개 ===")


if __name__ == "__main__":
    main()
