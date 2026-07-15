"""29CM 브랜드 판매순 TOP100 크롤러 — brands_list.py 의 브랜드별로 판매순 상위 100개 수집.

무신사 crawl_brands 와 달리 29CM 브랜드 스토어는 기간(개월) 필터가 없다 →
판매순(MOST_SOLD) 정렬 후 top100 만 뽑는다. 브랜드당 파일 1개.

  [1단계] 브랜드 스토어 그리드 (실제 페이지와 동일 API)
    POST display-bff-api.29cm.co.kr/api/v1/listing/items?colorchipVariant=control
    body {"pageType":"BRAND_HOME","sortType":"MOST_SOLD",
          "facets":{"brandFacetInputs":[{"frontBrandNo":<id>}]},
          "pageRequest":{"page":1,"size":100}}
    → data.list (판매순, itemEvent.eventProperties 에 카테고리 3-depth 포함)
    ※ search-api/products/brand 는 정렬·카테고리가 스토어와 달라 쓰지 않는다.
  [2단계] 상세 보강 (crawl_best.enrich_details 재사용)
    product-detail itemDetailsList → 제품 주소재/색상/치수(NOTICE_*)

대상 브랜드: brands_list.py 의 BRANDS = [(이름, brandId), ...].
상품 파싱(build_row)·프록시·상세·CSV 로직은 crawl_best.py 를 재사용한다.

사용법:
    python crawl_29cm/crawl_brands.py                    # 리스트 전체 판매순 top100
    python crawl_29cm/crawl_brands.py --top 50           # 상위 50개
    python crawl_29cm/crawl_brands.py --no-detail        # 1단계만(빠름)
    python crawl_29cm/crawl_brands.py --brand-id 2178 --name adidas   # 단일 브랜드
    python crawl_29cm/crawl_brands.py --no-proxy         # 회사 IP 직접

출력 (브랜드별 파일 분리):
    crawl_29cm/output/29cm_<브랜드>_sale_YYYYMMDD.csv
"""
import argparse
import csv
import os
import sys
import time
from datetime import datetime

import requests

# crawl_best 의 공유 로직 재사용 (같은 폴더). 이 import 가 .env 로드 +
# UTF-8 콘솔 고정(sys.stdout 재래핑)을 수행하므로 여기서 따로 하지 않는다.
from crawl_best import (
    HEADERS, COLUMNS, OUTPUT_DIR,
    fill_value, build_proxies, request_json, enrich_details, build_row,
)

LISTING_URL = "https://display-bff-api.29cm.co.kr/api/v1/listing/items?colorchipVariant=control"


def load_brand_targets():
    """brands_list.BRANDS → [(name, brandId), ...]. (이름,id) 튜플 / id(int) /
    "이름:id" 문자열 허용."""
    try:
        from brands_list import BRANDS
    except Exception as e:
        print(f"[FAIL] brands_list.py 로드 실패: {e}")
        return []
    out = []
    for entry in BRANDS:
        try:
            if isinstance(entry, (list, tuple)) and len(entry) >= 2:
                out.append((str(entry[0]), int(entry[1])))
            elif isinstance(entry, int):
                out.append((str(entry), entry))
            elif isinstance(entry, str) and ":" in entry:
                nm, i = entry.rsplit(":", 1)
                out.append((nm.strip(), int(i.strip())))
            else:
                print(f"  ⚠ 무시된 항목(형식 불명): {entry!r}")
        except (ValueError, TypeError):
            print(f"  ⚠ 무시된 항목(brandId 파싱 실패): {entry!r}")
    return out


def fetch_brand_products(session, front_brand_no, top, proxies):
    """브랜드 스토어 그리드(listing/items) 를 판매순으로 페이지네이션하며 top N 수집."""
    limit = top or 100
    items = []
    page = 1
    size = min(100, limit)
    while len(items) < limit:
        body = {
            "pageType": "BRAND_HOME",
            "sortType": "MOST_SOLD",
            "facets": {"brandFacetInputs": [{"frontBrandNo": front_brand_no}]},
            "pageRequest": {"page": page, "size": size},
        }
        data = request_json(session, "POST", LISTING_URL, proxies, json_body=body)
        if not data:
            break
        lst = (data.get("data") or {}).get("list") or []
        if not lst:
            break
        items.extend(lst)
        if len(lst) < size:
            break
        page += 1
    return items[:limit]


def save_csv(rows, now, prefix):
    """VALUE(c1..cN) 채우고 브랜드별 CSV 저장. 파일 잠김 시 시각 붙여 새 파일."""
    for r in rows:
        fill_value(r)
    day_dir = os.path.join(OUTPUT_DIR, now.strftime("%Y%m%d"))
    os.makedirs(day_dir, exist_ok=True)
    out_path = os.path.join(day_dir, f"{prefix}_{now.strftime('%Y%m%d')}.csv")

    def _write(path):
        with open(path, "w", encoding="utf-8-sig", newline="") as f:
            w = csv.DictWriter(f, fieldnames=COLUMNS)
            w.writeheader()
            w.writerows(rows)

    try:
        _write(out_path)
    except PermissionError:
        out_path = os.path.join(day_dir, f"{prefix}_{now.strftime('%Y%m%d_%H%M%S')}.csv")
        print(f"  ⚠ 기존 CSV 잠김 — 새 파일: {os.path.basename(out_path)}")
        _write(out_path)
    return out_path


def crawl_one(session, name, brand_id, proxies, args, now, ymd):
    """단일 브랜드 판매순 top N 수집→상세→CSV 저장. out_path 반환(0개면 None)."""
    print(f"\n=== [{name}] brandId={brand_id} 판매순 top{args.top} ===")
    prods = fetch_brand_products(session, brand_id, args.top, proxies)
    if not prods:
        print(f"  ⚠ {name}(brandId={brand_id}) 상품 없음 — 건너뜀 (brandId 확인)")
        return None
    # crawl_best.build_row 재사용 (itemEvent/itemInfo 파싱 + 카테고리/가격/PRODUCT_URL).
    # 판매순 리스트 순서 = RANKING, 성별필터는 전체(A).
    rows = [build_row(p, i, "A", ymd) for i, p in enumerate(prods, 1)]
    print(f"  {name}: {len(rows)}개 수집 (1위: {rows[0]['PRODUCT_NAME'][:30]})")

    out_path = None
    try:
        if not args.no_detail:
            enrich_details(session, rows, proxies, args.detail_delay)
    except KeyboardInterrupt:
        print("\n[중단] 사용자 Ctrl+C — 여기까지 저장")
        save_csv(rows, now, f"29cm_{name}_sale")
        raise
    except Exception as e:
        print(f"  [2단계 오류] {type(e).__name__}: {str(e)[:120]}")
    finally:
        out_path = save_csv(rows, now, f"29cm_{name}_sale")

    catd = sum(1 for r in rows if r["MAIN_CATEGORY"])
    mat = sum(1 for r in rows if r["NOTICE_MATERIAL"])
    print(f"  [완료] {name} {len(rows)}행 → {out_path} (카테고리 {catd}, 소재 {mat})")
    return out_path


def main():
    ap = argparse.ArgumentParser(description="29CM 브랜드 판매순 top100 크롤러")
    ap.add_argument("--brand-id", type=int, help="단일 brandId (리스트 무시)")
    ap.add_argument("--name", default="brand", help="--brand-id와 함께 쓰는 출력 파일명")
    ap.add_argument("--top", type=int, default=100, help="브랜드당 상위 N개 (기본 100)")
    ap.add_argument("--no-detail", action="store_true", help="2단계(상품정보 상세) 생략")
    ap.add_argument("--detail-delay", type=float, default=0.4, help="상세 요청 간 딜레이(초)")
    ap.add_argument("--no-proxy", action="store_true", help="회사 IP 직접 호출")
    args = ap.parse_args()

    if args.brand_id:
        brands = [(args.name, args.brand_id)]
    else:
        brands = load_brand_targets()
    if not brands:
        print("[FAIL] 대상 브랜드 없음 (brands_list.py 확인)")
        sys.exit(1)

    proxies = None if args.no_proxy else build_proxies()
    session = requests.Session()
    session.headers.update(HEADERS)

    now = datetime.now()
    ymd = (now.year, now.month, now.day)
    print(f"\n대상 브랜드 {len(brands)}개: {[b[0] for b in brands]}")

    outputs = []
    for name, bid in brands:
        out = crawl_one(session, name, bid, proxies, args, now, ymd)
        if out:
            outputs.append((name, out))

    print(f"\n[전체 완료] {len(outputs)}개 파일:")
    for name, out in outputs:
        print(f"  {name} → {out}")


if __name__ == "__main__":
    main()
