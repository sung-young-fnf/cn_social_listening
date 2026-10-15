"""샤오홍슈 업로더 → Crawly 통합 PG(social) 스테이징 적재 + 프로시저 호출.

도우인은 Airflow tg6(tasks/cn_social/tg6_crawly_pg_tasks.py)가 같은 일을 한다. 샤오홍슈는 로컬 수동
크롤러라 업로더가 S3 parquet 을 올린 뒤 **같은 행**을 스테이징에 넣고 프로시저를 부른다.

    게시물  s3_upload_xhs_post.py    → social.stg_red_profile_post → CALL social.sp_dw_post(기준일)
    프로필  s3_upload_xhs_account.py → social.stg_red_profile      → CALL social.sp_dm_profile_w(일요일)

- 스테이징 컬럼 = Snowflake 외부 테이블 FNF.STRG_SCL.RED_PROFILE(_POST) 대응 (crawly-db sql/social/002 · 004)
    file_name = parquet S3 키, crawl_ts = parquet timestamp, value = parquet 한 행 전체(jsonb)
- 스테이징은 append 전용이다. 같은 폴더를 다시 올리면 행이 한 번 더 쌓이지만, 프로시저가 게시물은
  unique_hash 별 MAX → GREATEST MERGE, 프로필은 주별 MAX UPSERT 라 결과는 같다.
- INSERT 와 CALL 을 한 트랜잭션으로 묶는다 — 중간에 실패하면 아무것도 남지 않는다.

env (.env): CRAWLY_HOST(운영 writer 엔드포인트), CRAWLY_SOCIAL_OPS_PASSWORD, (선택) CRAWLY_PORT=5432
계정 crawly_social_ops — 운영 Crawly 는 적재도 _ops 로 한다(노션 'crawling db' 기준, 2026-09-29). svc_* 는 조회 전용 앱(API) 몫.
"""
import datetime as dt
import json
import os

STG_POST_COLS = ["file_name", "crawl_ts", "value"]
STG_PROFILE_COLS = ["file_name", "profile_id", "profile_nm", "ip_address", "gender", "follower_cnt",
                    "following_cnt", "total_like_cnt", "profile_image_path", "crawl_ts", "value", "p_keyword"]


def _connect():
    import psycopg2   # --no-db · --dry-run 으로만 쓰는 환경에서도 업로더가 뜨게 지연 import

    missing = [k for k in ("CRAWLY_HOST", "CRAWLY_SOCIAL_OPS_PASSWORD") if not (os.getenv(k) or "").strip()]
    if missing:
        raise RuntimeError(f".env 에 {', '.join(missing)} 가 없다 (DB 적재를 건너뛰려면 --no-db)")
    return psycopg2.connect(
        host=os.environ["CRAWLY_HOST"].strip(),
        port=int(os.getenv("CRAWLY_PORT", "5432")),
        dbname="social",
        user="crawly_social_ops",
        password=os.environ["CRAWLY_SOCIAL_OPS_PASSWORD"].strip(),
        sslmode="require",
        connect_timeout=10,
        application_name="cn-social-listening/xhs-uploader",
    )


def post_rows(file_name, crawl_ts, parquet_rows):
    """build_post_parquet 이 만든 행 dict 목록 → stg_red_profile_post 행 튜플."""
    return [(file_name, crawl_ts, json.dumps(r, ensure_ascii=False)) for r in parquet_rows]


def profile_row(file_name, parquet_row, p_keyword):
    """build_parquet(account) 의 1행 dict → stg_red_profile 행 튜플.
    매핑은 Snowflake RED_PROFILE 외부 테이블과 같다: profile_id=rednote_id, total_like_cnt=liked_collect_count."""
    r = parquet_row
    return (
        file_name, r["rednote_id"], r["nickname"], r["ip_address"], r["gender"], r["fans"],
        r["following"], r["liked_collect_count"], r["profile_image_path"], r["timestamp"],
        json.dumps(r, ensure_ascii=False), p_keyword,
    )


def load_posts(rows, base_dt: dt.date) -> dict:
    """stg_red_profile_post append + CALL sp_dw_post(base_dt).

    sp_dw_post 는 crawl_ts 가 [base_dt-21일, base_dt 00:00] 인 행만 읽는다 → base_dt 는 crawl_ts 다음 날 이후여야 한다.
    """
    from psycopg2.extras import execute_values

    conn = _connect()
    try:
        with conn.cursor() as cur:
            execute_values(cur, f"INSERT INTO social.stg_red_profile_post ({', '.join(STG_POST_COLS)}) VALUES %s",
                           rows, page_size=500)
            cur.execute("CALL social.sp_dw_post(%s)", (base_dt,))
            # 프로시저는 건드린 행의 update_dttm 을 now() 로 둔다 — now() 는 트랜잭션 시작 시각이라 같은 값이다
            cur.execute("SELECT count(*), count(*) FILTER (WHERE create_dttm = now()) FROM social.dw_post "
                        "WHERE platform = 'RED' AND update_dttm = now()")
            touched, inserted = cur.fetchone()
        conn.commit()
        return {"stg_rows": len(rows), "dw_post_touched": touched, "dw_post_new": inserted, "base_dt": str(base_dt)}
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()


def load_profiles(rows, week_sunday: dt.date) -> dict:
    """stg_red_profile append + CALL sp_dm_profile_w(week_sunday) — 그 ISO 주(월~일) 1행 UPSERT."""
    from psycopg2.extras import execute_values

    conn = _connect()
    try:
        with conn.cursor() as cur:
            execute_values(cur, f"INSERT INTO social.stg_red_profile ({', '.join(STG_PROFILE_COLS)}) VALUES %s",
                           rows, page_size=500)
            cur.execute("CALL social.sp_dm_profile_w(%s)", (week_sunday,))
            cur.execute("SELECT count(*) FROM social.dm_profile_w WHERE platform = 'RED' AND end_dt = %s "
                        "AND update_dttm = now()", (week_sunday,))
            touched = cur.fetchone()[0]
        conn.commit()
        return {"stg_rows": len(rows), "dm_profile_w_touched": touched, "end_dt": str(week_sunday)}
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()
