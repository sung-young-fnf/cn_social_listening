"""샤오홍슈 profile/post 데이터를 S3에 업로드하는 스크립트

업로드 대상:
1. 게시물 parquet → xiaohongshu/profile/post/p_year=YYYY/p_month=MM/p_day=DD/p_keyword={nickname}/{nickname}.parquet
2. 게시물 이미지  → xiaohongshu/profile/image/{nickname}/{note_id}/{note_id}_N.jpg

사용법:
    python s3_upload_xhs_post.py /path/to/red-weekly-260311              # 전체 업로드 + Crawly PG 적재
    python s3_upload_xhs_post.py /path/to/red-weekly-260313 --dry-run   # 확인만
    python s3_upload_xhs_post.py /path/to/red-weekly-260313 --skip-images  # parquet 만 (재적재용)
    python s3_upload_xhs_post.py /path/to/red-weekly-260313 --db-only   # S3 는 건너뛰고 PG 적재만 (재시도 · 백필)
    python s3_upload_xhs_post.py /path/to/red-weekly-260313 --no-db     # S3 만 (PG 적재 안 함)
    python s3_upload_xhs_post.py /path/to/red-weekly-260313 --no-mirror # 새 버킷(dt-ane2-s3-prd-crawly) 이중 저장 끔

S3 는 옛 버킷(svc-fnf-cn-mkt-s3)에 올린 뒤 같은 파일을 새 통합 버킷 dt-ane2-s3-prd-crawly/social/ 에도 올린다 (crawly_s3.py).

Crawly PG(social): profile 모드에서 S3 에 올린 parquet 과 같은 행을 stg_red_profile_post 에 넣고
CALL social.sp_dw_post(기준일) 을 부른다 (uploaders/crawly_pg.py). 기준일 = timestamp(그 주 일요일 12:00)
다음 날 — sp_dw_post 는 기준일 00:00 이전 crawl_ts 만 읽기 때문이다. keyword 모드는 스테이징이 없어 적재하지 않는다.

post_date: notes.json 의 time 이 빈 값이면(detail 미진입/실패 노트) note_id 앞 8자리 hex 로
추정해 채운다. 추정값은 detail 게시일과 ~89% 같은 날, 나머지는 대부분 하루 이르다.
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

import crawly_pg
import crawly_s3

load_dotenv()

API_KEY = os.getenv("S3_API_KEY")
BASE_URL = "https://aviyup1kyk.execute-api.ap-northeast-2.amazonaws.com/prod"
BUCKET = "svc-fnf-cn-mkt-s3"

DRY_RUN = "--dry-run" in sys.argv
SKIP_IMAGES = "--skip-images" in sys.argv
DB_ONLY = "--db-only" in sys.argv     # S3 업로드 없이 PG 적재만
NO_DB = "--no-db" in sys.argv         # PG 적재 안 함


def _extract_flag_value(name: str, default=None):
    """sys.argv 에서 '--flag value' 또는 '--flag=value' 형식 추출."""
    eq_prefix = f"{name}="
    for i, a in enumerate(sys.argv[1:], start=1):
        if a == name and i + 1 < len(sys.argv):
            return sys.argv[i + 1]
        if a.startswith(eq_prefix):
            return a[len(eq_prefix):]
    return default


def parse_args():
    """폴더 경로 인자 파싱 + 모드 자동 감지."""
    positional = [a for a in sys.argv[1:] if not a.startswith("--")]
    # --mode 의 값(다음 토큰) 이 positional 로 들어오면 제거
    mode_value = _extract_flag_value("--mode")
    if mode_value and positional and positional[0] == mode_value:
        positional = positional[1:]

    if not positional:
        print("사용법: python s3_upload_xhs_post.py <폴더경로> [--mode profile|keyword] [--dry-run]")
        print("예시:")
        print("  python s3_upload_xhs_post.py /path/to/red-weekly-260311          (profile 모드 자동 감지)")
        print("  python s3_upload_xhs_post.py /path/to/red-keyword-260522         (keyword 모드 자동 감지)")
        print("  python s3_upload_xhs_post.py <폴더> --mode keyword               (명시)")
        sys.exit(1)

    base_dir = os.path.dirname(os.path.abspath(__file__))
    data_dir = os.path.join(base_dir, positional[0]) if not os.path.isabs(positional[0]) else positional[0]
    if not os.path.isdir(data_dir):
        print(f"오류: 폴더를 찾을 수 없습니다 — {data_dir}")
        sys.exit(1)

    # 모드 결정 — 명시 우선, 없으면 폴더명에 'keyword' 포함 여부로 자동
    folder_basename = os.path.basename(data_dir.rstrip("/\\"))
    if mode_value in ("profile", "keyword"):
        mode = mode_value
    elif "keyword" in folder_basename.lower():
        mode = "keyword"
    else:
        mode = "profile"

    return data_dir, mode


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


def estimate_post_date(note_id):
    """note_id 앞 8자리 hex(ObjectId 생성 시각) → yyyy-mm-dd. 범위 밖/파싱 불가 시 ""."""
    if not note_id or len(note_id) < 8:
        return ""
    try:
        ts = int(note_id[:8], 16)
    except ValueError:
        return ""
    # 2013-01-01(xhs 서비스 시작) ~ 2030-01-01. 2018년 고정 노트가 매주 섞여 들어오므로 2020 하한은 안 됨
    if not (1356998400 <= ts <= 1893456000):
        return ""
    return datetime.fromtimestamp(ts).strftime("%Y-%m-%d")


# xhs 정식 토픽 태그는 본문에 '#태그명[话题]#' 형태로 박힌다.
# [话题] 없는 '#...#' 는 강조용 텍스트라 제외.
_HASHTAG_RE = re.compile(r"#([^#\[\]\n]{1,50}?)\[话题\]#")


def extract_hashtags(desc):
    """본문에서 토픽 태그 추출 (등장 순서 유지, 중복 제거). desc 없으면 []."""
    if not desc:
        return []
    return list(dict.fromkeys(t.strip() for t in _HASHTAG_RE.findall(desc) if t.strip()))


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
    # 전환 전 이중 저장: 옛 버킷 성공 뒤 새 통합 버킷 social/ 에도 (실패해도 예외 없음 — crawly_s3.py)
    crawly_s3.mirror_upload(s3_key, data)


def build_post_rows(notes, keyword_col_val, timestamp_str, image_path_prefix):
    """notes.json을 S3 parquet 스키마(20컬럼 = 기존 19 + hashtags list<string>)의 행 dict 목록으로 변환.
    같은 행이 parquet(build_post_parquet)과 Crawly PG 스테이징(value jsonb)에 들어간다.

    Args:
        keyword_col_val: parquet 의 keyword 컬럼에 박을 값
            - profile 모드: user_id (24-hex)
            - keyword 모드: 검색 키워드 (예: 'discoveryexpedition')
        image_path_prefix: thumbnail_path 의 prefix
            - profile 모드: 'xiaohongshu/profile/image/{user_id}'
            - keyword 모드: 'xiaohongshu/keyword/image/{search_keyword}'
    """
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

        # thumbnail_path — 모드에 따라 prefix 다름
        note_id = note.get("note_id", "")
        thumbnail_path = f"{image_path_prefix}/{note_id}/{note_id}_1.jpg" if note_id else ""

        # post_url
        note_url = note.get("note_url", "")
        post_url = note_url.split("?")[0] if note_url else ""

        rows.append({
            "keyword": keyword_col_val,
            "author": note.get("nickname", ""),
            "content": note.get("desc", ""),
            "likes": parse_chinese_number(note.get("liked_count", 0)),
            "stars": parse_chinese_number(note.get("collected_count", 0)),
            "comments": parse_chinese_number(note.get("comment_count", 0)),
            "images_captured": images_captured,
            "post_date": note.get("time", "") or estimate_post_date(note_id),
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
            "hashtags": extract_hashtags(note.get("desc", "")),
        })
    return rows


def build_post_parquet(rows):
    """build_post_rows 의 행 → parquet bytes. 행이 없으면 None."""
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
        ("hashtags", pa.list_(pa.string())),
    ])

    table = pa.table({k: [r[k] for r in rows] for k in rows[0]}, schema=schema)
    buf = io.BytesIO()
    pq.write_table(table, buf)
    return buf.getvalue()


def main():
    data_dir, mode = parse_args()
    p_year, p_month, p_day = parse_date_from_dirname(data_dir)

    if DRY_RUN:
        print("=== DRY RUN 모드 (실제 업로드 없음) ===\n")

    # timestamp 박는 방식 — 모드별
    if mode == "profile":
        # 그 주차 마지막 일요일 12:00 (운영 표준 END_DT=Sun 패턴과 일치)
        _data_start = datetime(int(p_year), int(p_month), int(p_day))
        _data_end = _data_start + timedelta(days=6)
        timestamp_str = _data_end.strftime("%Y-%m-%d 12:00:00")
    else:  # keyword
        # 그 날(p_day) 12:00 — keyword 는 그날 단위 적재 (주차 아님)
        timestamp_str = f"{p_year}-{p_month}-{p_day} 12:00:00"

    label = "샤오홍수 인플루언서 게시물" if mode == "profile" else "샤오홍수 키워드 검색 게시물"
    print(f"대상 폴더: {data_dir}")
    print(f"모드:      {mode}  ({label})")
    print(f"파티션:    p_year={p_year}/p_month={p_month}/p_day={p_day}")
    print(f"timestamp: {timestamp_str}")
    print(f"이미지:    {'건너뜀 (--skip-images)' if SKIP_IMAGES or DB_ONLY else '업로드'}")
    use_db = mode == "profile" and not NO_DB
    # sp_dw_post 는 crawl_ts ≤ 기준일 00:00 만 읽는다 → crawl_ts(일요일 12:00) 다음 날을 기준일로
    db_base_dt = (datetime.strptime(timestamp_str, "%Y-%m-%d %H:%M:%S") + timedelta(days=1)).date()
    if use_db:
        print(f"PG:        stg_red_profile_post → CALL social.sp_dw_post('{db_base_dt}')"
              f"{' (DRY: 적재 안 함)' if DRY_RUN else ''}")
    else:
        print(f"PG:        건너뜀 ({'--no-db' if NO_DB else 'keyword 모드는 스테이징이 없다'})")
    if DB_ONLY:
        print("S3:        건너뜀 (--db-only)")
    print()

    folders = sorted([
        f for f in os.listdir(data_dir)
        if os.path.isdir(os.path.join(data_dir, f))
    ])

    unit_label = "계정" if mode == "profile" else "키워드"
    print(f"총 {len(folders)}개 {unit_label} 처리 예정\n")

    success = 0
    fail = 0
    skipped = 0
    total_images = 0
    stg_rows = []   # S3 업로드에 성공한(또는 --db-only) 계정의 행만 PG 에 넣는다

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

        # 모드별 식별자/경로 결정
        if mode == "profile":
            # creator.json 에서 user_id 추출 → PROFILE_ID
            profile_id = ""
            if os.path.isfile(creator_path):
                with open(creator_path, "r", encoding="utf-8") as f:
                    creator = json.load(f)
                profile_id = creator.get("user_id", "")
            if not profile_id:
                profile_id = notes[0].get("user_id", folder)   # fallback
            partition_keyword = profile_id          # S3 partition key (p_keyword=)
            keyword_col_val = profile_id            # parquet 'keyword' 컬럼
            image_path_prefix = f"xiaohongshu/profile/image/{profile_id}"
            parquet_key = (
                f"xiaohongshu/profile/post/p_year={p_year}/p_month={p_month}/p_day={p_day}/"
                f"p_keyword={profile_id}/{profile_id}.parquet"
            )
            display_target = f"{folder} → {profile_id}"
        else:  # keyword
            # creator.json 없음. folder 자체가 검색 키워드
            search_keyword = folder
            partition_keyword = search_keyword
            keyword_col_val = search_keyword
            image_path_prefix = f"xiaohongshu/keyword/image/{search_keyword}"
            parquet_key = (
                f"xiaohongshu/keyword/post/p_year={p_year}/p_month={p_month}/p_day={p_day}/"
                f"p_keyword={search_keyword}/{search_keyword}.parquet"
            )
            display_target = search_keyword

        print(f"[{i}/{len(folders)}] {display_target} ({len(notes)} posts)")

        try:
            # 1. 이미지 업로드 — 모드별 prefix
            img_count = 0
            for note in ([] if SKIP_IMAGES or DB_ONLY else notes):
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
                    image_key = f"{image_path_prefix}/{note_id}/{note_id}_{idx}{ext}"

                    if DRY_RUN:
                        img_count += 1
                    else:
                        with open(os.path.join(note_dir, fname), "rb") as img_f:
                            upload_file(image_key, img_f.read())
                        img_count += 1

            total_images += img_count

            # 2. Parquet 업로드
            estimated = sum(1 for n in notes if not n.get("time"))
            if estimated:
                print(f"    post_date 빈 값 {estimated}개 → note_id 로 추정")
            rows = build_post_rows(notes, keyword_col_val, timestamp_str, image_path_prefix)
            parquet_data = build_post_parquet(rows)
            if parquet_data is None:
                print(f"    parquet 생성 실패, 건너뜀")
                skipped += 1
                continue

            if DRY_RUN:
                print(f"    [DRY] parquet → {parquet_key} ({len(parquet_data):,} bytes, {len(notes)} rows)")
                print(f"    [DRY] 이미지 {img_count}개")
            elif DB_ONLY:
                print(f"    S3 건너뜀 (--db-only) — PG 적재 대상 {len(rows)} rows")
            else:
                upload_file(parquet_key, parquet_data)
                print(f"    parquet 업로드 완료 ({len(parquet_data):,} bytes, {len(notes)} rows)")
                print(f"    이미지 {img_count}개 업로드 완료")

            if use_db:
                stg_rows.extend(crawly_pg.post_rows(parquet_key, timestamp_str, rows))
            success += 1

        except Exception as e:
            print(f"    [ERROR] {e}")
            fail += 1

    print(f"\n=== 완료: 성공 {success}, 실패 {fail}, 건너뜀 {skipped}, 이미지 총 {total_images}개 ===")
    if not (DRY_RUN or DB_ONLY):
        print(crawly_s3.summary())

    if not use_db:
        return
    if DRY_RUN:
        print(f"[DRY] PG: stg_red_profile_post {len(stg_rows):,}행 → CALL social.sp_dw_post('{db_base_dt}')")
        return
    if not stg_rows:
        print("PG: 넣을 행이 없어 건너뜀")
        return
    try:
        r = crawly_pg.load_posts(stg_rows, db_base_dt)
        print(f"PG 적재 완료: stg_red_profile_post +{r['stg_rows']:,}행 → sp_dw_post('{r['base_dt']}') "
              f"· dw_post(RED) 갱신 {r['dw_post_touched']:,}행 (신규 {r['dw_post_new']:,})")
    except Exception as e:
        # S3 는 이미 올라갔다. 원인을 고친 뒤 --db-only 로 PG 만 다시 넣는다
        print(f"[PG ERROR] {type(e).__name__}: {e}")
        print(f"  → 롤백됨 (스테이징·dw_post 변경 없음). 고친 뒤: python {os.path.basename(__file__)} {data_dir} --db-only")
        sys.exit(1)


if __name__ == "__main__":
    main()
