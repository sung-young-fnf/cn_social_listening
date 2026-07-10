"""29CM Best 월간 인기 상품 크롤러 — 카테고리별 인기 상품을 23컬럼 CSV로 수집.

순수 HTTP (브라우저 불필요, 서명 없음):
  [1] 카테고리 그룹 : recommend-api.29cm.co.kr/api/v4/best/category-groups  (GET)
                      → WOMEN/MEN/... 그룹별 대카테고리 코드 목록
  [2] 인기 상품     : display-bff-api.29cm.co.kr/api/v1/plp/best/items      (POST)
                      → 대카테고리별 MONTHLY POPULARITY 순 상품 (한 응답에 카테고리 3-depth 포함)

무신사(crawl_musinsa)와 동일하게 Oxylabs KR 프록시 경유 + VALUE(c1..cN) JSON + 23컬럼 schema.

수집 가능 필드: MAIN/MID/SUB_CATEGORY, RANKING, BRAND, PRODUCT_NAME, PRICE, DISCOUNT_PRICE,
               DISCOUNT_COUPON_VALUE, REVIEW_COUNT, LIKE_COUNT, IMAGE_URL
수집 불가 (API 미제공): SEASON, GENDER(상품별), VIEW_COUNT, SELL_COUNT

사용법:
    python crawl_29cm/crawl_best.py                       # 전체 인기 TOP 100 (월간/인기)
    python crawl_29cm/crawl_best.py --period WEEKLY        # 주간
    python crawl_29cm/crawl_best.py --ranking LIKE         # 좋아요순
    python crawl_29cm/crawl_best.py --gender F             # 여성 세그먼트 TOP 100
    python crawl_29cm/crawl_best.py --no-proxy            # 회사 IP 직접
    python crawl_29cm/crawl_best.py --large 268100100      # 단일 대카테고리만
    python crawl_29cm/crawl_best.py --all-categories       # 대카테고리별 각각 수집

출력:
    crawl_29cm/output/29cm_best_YYYYMMDD.csv
"""
import argparse
import csv
import io
import json
import os
import secrets
import sys
import time
from datetime import datetime

import requests

# UTF-8 콘솔 고정 (한글 깨짐 방지)
sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", line_buffering=True)

try:
    from dotenv import load_dotenv
    ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
    load_dotenv(os.path.join(ROOT, ".env"))
except ImportError:
    pass

HERE = os.path.dirname(os.path.abspath(__file__))
OUTPUT_DIR = os.path.join(HERE, "output")

UA = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
      "(KHTML, like Gecko) Chrome/126.0.0.0 Safari/537.36")
HEADERS = {
    "User-Agent": UA,
    "Accept": "application/json, text/plain, */*",
    "Accept-Language": "ko-KR,ko;q=0.9,en;q=0.8",
    "Content-Type": "application/json",
    "Origin": "https://www.29cm.co.kr",
    "Referer": "https://www.29cm.co.kr/",
}

CATEGORY_GROUPS_URL = "https://recommend-api.29cm.co.kr/api/v4/best/category-groups"
BEST_ITEMS_URL = "https://display-bff-api.29cm.co.kr/api/v1/plp/best/items"
# 상품 상세 (상품정보: 제품 주소재/색상/치수 = data.itemDetailsList)
PRODUCT_DETAIL_URL = "https://bff-api.29cm.co.kr/api/v5/product-detail/{no}"
PRODUCT_PAGE_URL = "https://www.29cm.co.kr/products/{no}"

# 운영 schema — 무신사 확장과 동일 (상품정보 3필드 + 상품주소).
COLUMNS = [
    "VALUE", "YEAR", "MONTH", "DAY",
    "MAIN_CATEGORY", "MID_CATEGORY", "SUB_CATEGORY",
    "GENDER_FILTER", "RANKING", "SEASON",
    "BRAND", "GENDER", "PRODUCT_NUMBER", "PRODUCT_NAME",
    "PRICE", "DISCOUNT_PRICE", "DISCOUNT_COUPON_VALUE",
    "REVIEW_COUNT", "LIKE_COUNT", "VIEW_COUNT", "SELL_COUNT",
    "IMAGE_URL", "PRODUCT_NO",
    # === 상품 상세(product-detail API) 확장 ===
    "NOTICE_MATERIAL", "NOTICE_COLOR", "NOTICE_SIZE",
    "PRODUCT_URL",   # 상품 상세 페이지 주소 (PRODUCT_NO 로 생성)
]
# VALUE 컬럼: 데이터 컬럼(c1..c19)을 JSON으로 저장 (VALUE/YEAR/MONTH/DAY 제외).
VALUE_KEY_COLUMNS = COLUMNS[4:]

# 카테고리 그룹명 → GENDER_FILTER (A/M/F). WOMEN=F, MEN=M, 그 외(LIFE 등)=A.
GROUP_GENDER = {"WOMEN": "F", "MEN": "M"}


def fill_value(row):
    """데이터 컬럼을 {c1, c2, ...} JSON 문자열로 만들어 VALUE에 저장.
    정수는 89,000 처럼 천단위 콤마 포맷."""
    obj = {}
    for i, col in enumerate(VALUE_KEY_COLUMNS, 1):
        v = row.get(col, "")
        if isinstance(v, int):
            v = f"{v:,}"
        elif v is None:
            v = ""
        obj[f"c{i}"] = str(v)
    row["VALUE"] = json.dumps(obj, ensure_ascii=False)


# === Oxylabs 프록시 (crawl_musinsa 패턴) ===
def build_proxies():
    """requests용 {http,https} dict. Oxylabs KR sticky sessid."""
    user = os.getenv("OXYLABS_USERNAME")
    pwd = os.getenv("OXYLABS_PASSWORD")
    if not user or not pwd:
        print("[FAIL] OXYLABS_USERNAME/PASSWORD 없음 (.env 확인). --no-proxy로 직접 호출 가능.")
        sys.exit(1)
    country = os.getenv("OXYLABS_COUNTRY", "kr")
    base = user if "-cc-" in user else f"{user}-cc-{country}"
    sessid = f"cm29_{secrets.token_hex(4)}"
    sesstime = os.getenv("OXYLABS_SESSTIME", "30")
    username = f"{base}-sessid-{sessid}-sesstime-{sesstime}"
    host = os.getenv("OXYLABS_HOST", "pr.oxylabs.io")
    port = os.getenv("OXYLABS_PORT", "7777")
    url = f"http://{username}:{pwd}@{host}:{port}"
    print(f"[proxy] country={country} {host}:{port} sessid={sessid}")
    return {"http": url, "https": url}


def request_json(session, method, url, proxies, json_body=None, retries=3):
    for attempt in range(retries):
        try:
            if method == "GET":
                r = session.get(url, proxies=proxies, timeout=25)
            else:
                r = session.post(url, json=json_body, proxies=proxies, timeout=25)
            if r.status_code == 200:
                return r.json()
            print(f"    ! {url} status={r.status_code} (시도 {attempt+1})")
        except Exception as e:
            print(f"    ! {url} 실패: {e} (시도 {attempt+1})")
        time.sleep(1.5 * (attempt + 1))
    return None


def fetch_category_groups(session, proxies):
    """[(large_code, group_name), ...] 반환. 실패 시 빈 리스트."""
    data = request_json(session, "GET", CATEGORY_GROUPS_URL, proxies)
    out = []
    if not data:
        return out
    for group in data.get("data") or []:
        gname = group.get("categoryGroupName") or ""
        for item in group.get("categoryGroupItemList") or []:
            code = item.get("categoryCode")
            if code:
                out.append((code, gname))
    return out


def fetch_best_items(session, large_code, gender, period, ranking, size, proxies):
    """대카테고리 하나의 인기 상품 list 반환."""
    body = {
        "pageRequest": {"page": 1, "size": size},
        "userSegment": {"gender": gender, "age": "ALL"},
        "facets": {
            "categoryFacetInputs": ([{"largeId": large_code}] if large_code else []),
            "periodFacetInput": {"type": period, "order": "DESC"},
            "rankingFacetInput": {"type": ranking},
        },
    }
    data = request_json(session, "POST", BEST_ITEMS_URL, proxies, json_body=body)
    if not data:
        return []
    return ((data.get("data") or {}).get("list")) or []


def build_row(item, rank, gender_filter, ymd):
    """29cm best item → 23컬럼 행. 카테고리 3-depth는 itemEvent.eventProperties에서.
    조회/판매수·시즌·상품성별은 API 미제공 → 빈 값."""
    y, m, d = ymd
    ev = (item.get("itemEvent") or {}).get("eventProperties") or {}
    info = item.get("itemInfo") or {}
    item_id = item.get("itemId") or ev.get("itemNo") or ""

    # 쿠폰 할인율: is_cart_coupon_item=true 일 때 (sellPrice - displayPrice)/sellPrice×100
    coupon = ""
    is_coupon = (ev.get("experimentData") or {}).get("is_cart_coupon_item")
    sell = info.get("sellPrice")
    disp = info.get("displayPrice")
    if is_coupon and sell and disp and sell > 0 and disp < sell:
        coupon = round((sell - disp) / sell * 100)

    return {
        "VALUE": "",  # 마지막에 fill_value()로 채움
        "YEAR": y, "MONTH": m, "DAY": d,
        "MAIN_CATEGORY": ev.get("largeCategoryName", ""),
        "MID_CATEGORY": ev.get("middleCategoryName", ""),
        "SUB_CATEGORY": ev.get("smallCategoryName", ""),   # 문서엔 불가로 적혔으나 실제 제공됨
        "GENDER_FILTER": gender_filter,
        "RANKING": rank,
        "SEASON": "",                                      # API 미제공
        "BRAND": info.get("brandName") or ev.get("brandName", ""),
        "GENDER": "",                                      # API 미제공 (필터값만 존재)
        "PRODUCT_NUMBER": item_id,
        "PRODUCT_NAME": info.get("productName") or ev.get("itemName", ""),
        "PRICE": info.get("originalPrice", ""),            # 정가
        "DISCOUNT_PRICE": info.get("sellPrice", ""),       # 쿠폰 미적용 할인가
        "DISCOUNT_COUPON_VALUE": coupon,
        "REVIEW_COUNT": info.get("reviewCount", ""),
        "LIKE_COUNT": info.get("likeCount", ""),
        "VIEW_COUNT": "",                                  # API 미제공
        "SELL_COUNT": "",                                  # API 미제공
        "IMAGE_URL": info.get("thumbnailUrl", ""),
        "PRODUCT_NO": item_id,
        # 상세 보강 전 기본값 (enrich_details 에서 채움)
        "NOTICE_MATERIAL": "", "NOTICE_COLOR": "", "NOTICE_SIZE": "",
        "PRODUCT_URL": PRODUCT_PAGE_URL.format(no=item_id) if item_id else "",
    }


def _map_item_details(item_details_list):
    """product-detail 의 itemDetailsList → 상품정보 3필드 매핑.
    제목 변형(제품 주소재/제품 소재) 관대 매칭. 값이 '상세 페이지 참고'면 그대로 저장."""
    out = {"NOTICE_MATERIAL": "", "NOTICE_COLOR": "", "NOTICE_SIZE": ""}
    for it in item_details_list or []:
        title = it.get("itemDetailsTitles", "") or ""
        value = it.get("itemDetailsValue", "") or ""
        if "소재" in title:
            out["NOTICE_MATERIAL"] = value
        elif "색상" in title:
            out["NOTICE_COLOR"] = value
        elif "치수" in title:
            out["NOTICE_SIZE"] = value
    return out


def enrich_details(session, rows, proxies, delay):
    """각 행의 PRODUCT_NO 로 product-detail API 호출 → 상품정보(주소재/색상/치수) 채움.
    실패는 건너뛰고(빈 값 유지) 계속 진행."""
    total = len(rows)
    print(f"\n[2단계] 상품정보 보강 {total}개 (product-detail API)")
    ok = 0
    for idx, row in enumerate(rows, 1):
        no = row["PRODUCT_NO"]
        if not no:
            continue
        data = request_json(session, "GET", PRODUCT_DETAIL_URL.format(no=no), proxies)
        if data:
            details = (data.get("data") or {}).get("itemDetailsList") or []
            mapped = _map_item_details(details)
            row.update(mapped)
            if any(mapped.values()):
                ok += 1
        if idx <= 3 or idx % 50 == 0:
            print(f"  [{idx}/{total}] {no} → 소재={row['NOTICE_MATERIAL'][:20]} "
                  f"색상={row['NOTICE_COLOR'][:20]}")
        time.sleep(delay)
    print(f"[2단계 완료] 상품정보 {ok}/{total}")


def save_csv(all_rows, now):
    """VALUE(c1..cN) 채우고 CSV 저장. 파일 잠김 시 시각 붙여 새 파일. out_path 반환."""
    for row in all_rows:
        fill_value(row)
    os.makedirs(OUTPUT_DIR, exist_ok=True)
    out_path = os.path.join(OUTPUT_DIR, f"29cm_best_{now.strftime('%Y%m%d')}.csv")

    def _write(path):
        with open(path, "w", encoding="utf-8-sig", newline="") as f:
            w = csv.DictWriter(f, fieldnames=COLUMNS)
            w.writeheader()
            w.writerows(all_rows)

    try:
        _write(out_path)
    except PermissionError:
        out_path = os.path.join(OUTPUT_DIR, f"29cm_best_{now.strftime('%Y%m%d_%H%M%S')}.csv")
        print(f"  ⚠ 기존 CSV 잠김 — 새 파일로 저장: {os.path.basename(out_path)}")
        _write(out_path)
    return out_path


def main():
    ap = argparse.ArgumentParser(description="29CM Best 인기 상품 크롤러")
    ap.add_argument("--period", default="MONTHLY", choices=["DAILY", "WEEKLY", "MONTHLY"],
                    help="집계 기간 (기본 MONTHLY)")
    ap.add_argument("--ranking", default="POPULARITY", choices=["POPULARITY", "LIKE"],
                    help="랭킹 기준 (기본 POPULARITY)")
    ap.add_argument("--size", type=int, default=100, help="수집 상품 수 (기본 100 = TOP 100)")
    ap.add_argument("--large", type=int, default=0, help="단일 대카테고리 코드만 (0=필터없음)")
    ap.add_argument("--all-categories", action="store_true",
                    help="대카테고리별로 각각 수집 (기본은 전체 통합 TOP N)")
    ap.add_argument("--gender", default="", choices=["", "F", "M", "ALL"],
                    help="userSegment 성별 (기본: 통합은 ALL, --all-categories는 그룹따라 자동)")
    ap.add_argument("--delay", type=float, default=1.0, help="카테고리 간 딜레이(초)")
    ap.add_argument("--no-detail", action="store_true", help="2단계(상품정보 상세) 생략")
    ap.add_argument("--detail-delay", type=float, default=0.4, help="상세 요청 간 딜레이(초)")
    ap.add_argument("--no-proxy", action="store_true", help="회사 IP 직접 호출")
    args = ap.parse_args()

    proxies = None if args.no_proxy else build_proxies()
    session = requests.Session()
    session.headers.update(HEADERS)

    now = datetime.now()
    ymd = (now.year, now.month, now.day)

    # 대상 카테고리 목록 결정
    if args.large:
        # 단일 카테고리
        targets = [(args.large, "")]
    elif args.all_categories:
        # 대카테고리별 순회
        print("[1] 카테고리 그룹 조회...")
        targets = fetch_category_groups(session, proxies)
        if not targets:
            print("[FAIL] 카테고리 그룹을 못 받음 — 종료")
            return
        print(f"  대카테고리 {len(targets)}개")
    else:
        # 기본: 카테고리 필터 없이 전체 통합 TOP N (large_code=0 → categoryFacetInputs=[])
        targets = [(0, "")]
        print(f"[모드] 전체 통합 TOP {args.size}")

    all_rows = []
    for idx, (large_code, gname) in enumerate(targets, 1):
        # 성별 필터: --gender 강제 우선, 없으면 그룹따라, 그것도 없으면 ALL
        if args.gender:
            seg_gender = args.gender
        else:
            seg_gender = GROUP_GENDER.get(gname, "ALL")
        gender_filter = "A" if seg_gender == "ALL" else seg_gender

        items = fetch_best_items(session, large_code, seg_gender,
                                 args.period, args.ranking, args.size, proxies)
        print(f"  [{idx}/{len(targets)}] {gname or '-'} cat={large_code} "
              f"seg={seg_gender} → {len(items)}개")
        for rank, item in enumerate(items, 1):
            all_rows.append(build_row(item, rank, gender_filter, ymd))
        time.sleep(args.delay)

    if not all_rows:
        print("\n[결과] 수집된 행 없음")
        return

    # [2단계] 상품정보 보강 — 무엇이 터져도 finally 에서 저장 (데이터 손실 방지)
    out_path = None
    try:
        if not args.no_detail:
            enrich_details(session, all_rows, proxies, args.detail_delay)
    except KeyboardInterrupt:
        print("\n[중단] 사용자 Ctrl+C — 여기까지 모은 데이터 저장")
    except Exception as e:
        print(f"\n[2단계 오류] {type(e).__name__}: {str(e)[:120]}")
    finally:
        out_path = save_csv(all_rows, now)

    print(f"\n[완료] {len(all_rows)}개 행 → {out_path}")
    subc = sum(1 for r in all_rows if r["SUB_CATEGORY"])
    liked = sum(1 for r in all_rows if r["LIKE_COUNT"] != "")
    coup = sum(1 for r in all_rows if r["DISCOUNT_COUPON_VALUE"] != "")
    matc = sum(1 for r in all_rows if r["NOTICE_MATERIAL"])
    print(f"  채움률 — SUB_CATEGORY {subc}, LIKE {liked}, COUPON {coup}, "
          f"NOTICE_MATERIAL {matc} / {len(all_rows)}")


if __name__ == "__main__":
    main()
