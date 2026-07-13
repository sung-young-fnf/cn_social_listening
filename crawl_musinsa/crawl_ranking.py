"""무신사 랭킹 크롤러 — 랭킹 페이지 상위 상품(기본 월간·전체) 100개 + 상세 정보 수집.

브랜드 지정(crawl_brands.py)과 달리, 무신사 랭킹 API 로 top100 목록을 받아
각 상품의 23컬럼을 채운다. 전 과정 순수 HTTP(curl_cffi) — 브라우저 없음.

  [1단계] 랭킹 API (client.musinsa.com/.../ranking/sections/200)
    - GET, 무인증. period(REALTIME/DAILY/WEEKLY/MONTHLY) + categoryCode + gf + ageBand
    - 응답 data.modules 의 MULTICOLUMN.items(PRODUCT_COLUMN) 를 rank 순으로 모음
    - 목록만으로: RANKING·상품번호·브랜드·상품명·정가·할인가·할인율·리뷰수·썸네일
  [1.5] 좋아요 API (like.musinsa.com, POST 배치) → LIKE_COUNT
  [2단계] 상세 enrich (crawl_brands.enrich_details_http 재사용)
    - 상세 __NEXT_DATA__ → 카테고리(MAIN/MID/SUB)·시즌·성별(GENDER)
    - stat API(goods-detail.musinsa.com/.../stat) → 조회수·판매수(원본값)

스토어(--store):
    musinsa = 무신사 일반 랭킹 (section 200, storeCode=musinsa, 기본 category 000)
    sport   = 무신사 스포츠 랭킹 (section 212, storeCode=player, 기본 category 017000)

사용법:
    python crawl_musinsa/crawl_ranking.py                          # 무신사 일반 월간 top100
    python crawl_musinsa/crawl_ranking.py --store sport            # 무신사 스포츠 월간 top100
    python crawl_musinsa/crawl_ranking.py --gf M                   # 월간·남성
    python crawl_musinsa/crawl_ranking.py --category 001           # 특정 카테고리 랭킹
    python crawl_musinsa/crawl_ranking.py --top 20                 # 상위 20개만
    python crawl_musinsa/crawl_ranking.py --no-detail              # 1단계만(빠름)
    python crawl_musinsa/crawl_ranking.py --no-proxy               # 회사 IP 직접

출력:
    crawl_musinsa/output/musinsa_ranking_YYYYMMDD.csv        (일반)
    crawl_musinsa/output/musinsa_sport_ranking_YYYYMMDD.csv  (스포츠)
"""
import argparse
import time
from datetime import datetime

import requests
from curl_cffi import requests as cffi

# crawl_brands 의 공유 로직 재사용 (같은 폴더). 이 import 가 UTF-8 콘솔 고정
# (sys.stdout 재래핑)도 수행하므로 여기서 따로 감싸지 않는다(이중 래핑 시 파일 닫힘).
from crawl_brands import (
    HEADERS, build_proxies_requests, fetch_like_counts,
    enrich_details_http, save_csv,
)

RANKING_URL_TMPL = "https://client.musinsa.com/api/home/web/v5/pans/ranking/sections/{section}"
VALID_PERIODS = ["REALTIME", "DAILY", "WEEKLY", "MONTHLY"]

# 스토어별 랭킹 프리셋 — section·storeCode·기본 카테고리·출력 파일 prefix.
# 무신사 일반과 스포츠(player)는 같은 랭킹 API 계열이라 파라미터만 다르다.
STORE_PRESETS = {
    "musinsa": {"section": "200", "store_code": "musinsa", "category": "000",
                "prefix": "musinsa_ranking", "label": "무신사 일반"},
    "sport": {"section": "212", "store_code": "player", "category": "017000",
              "prefix": "musinsa_sport_ranking", "label": "무신사 스포츠"},
}


def _amp_payload(it):
    """아이템의 amplitude 이벤트 로그 payload (정가·리뷰수 등 부가 필드 소스)."""
    try:
        return it["image"]["onClickLike"]["eventLog"]["amplitude"]["payload"]
    except Exception:
        return {}


def _to_int(v):
    try:
        return int(v)
    except (TypeError, ValueError):
        return v if v not in (None, "") else ""


def fetch_ranking(sess, proxies, section, store_code, period, gf, category, top):
    """랭킹 API 호출 → PRODUCT_COLUMN 아이템을 rank 오름차순 리스트로 반환."""
    url = RANKING_URL_TMPL.format(section=section)
    params = {"storeCode": store_code, "categoryCode": category, "contentsId": "",
              "gf": gf, "period": period, "ageBand": "AGE_BAND_ALL"}
    r = sess.get(url, params=params, headers=HEADERS, proxies=proxies, timeout=30)
    if r.status_code != 200:
        print(f"[FAIL] 랭킹 API status={r.status_code} body={r.text[:200]}")
        return []
    data = r.json()
    if (data.get("meta") or {}).get("result") != "SUCCESS":
        print(f"[FAIL] 랭킹 API meta={data.get('meta')}")
        return []
    items = []
    for m in (data.get("data") or {}).get("modules", []):
        if m.get("type") == "MULTICOLUMN":
            for it in m.get("items", []):
                if it.get("type") == "PRODUCT_COLUMN" and it.get("id"):
                    items.append(it)
    items.sort(key=lambda it: it.get("image", {}).get("rank") or 9999)
    if top:
        items = items[:top]
    return items


def build_row(it, gf, ymd):
    """랭킹 아이템 → 23컬럼 row (카테고리/시즌/성별/조회/판매/좋아요는 후속 단계에서 채움)."""
    info = it.get("info", {}) or {}
    img = it.get("image", {}) or {}
    amp = _amp_payload(it)
    no = str(it.get("id", ""))
    y, m, d = ymd
    # 정가: amplitude.original_price 우선, 없으면 할인 없다고 보고 finalPrice.
    orig = _to_int(amp.get("original_price")) if amp.get("original_price") else info.get("finalPrice", "")
    return {
        "VALUE": "",
        "YEAR": y, "MONTH": m, "DAY": d,
        "MAIN_CATEGORY": "", "MID_CATEGORY": "", "SUB_CATEGORY": "",   # 2단계(상세)
        "GENDER_FILTER": gf,
        "RANKING": img.get("rank", ""),
        "SEASON": "",                                                 # 2단계(상세)
        "BRAND": info.get("brandName", ""),
        "GENDER": "",                                                 # 2단계(상세 sex)
        "PRODUCT_NUMBER": no,
        "PRODUCT_NAME": info.get("productName", ""),
        "PRICE": orig,
        "DISCOUNT_PRICE": info.get("finalPrice", ""),
        "DISCOUNT_COUPON_VALUE": info.get("discountRatio", ""),
        "REVIEW_COUNT": _to_int(amp.get("reviewCount")),
        "LIKE_COUNT": "",                                             # 좋아요 API
        "VIEW_COUNT": "", "SELL_COUNT": "",                          # 2단계(stat)
        "IMAGE_URL": img.get("url", ""),
        "PRODUCT_NO": no,
        "PRODUCT_URL": f"https://www.musinsa.com/products/{no}" if no else "",
    }


# ─────────────────────────────────────────────────────────────────────────
# [향후 계획] S3 연결 + Airflow 등록 시 리팩터링 예정 (지금은 CLI main 유지)
#
# 현재는 argparse 기반 main() 뿐이라 CLI 전용. Airflow 는 PythonOperator 로
# 함수를 직접 호출하므로, S3 적재 붙일 때 아래처럼 바꾼다:
#
#   1) main() 본문을 run(store="musinsa", period="MONTHLY", gf="A",
#      category=None, top=100, use_proxy=True, no_detail=False, ...) 콜러블로
#      추출하고, main() 은 argparse 파싱 후 run() 호출만 하게 한다.
#      (CLI·Airflow 둘 다 지원, 로직 중복 0)
#   2) run() 이 요약 dict(출력경로·건수)를 return 하게 해서 XCom 으로 다음
#      단계(S3 업로드 등)에 넘긴다.
#
# 그러면 DAG 는 스토어를 파일 분리 없이 TaskGroup 안 병렬 task 로 구성:
#
#   from crawl_musinsa.crawl_ranking import run
#   with TaskGroup("musinsa_ranking"):
#       PythonOperator(task_id="rank_musinsa", python_callable=run,
#                      op_kwargs={"store": "musinsa"})
#       PythonOperator(task_id="rank_sport",   python_callable=run,
#                      op_kwargs={"store": "sport"})   # 두 task 병렬 실행
#
# → 스토어가 늘어도 STORE_PRESETS 에 한 줄 + task 한 개만 추가.
# ─────────────────────────────────────────────────────────────────────────
# 성별 필터 → 출력 파일명 suffix. A(전체)는 기존 파일명 유지(하위호환).
GENDER_LABEL = {"M": "men", "F": "women", "A": ""}


def run_one_gender(sess, rsession, proxies, preset, gf, period, category, top,
                   no_detail, detail_delay, detail_limit, now, ymd):
    """성별 하나에 대한 랭킹 수집→좋아요→상세→CSV 저장 (성별별 파일 분리)."""
    section = preset["section"]
    store_code = preset["store_code"]
    print(f"\n=== [{preset['label']}] gf={gf} 랭킹 수집 (store={store_code} "
          f"section={section} period={period} category={category} top={top or '전체'}) ===")
    items = fetch_ranking(sess, proxies, section, store_code, period, gf, category, top)
    if not items:
        print(f"  [결과] gf={gf} 랭킹 0개 — 건너뜀")
        return None
    rows = [build_row(it, gf, ymd) for it in items]
    print(f"  랭킹 {len(rows)}개 수집 (rank {rows[0]['RANKING']}~{rows[-1]['RANKING']})")

    # [1.5] 좋아요
    nos = [r["PRODUCT_NO"] for r in rows if r["PRODUCT_NO"]]
    like_map = fetch_like_counts(rsession, nos, proxies)
    for r in rows:
        r["LIKE_COUNT"] = like_map.get(r["PRODUCT_NO"], "")
    print(f"  좋아요 {len(like_map)}개 매핑")

    # 성별별 출력 prefix (M→_men, F→_women, A→기존)
    suffix = GENDER_LABEL.get(gf, gf.lower())
    prefix = f"{preset['prefix']}_{suffix}" if suffix else preset["prefix"]

    # [2단계] 상세 enrich — 무엇이 터져도 finally 에서 저장
    out_path = None
    try:
        if not no_detail:
            enrich_details_http(rows, proxies, detail_delay, detail_limit)
    except KeyboardInterrupt:
        print("\n[중단] 사용자 Ctrl+C — 여기까지 모은 데이터 저장")
        out_path = save_csv(rows, now, prefix=prefix)
        raise
    except Exception as e:
        print(f"\n[2단계 오류] {type(e).__name__}: {str(e)[:120]}")
        print("  → 1단계 수집분은 그대로 저장 (2단계 일부 필드만 비어있을 수 있음)")
    finally:
        out_path = save_csv(rows, now, prefix=prefix)

    liked = sum(1 for r in rows if r["LIKE_COUNT"] != "")
    catd = sum(1 for r in rows if r["MAIN_CATEGORY"] != "")
    viewd = sum(1 for r in rows if r["VIEW_COUNT"] != "")
    print(f"  [완료] gf={gf} {len(rows)}개 행 → {out_path}")
    print(f"    채움률 — LIKE {liked}, CATEGORY {catd}, VIEW {viewd} / {len(rows)}")
    return out_path


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--store", default="musinsa", choices=list(STORE_PRESETS),
                    help="스토어 (musinsa=일반 랭킹, sport=무신사 스포츠 랭킹)")
    ap.add_argument("--period", default="MONTHLY", choices=VALID_PERIODS,
                    help="랭킹 기간 (기본 MONTHLY=최근 1개월)")
    ap.add_argument("--gf", default="A",
                    help="성별 필터. 콤마로 여러 개 가능 (A=전체, M=남성, F=여성). "
                         "예: --gf M,F → 남성 top100·여성 top100 각각 별도 파일")
    ap.add_argument("--category", default=None,
                    help="카테고리 코드 (미지정 시 스토어 기본값: musinsa=000, sport=017000)")
    ap.add_argument("--top", type=int, default=100, help="수집 상위 개수 (0=전체)")
    ap.add_argument("--no-proxy", action="store_true")
    ap.add_argument("--no-detail", action="store_true", help="2단계(상세) 생략")
    ap.add_argument("--detail-limit", type=int, default=0, help="2단계 상품 수 상한 (0=전체)")
    ap.add_argument("--detail-delay", type=float, default=0.8, help="상세 요청 간 딜레이(초)")
    args = ap.parse_args()

    preset = STORE_PRESETS[args.store]
    category = args.category or preset["category"]
    genders = [g.strip().upper() for g in args.gf.split(",") if g.strip()]

    use_proxy = not args.no_proxy
    proxies = build_proxies_requests() if use_proxy else None
    sess = cffi.Session(impersonate="chrome")
    rsession = requests.Session()
    rsession.headers.update(HEADERS)

    now = datetime.now()
    ymd = (now.year, now.month, now.day)

    outputs = []
    for gf in genders:
        out = run_one_gender(sess, rsession, proxies, preset, gf, args.period,
                             category, args.top, args.no_detail, args.detail_delay,
                             args.detail_limit, now, ymd)
        if out:
            outputs.append((gf, out))

    print(f"\n[전체 완료] {len(outputs)}개 파일:")
    for gf, out in outputs:
        print(f"  gf={gf} → {out}")


if __name__ == "__main__":
    main()
