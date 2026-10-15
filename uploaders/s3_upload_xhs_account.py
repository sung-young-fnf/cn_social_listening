"""샤오홍슈 account 데이터를 S3에 업로드하는 스크립트

업로드 대상:
1. 프로필 parquet → xiaohongshu/account/p_year=YYYY/p_month=MM/p_day=DD/p_keyword={user_id}/{user_id}.parquet
2. 프로필 이미지  → xiaohongshu/account/image/{user_id}/{user_id}.png

사용법:
    python s3_upload_xhs_account.py MediaCrawler/output/red-weekly-260316              # 전체 업로드 + Crawly PG 적재
    python s3_upload_xhs_account.py MediaCrawler/output/red-weekly-260316 --dry-run    # 확인만
    python s3_upload_xhs_account.py MediaCrawler/output/red-weekly-260316 --db-only    # S3 는 건너뛰고 PG 적재만 (재시도 · 백필)
    python s3_upload_xhs_account.py MediaCrawler/output/red-weekly-260316 --no-db      # S3 만 (PG 적재 안 함)
    python s3_upload_xhs_account.py MediaCrawler/output/red-weekly-260316 --no-mirror  # 새 버킷(dt-ane2-s3-prd-crawly) 이중 저장 끔

S3 는 옛 버킷(svc-fnf-cn-mkt-s3)에 올린 뒤 같은 파일을 새 통합 버킷 dt-ane2-s3-prd-crawly/social/ 에도 올린다 (crawly_s3.py).

Crawly PG(social): S3 에 올린 parquet 과 같은 행을 stg_red_profile 에 넣고 CALL social.sp_dm_profile_w(그 주 일요일)
을 부른다 (uploaders/crawly_pg.py) — dm_profile_w 에 그 ISO 주(월~일) 1행이 UPSERT 된다.
--db-only 는 avatar 를 받지 않으므로 profile_image_path 를 S3 키 규칙으로 채운다(이미 올라가 있다고 가정).
"""
import os
import sys
import json
import io
import re
import requests
import urllib3
import pyarrow as pa
import pyarrow.parquet as pq
from datetime import datetime, timedelta
from dotenv import load_dotenv

import crawly_pg
import crawly_s3

load_dotenv()
urllib3.disable_warnings(urllib3.exceptions.InsecureRequestWarning)

API_KEY = os.getenv("S3_API_KEY")
BASE_URL = "https://aviyup1kyk.execute-api.ap-northeast-2.amazonaws.com/prod"
BUCKET = "svc-fnf-cn-mkt-s3"
BASE_DIR = os.path.dirname(os.path.abspath(__file__))

DRY_RUN = "--dry-run" in sys.argv
DB_ONLY = "--db-only" in sys.argv     # S3 업로드 없이 PG 적재만
NO_DB = "--no-db" in sys.argv         # PG 적재 안 함
# 검증 전용: avatar 다운로드만 시도 → 로컬 저장. S3 PUT/parquet 빌드 X.
AVATAR_TEST = "--avatar-test" in sys.argv

# avatar 다운로드용 헤더 (Referer는 xhscdn 계열 검증 통과용)
_AVATAR_HEADERS = {
    "Referer": "https://www.xiaohongshu.com/",
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/120.0.0.0 Safari/537.36"
    ),
}


def _build_oxylabs_proxy_url():
    """Oxylabs KR Residential proxy URL 생성. .env에 자격증명 없으면 fail-closed.

    회사 IP 노출 정책 — avatar 다운로드도 Oxylabs 거쳐야 안전.
    """
    user = os.getenv("OXYLABS_USERNAME")
    pwd = os.getenv("OXYLABS_PASSWORD")
    if not user or not pwd:
        print("[FAIL] OXYLABS_USERNAME / OXYLABS_PASSWORD 환경변수 필수.")
        print("       .env에 자격증명 박은 후 재실행. (회사 IP 보호 정책)")
        sys.exit(1)
    country = os.getenv("OXYLABS_COUNTRY", "kr")
    if "-cc-" in user:
        username = user
    else:
        username = f"{user}-cc-{country}"
    host = os.getenv("OXYLABS_HOST", "pr.oxylabs.io")
    port = os.getenv("OXYLABS_PORT", "7777")
    return f"http://{username}:{pwd}@{host}:{port}"


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
    # 전환 전 이중 저장: 옛 버킷 성공 뒤 새 통합 버킷 social/ 에도 (실패해도 예외 없음 — crawly_s3.py)
    crawly_s3.mirror_upload(s3_key, data)


def build_profile_record(creator, timestamp_str, image_path):
    """creator.json을 S3 parquet 스키마의 1행 dict 로 변환.
    같은 행이 parquet(build_parquet)과 Crawly PG 스테이징(stg_red_profile)에 들어간다.
    gender는 int(1/2/0)로 박혀오고, tag_list는 list[dict]라
    schema(string) 매핑을 위해 직렬화/문자열화 필요.
    """
    gender_raw = creator.get("gender", "")
    gender_str = "" if gender_raw == "" or gender_raw is None else str(gender_raw)

    tag_list_raw = creator.get("tag_list", [])
    if isinstance(tag_list_raw, (list, dict)):
        tag_list_str = json.dumps(tag_list_raw, ensure_ascii=False)
    else:
        tag_list_str = str(tag_list_raw) if tag_list_raw else ""

    user_id = creator.get("user_id", "")
    ip_location = creator.get("ip_location", "")
    interaction_val = parse_chinese_number(creator.get("interaction", 0))

    # 옛 키 alias 도 같이 박음 — STRG_SCL.RED_PROFILE External Table 가
    # GET($1, 'rednote_id'/'ip_address'/'liked_collect_count') 로 매핑되어 있어
    # 새 키만 박으면 PROFILE_ID/TOTAL_LIKE_CNT 가 NULL 로 들어감.
    # 동일 값을 옛/새 두 컬럼에 중복 저장.
    return {
        "profile_id": user_id,   # CHN_MKT.DM_PROFILE_W 프로시저 JOIN 키 (= user_id)
        "user_id": user_id,
        "rednote_id": user_id,   # alias for External Table compatibility
        "nickname": creator.get("nickname", ""),
        "gender": gender_str,
        "desc": creator.get("desc", ""),
        "ip_location": ip_location,
        "ip_address": ip_location,   # alias
        "fans": parse_chinese_number(creator.get("fans", 0)),
        "following": parse_chinese_number(creator.get("follows", 0)),
        "interaction": interaction_val,
        "liked_collect_count": interaction_val,   # alias
        "tag_list": tag_list_str,
        "timestamp": timestamp_str,
        "profile_image_path": image_path,
    }


def build_parquet(record):
    """build_profile_record 의 1행 → parquet bytes."""
    data = {k: [v] for k, v in record.items()}

    schema = pa.schema([
        ("profile_id", pa.string()),
        ("user_id", pa.string()),
        ("rednote_id", pa.string()),
        ("nickname", pa.string()),
        ("gender", pa.string()),
        ("desc", pa.string()),
        ("ip_location", pa.string()),
        ("ip_address", pa.string()),
        ("fans", pa.int64()),
        ("following", pa.int64()),
        ("interaction", pa.int64()),
        ("liked_collect_count", pa.int64()),
        ("tag_list", pa.string()),
        ("timestamp", pa.string()),
        ("profile_image_path", pa.string()),
    ])

    table = pa.table(data, schema=schema)
    buf = io.BytesIO()
    pq.write_table(table, buf)
    return buf.getvalue()


def download_avatar(url, proxy_url=None):
    """샤오홍슈 avatar URL에서 이미지 다운로드.

    Oxylabs proxy 거치도록 변경 — 회사 IP 노출 방지.
    Referer 박기 — xhscdn 계열 검증 통과용.
    """
    try:
        kwargs = {
            "timeout": 30,
            "headers": _AVATAR_HEADERS,
            "verify": False,
        }
        if proxy_url:
            kwargs["proxies"] = {"http": proxy_url, "https": proxy_url}
        resp = requests.get(url, **kwargs)
        resp.raise_for_status()
        return resp.content
    except Exception as e:
        print(f"    [WARN] 아바타 다운로드 실패: {e}")
        return None


def main():
    data_dir = parse_args()
    p_year, p_month, p_day = parse_date_from_dirname(data_dir)

    # Oxylabs proxy URL — 모든 avatar 다운로드 시 적용 (fail-closed)
    proxy_url = _build_oxylabs_proxy_url()

    if AVATAR_TEST:
        print("=== AVATAR TEST 모드 (다운로드 검증만, S3 PUT/parquet X) ===\n")
    elif DRY_RUN:
        print("=== DRY RUN 모드 (실제 업로드 없음) ===\n")

    print(f"대상 폴더: {data_dir}")
    print(f"파티션: p_year={p_year}/p_month={p_month}/p_day={p_day}")
    print(f"proxy: {proxy_url.split('@')[-1]}")
    # 그 주차 일요일 — timestamp 와 sp_dm_profile_w 인자에 같이 쓴다 (아래 루프의 주석 참고)
    week_sunday = (datetime(int(p_year), int(p_month), int(p_day)) + timedelta(days=6)).date()
    use_db = not NO_DB and not AVATAR_TEST
    if use_db:
        print(f"PG:    stg_red_profile → CALL social.sp_dm_profile_w('{week_sunday}')"
              f"{' (DRY: 적재 안 함)' if DRY_RUN else ''}")
    else:
        print("PG:    건너뜀")
    if DB_ONLY:
        print("S3:    건너뜀 (--db-only)")
    print()

    folders = sorted([
        f for f in os.listdir(data_dir)
        if os.path.isdir(os.path.join(data_dir, f))
    ])

    print(f"총 {len(folders)}개 계정 처리 예정\n")

    # avatar 검증 결과 로컬 저장 경로
    avatar_test_dir = None
    if AVATAR_TEST:
        ts = datetime.now().strftime("%y%m%d_%H%M%S")
        avatar_test_dir = os.path.join(
            os.path.dirname(BASE_DIR), "output", f"avatar_test_{ts}"
        )
        os.makedirs(avatar_test_dir, exist_ok=True)
        print(f"[avatar-test] 로컬 저장 경로: {avatar_test_dir}\n")

    seen_user_ids = set()
    stg_rows = []   # S3 업로드에 성공한(또는 --db-only) 계정만 PG 에 넣는다
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

        # === AVATAR TEST 모드 — 다운로드만 + 로컬 저장 ===
        if AVATAR_TEST:
            if not avatar_url:
                print(f"[{i}/{len(folders)}] {folder} → {user_id} — avatar URL 없음, 건너뜀")
                skipped += 1
                continue
            print(f"[{i}/{len(folders)}] {folder} → {user_id}")
            img_data = download_avatar(avatar_url, proxy_url=proxy_url)
            if img_data:
                save_path = os.path.join(avatar_test_dir, f"{user_id}.png")
                with open(save_path, "wb") as f:
                    f.write(img_data)
                print(f"    ✓ 다운로드 OK ({len(img_data):,} bytes) → {save_path}")
                success += 1
            else:
                fail += 1
            continue

        # === 일반 모드 (DRY_RUN 또는 실제 업로드) ===
        # timestamp 는 "그 주차 마지막 일요일 12:00" 으로 박는다.
        # 운영 Airflow 가 UTC 일요일 21:00 트리거 → CURRENT_DATE()=일요일 → END_DT=Sun.
        # backfill 도 동일하게 END_DT=Sun, START_DT=Mon 으로 박히도록.
        # SP_DM_PROFILE_W('YYYY-MM-DD' = 그 주차 일요일) 으로 호출하면 윈도우 매칭.
        _data_start = datetime(int(p_year), int(p_month), int(p_day))   # 그 주차 월요일
        _data_end = _data_start + timedelta(days=6)                      # 그 주차 일요일
        timestamp_str = _data_end.strftime("%Y-%m-%d 12:00:00")

        # S3 경로 — user_id 기준
        parquet_key = f"xiaohongshu/account/p_year={p_year}/p_month={p_month}/p_day={p_day}/p_keyword={user_id}/{user_id}.parquet"
        image_key = f"xiaohongshu/account/image/{user_id}/{user_id}.png"

        print(f"[{i}/{len(folders)}] {folder} → {user_id}")

        try:
            # 1. 프로필 이미지 업로드
            if DB_ONLY:
                # avatar 를 다시 받지 않는다 — 앞선 실행에서 올라간 키 규칙을 그대로 쓴다
                image_key = image_key if avatar_url else ""
            elif avatar_url:
                if DRY_RUN:
                    print(f"    [DRY] 이미지 → {image_key}")
                else:
                    img_data = download_avatar(avatar_url, proxy_url=proxy_url)
                    if img_data:
                        upload_file(image_key, img_data)
                        print(f"    이미지 업로드 완료 ({len(img_data):,} bytes)")
                    else:
                        image_key = ""
            else:
                image_key = ""

            # 2. Parquet 업로드
            record = build_profile_record(creator, timestamp_str, image_key)
            parquet_data = build_parquet(record)
            if DRY_RUN:
                print(f"    [DRY] parquet → {parquet_key} ({len(parquet_data):,} bytes)")
            elif DB_ONLY:
                print("    S3 건너뜀 (--db-only)")
            else:
                upload_file(parquet_key, parquet_data)
                print(f"    parquet 업로드 완료 ({len(parquet_data):,} bytes)")

            if use_db:
                stg_rows.append(crawly_pg.profile_row(parquet_key, record, user_id))
            success += 1

        except Exception as e:
            print(f"    [ERROR] {e}")
            fail += 1

    print(f"\n=== 완료: 성공 {success}, 실패 {fail}, 건너뜀 {skipped} ===")
    if not (DRY_RUN or DB_ONLY or AVATAR_TEST):
        print(crawly_s3.summary())
    if AVATAR_TEST and avatar_test_dir:
        print(f"avatar 파일: {avatar_test_dir}")

    if not use_db:
        return
    if DRY_RUN:
        print(f"[DRY] PG: stg_red_profile {len(stg_rows):,}행 → CALL social.sp_dm_profile_w('{week_sunday}')")
        return
    if not stg_rows:
        print("PG: 넣을 행이 없어 건너뜀")
        return
    try:
        r = crawly_pg.load_profiles(stg_rows, week_sunday)
        print(f"PG 적재 완료: stg_red_profile +{r['stg_rows']:,}행 → sp_dm_profile_w('{r['end_dt']}') "
              f"· dm_profile_w(RED, end_dt={r['end_dt']}) 갱신 {r['dm_profile_w_touched']:,}행")
    except Exception as e:
        # S3 는 이미 올라갔다. 원인을 고친 뒤 --db-only 로 PG 만 다시 넣는다
        print(f"[PG ERROR] {type(e).__name__}: {e}")
        print(f"  → 롤백됨 (스테이징·dm_profile_w 변경 없음). 고친 뒤: python {os.path.basename(__file__)} {data_dir} --db-only")
        sys.exit(1)


if __name__ == "__main__":
    main()
