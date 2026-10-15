"""새 통합 버킷(dt-ane2-s3-prd-crawly) 이중 저장 — 옛 버킷(svc-fnf-cn-mkt-s3) 업로드가 성공한 뒤 같은 바이트를 한 번 더 올린다.

Airflow 의 dags/utils/crawly_s3_mirror.py (crawly-db 레포) 와 같은 규칙을 로컬 업로더에 옮긴 것이다.
도우인은 Airflow 가 이미 이중 저장 중이고, 샤오홍슈는 로컬 수동 업로더라 여기서 한다.

    s3_upload_xhs_post.py · s3_upload_xhs_account.py → upload_file() 안에서 mirror_upload()

- 경로: 옛 key 앞에 social/ 을 붙인다 (인프라팀 일괄 복사 구조와 같다)
    svc-fnf-cn-mkt-s3/xiaohongshu/X → dt-ane2-s3-prd-crawly/social/xiaohongshu/X
- 서명: 같은 통합 S3 API(aviyup1kyk /sign) · 같은 키(.env S3_API_KEY).
  새 버킷 허용 prefix 는 brand/ · social/ · entertainment/ 뿐이다.
- **실패해도 예외를 올리지 않는다.** 옛 버킷이 아직 원본이라 업로드 흐름을 깨지 않게 경고만 찍고,
  끝에 summary() 로 건수를 보여준다. 실패가 있으면 같은 명령으로 다시 돌리면 된다(덮어쓰기).
- 끄기: --no-mirror 또는 .env CRAWLY_S3_MIRROR=false
"""
import os
import sys
import time

import requests

MIRROR_BUCKET = "dt-ane2-s3-prd-crawly"
SIGN_ENDPOINT = "https://aviyup1kyk.execute-api.ap-northeast-2.amazonaws.com/prod/sign"
MIRROR_PREFIX = "social/"
MIRROR_ATTEMPTS = 3
MIRROR_BACKOFF_SEC = (2, 5)        # 1→2회, 2→3회 사이 대기 (SSLError 한 번으로 새 버킷에만 빠지는 걸 막는다)

_stats = {"ok": 0, "fail": 0}
_failed_keys = []


def enabled() -> bool:
    if "--no-mirror" in sys.argv:
        return False
    return (os.getenv("CRAWLY_S3_MIRROR", "true") or "true").strip().lower() != "false"


def mirror_key(old_key: str) -> str:
    """svc-fnf-cn-mkt-s3 key → 새 버킷 key."""
    return MIRROR_PREFIX + old_key.lstrip("/")


def _put_once(new_key: str, data: bytes) -> None:
    resp = requests.post(
        SIGN_ENDPOINT,
        headers={"x-api-key": os.getenv("S3_API_KEY"), "content-type": "application/json"},
        json={"bucket": MIRROR_BUCKET, "key": new_key, "action": "PUT_OBJECT"},
        timeout=30,
    )
    resp.raise_for_status()
    put = requests.put(resp.json()["url"], data=data, timeout=120)
    put.raise_for_status()


def mirror_upload(old_key: str, data: bytes):
    """새 버킷에 한 번 더 올린다. 성공 True · 실패 False · 꺼짐 None. 예외는 올리지 않는다."""
    if not enabled():
        return None
    new_key = mirror_key(old_key)
    try:
        for attempt in range(1, MIRROR_ATTEMPTS + 1):
            try:
                _put_once(new_key, data)
                break
            except Exception:
                if attempt == MIRROR_ATTEMPTS:
                    raise
                time.sleep(MIRROR_BACKOFF_SEC[min(attempt, len(MIRROR_BACKOFF_SEC)) - 1])
        _stats["ok"] += 1
        return True
    except Exception as exc:
        _stats["fail"] += 1
        _failed_keys.append(new_key)
        if _stats["fail"] <= 20:          # 대량 실패 시 로그 폭주 방지
            print(f"    [MIRROR 실패] s3://{MIRROR_BUCKET}/{new_key} — {type(exc).__name__}: {str(exc)[:200]}")
        return False


def summary() -> str:
    if not enabled():
        return f"새 버킷({MIRROR_BUCKET}): 건너뜀 (--no-mirror / CRAWLY_S3_MIRROR=false)"
    line = f"새 버킷({MIRROR_BUCKET}/{MIRROR_PREFIX}): 성공 {_stats['ok']:,} · 실패 {_stats['fail']:,}"
    if _stats["fail"]:
        line += "\n  → 실패분은 같은 명령으로 다시 돌리면 덮어쓴다 (DB 까지 다시 넣기 싫으면 --no-db)"
    return line
