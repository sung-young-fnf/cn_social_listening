"""S3 버킷 내 파일 목록 조회 스크립트

사용법:
    python s3_list_bucket.py                          # 루트 조회
    python s3_list_bucket.py douyin/account/           # 특정 경로 조회
    python s3_list_bucket.py douyin/keyword/video/ 20  # 최대 20개
"""
import sys
import os
import requests
from dotenv import load_dotenv

load_dotenv()

API_KEY = os.getenv("S3_API_KEY")
BASE_URL = "https://aviyup1kyk.execute-api.ap-northeast-2.amazonaws.com/prod"
BUCKET = "svc-fnf-cn-mkt-s3"


def list_objects(prefix="", max_keys=1000):
    resp = requests.get(
        f"{BASE_URL}/list",
        headers={"x-api-key": API_KEY},
        params={
            "bucket": BUCKET,
            "prefix": prefix,
            "maxKeys": max_keys,
        },
        timeout=30,
    )
    resp.raise_for_status()
    return resp.json()


if __name__ == "__main__":
    prefix = sys.argv[1] if len(sys.argv) > 1 else ""
    max_keys = int(sys.argv[2]) if len(sys.argv) > 2 else 100

    print(f"=== s3://{BUCKET}/{prefix} ===\n")
    data = list_objects(prefix=prefix, max_keys=max_keys)

    for item in data.get("items", []):
        print(f"  {item['key']:80s}  {item['size']:>12,} bytes  {item['lastModified']}")

    print(f"\n총 {len(data.get('items', []))}개 항목")
    if data.get("nextContinuationToken"):
        print("(더 많은 항목이 있습니다 — maxKeys를 늘려서 재조회)")
