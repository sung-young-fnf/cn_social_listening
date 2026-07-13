"""무신사 브랜드 상품 크롤러 — brands.txt 브랜드들의 상품을 23컬럼 CSV로 수집.

2단계 (둘 다 순수 HTTP — 브라우저 불필요):
  [1단계] requests (빠름, 인증 X)
    - 상품 목록 : api.musinsa.com/api2/dp/v2/plp/goods   (nextPageUrl 체인 페이지네이션)
    - 랭킹      : api.musinsa.com/api2/dp/v1/brand/flagship/{brand}/ranking-goods
    - 좋아요    : like.musinsa.com/like/api/v2/liketypes/goods/counts  (POST 배치)
  [2단계] curl_cffi (상품당 HTTP 2회 — Playwright 제거)
    - 카테고리(MAIN/MID/SUB) + 시즌 : 상세 페이지 __NEXT_DATA__ 파싱
                                     (상세 HTML은 plain requests 403 → curl_cffi TLS 위장으로 우회)
    - 조회수 / 판매수               : goods-detail.musinsa.com/api2/goods/{no}/stat
                                     (pageViewTotal/purchaseTotal 원본 정확값 — 옛 DOM 버킷텍스트보다 정밀)

사용법:
    python crawl_musinsa/crawl_brands.py                  # brands.txt 전체 (1+2단계)
    python crawl_musinsa/crawl_brands.py --no-detail      # 1단계만 (빠름)
    python crawl_musinsa/crawl_brands.py --brand trillion --max-pages 1 --detail-limit 5
    python crawl_musinsa/crawl_brands.py --no-proxy       # 회사 IP 직접

출력:
    crawl_musinsa/output/musinsa_products_YYYYMMDD.csv
"""
import argparse
import csv
import io
import json
import os
import re
import secrets
import sys
import time
from datetime import datetime
from urllib.parse import urlparse, parse_qs

import requests
from curl_cffi import requests as cffi

# UTF-8 콘솔 고정 (한글 깨짐 방지)
sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", line_buffering=True)

try:
    from dotenv import load_dotenv
    ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
    load_dotenv(os.path.join(ROOT, ".env"))
except ImportError:
    pass

HERE = os.path.dirname(os.path.abspath(__file__))
BRANDS_FILE = os.path.join(HERE, "brands.txt")
OUTPUT_DIR = os.path.join(HERE, "output")

UA = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
      "(KHTML, like Gecko) Chrome/126.0.0.0 Safari/537.36")
HEADERS = {
    "User-Agent": UA,
    "Accept": "application/json, text/plain, */*",
    "Accept-Language": "ko-KR,ko;q=0.9,en;q=0.8",
    "Origin": "https://www.musinsa.com",
    "Referer": "https://www.musinsa.com/",
    "Content-Type": "application/json",
}

# 운영 schema (23컬럼).
COLUMNS = [
    "VALUE", "YEAR", "MONTH", "DAY",
    "MAIN_CATEGORY", "MID_CATEGORY", "SUB_CATEGORY",
    "GENDER_FILTER", "RANKING", "SEASON",
    "BRAND", "GENDER", "PRODUCT_NUMBER", "PRODUCT_NAME",
    "PRICE", "DISCOUNT_PRICE", "DISCOUNT_COUPON_VALUE",
    "REVIEW_COUNT", "LIKE_COUNT", "VIEW_COUNT", "SELL_COUNT",
    "IMAGE_URL", "PRODUCT_NO",
    # === 상세 base API 확장 (goods base=소재속성 / tags=연관태그 / essential=상품고시) ===
    "TAGS", "MATERIAL",
    "NOTICE_MATERIAL", "NOTICE_COLOR", "NOTICE_SIZE",
    "PRODUCT_URL",   # 상품 상세 페이지 주소 (PRODUCT_NO 로 생성)
]

# VALUE 컬럼: 상품 raw 데이터를 {c1..cN} JSON으로 저장.
# c1=MAIN_CATEGORY ... c19=PRODUCT_NO (VALUE/YEAR/MONTH/DAY 제외한 데이터 컬럼).
VALUE_KEY_COLUMNS = COLUMNS[4:]


def fill_value(row):
    """row의 데이터 컬럼을 {c1, c2, ...} JSON 문자열로 만들어 VALUE에 저장.
    정수는 89,000 처럼 천단위 콤마 포맷 (스크린샷 형식)."""
    obj = {}
    for i, col in enumerate(VALUE_KEY_COLUMNS, 1):
        v = row.get(col, "")
        if isinstance(v, int):
            v = f"{v:,}"
        elif v is None:
            v = ""
        obj[f"c{i}"] = str(v)
    row["VALUE"] = json.dumps(obj, ensure_ascii=False)


# === Oxylabs 프록시 자격증명 (grab_xhs.build_proxy 패턴) ===
def _oxylabs_username():
    user = os.getenv("OXYLABS_USERNAME")
    pwd = os.getenv("OXYLABS_PASSWORD")
    if not user or not pwd:
        print("[FAIL] OXYLABS_USERNAME/PASSWORD 없음 (.env 확인). --no-proxy로 직접 호출 가능.")
        sys.exit(1)
    country = os.getenv("OXYLABS_COUNTRY", "kr")
    base = user if "-cc-" in user else f"{user}-cc-{country}"
    sessid = f"musinsa_{secrets.token_hex(4)}"
    sesstime = os.getenv("OXYLABS_SESSTIME", "30")
    return f"{base}-sessid-{sessid}-sesstime-{sesstime}", pwd, country


def build_proxies_requests():
    """requests용 {http,https} dict."""
    username, pwd, country = _oxylabs_username()
    host = os.getenv("OXYLABS_HOST", "pr.oxylabs.io")
    port = os.getenv("OXYLABS_PORT", "7777")
    url = f"http://{username}:{pwd}@{host}:{port}"
    print(f"[proxy:requests] country={country} {host}:{port}")
    return {"http": url, "https": url}


# === brands.txt 파싱 → [(slug, gf), ...] ===
def parse_brand_line(line):
    line = line.strip()
    if not line or line.startswith("#"):
        return None, None
    gf = "A"
    if line.startswith("http"):
        parsed = urlparse(line)
        parts = [p for p in parsed.path.split("/") if p]
        slug = None
        if "brand" in parts:
            i = parts.index("brand")
            if i + 1 < len(parts):
                slug = parts[i + 1]
        q = parse_qs(parsed.query)
        if "gf" in q and q["gf"]:
            gf = q["gf"][0]
        return slug, gf
    toks = line.split()
    slug = toks[0]
    if len(toks) > 1:
        gf = toks[1]
    return slug, gf


def load_brands(path):
    out, seen = [], set()
    with open(path, "r", encoding="utf-8") as f:
        for line in f:
            slug, gf = parse_brand_line(line)
            if slug and (slug, gf) not in seen:
                seen.add((slug, gf))
                out.append((slug, gf))
    return out


# === [1단계] requests API ===
def get_json(session, url, params=None, proxies=None, retries=3):
    for attempt in range(retries):
        try:
            r = session.get(url, params=params, proxies=proxies, timeout=25)
            if r.status_code == 200:
                return r.json()
            print(f"    ! {url} status={r.status_code} (시도 {attempt+1})")
        except Exception as e:
            print(f"    ! {url} 실패: {e} (시도 {attempt+1})")
        time.sleep(1.5 * (attempt + 1))
    return None


def fetch_goods_list(session, brand, gf, proxies, max_pages, delay,
                     sort_code="POPULAR", top=0):
    """plp/goods 페이지네이션. page>1은 hmacId(서버 서명) 필수 →
    page1만 params로 호출, 이후는 응답의 nextPageUrl 체인을 그대로 따라감.
    sort_code: POPULAR(추천순) / SALE_ONE_MONTH_COUNT(판매수량순 1개월) 등.
    top: 상위 N개만 수집 (0=제한없음). N 채우면 조기 종료."""
    goods = []
    page = 1
    url = "https://api.musinsa.com/api2/dp/v2/plp/goods"
    params = {"brand": brand, "gf": gf, "sortCode": sort_code,
              "page": 1, "size": 60, "caller": "FLAGSHIP"}
    while True:
        if max_pages and page > max_pages:
            break
        data = get_json(session, url, params=params, proxies=proxies)
        if not data:
            break
        d = data.get("data") or {}
        lst = d.get("list") or []
        if not lst:
            break
        goods.extend(lst)
        pg = d.get("pagination") or {}
        print(f"    page {page}/{pg.get('totalPages') or '?'} → {len(lst)}개 "
              f"(누적 {len(goods)}/{pg.get('totalCount') or '?'})")
        if top and len(goods) >= top:
            goods = goods[:top]
            break
        if not pg.get("hasNext"):
            break
        next_url = pg.get("nextPageUrl")
        if not next_url:
            break
        url, params = next_url, None
        page += 1
        time.sleep(delay)
    return goods


def fetch_ranking(session, brand, gf, proxies):
    """{goodsNo(str): rank(int)}. REALTIME만 데이터 존재(상위 100)."""
    data = get_json(
        session,
        f"https://api.musinsa.com/api2/dp/v1/brand/flagship/{brand}/ranking-goods",
        params={"sortCode": "REALTIME", "size": 100, "gf": gf}, proxies=proxies)
    rank_map = {}
    if data:
        for idx, g in enumerate((data.get("data") or {}).get("goodsList") or [], 1):
            no = str(g.get("goodsNo", ""))
            if no:
                rank_map[no] = idx
    return rank_map


def fetch_like_counts(session, goods_nos, proxies, batch=80, delay=0.5):
    """{goodsNo(str): count}. POST 배치."""
    like_map = {}
    url = "https://like.musinsa.com/like/api/v2/liketypes/goods/counts"
    for i in range(0, len(goods_nos), batch):
        chunk = [str(n) for n in goods_nos[i:i + batch]]
        try:
            r = session.post(url, json={"relationIds": chunk}, proxies=proxies, timeout=25)
            items = (((r.json().get("data") or {}).get("contents") or {}).get("items")) or []
            for it in items:
                like_map[str(it.get("relationId"))] = it.get("count", "")
        except Exception as e:
            print(f"    ! like 배치 실패: {e}")
        time.sleep(delay)
    return like_map


def build_row(g, brand_slug, gf, rank_map, like_map, ymd):
    no = str(g.get("goodsNo", ""))
    y, m, d = ymd
    return {
        "VALUE": "",  # 마지막에 fill_value()로 c1..cN JSON 채움
        "YEAR": y, "MONTH": m, "DAY": d,
        "MAIN_CATEGORY": "", "MID_CATEGORY": "", "SUB_CATEGORY": "",  # 2단계에서 채움
        "GENDER_FILTER": gf,
        "RANKING": rank_map.get(no, ""),
        "SEASON": "",                                                # 2단계
        "BRAND": g.get("brandName") or brand_slug,
        "GENDER": g.get("displayGenderText", ""),
        "PRODUCT_NUMBER": no,
        "PRODUCT_NAME": g.get("goodsName", ""),
        "PRICE": g.get("normalPrice", ""),
        "DISCOUNT_PRICE": g.get("price", ""),
        "DISCOUNT_COUPON_VALUE": g.get("saleRate", ""),
        "REVIEW_COUNT": g.get("reviewCount", ""),
        "LIKE_COUNT": like_map.get(no, ""),
        "VIEW_COUNT": "", "SELL_COUNT": "",                          # 2단계
        "IMAGE_URL": g.get("thumbnail", ""),
        "PRODUCT_NO": no,
        "PRODUCT_URL": f"https://www.musinsa.com/products/{no}" if no else "",
    }


# === [2단계] 상세 보강 — 순수 HTTP (curl_cffi), Playwright 제거 ===
# 상세 페이지(www.musinsa.com/products/{no}) HTML 은 plain requests 로는 403 →
# curl_cffi 의 TLS 지문 위장(impersonate=chrome)으로 우회. 조회/판매는 stat API 원본값.
_NEXT_RE = re.compile(r'<script id="__NEXT_DATA__"[^>]*>(.*?)</script>', re.S)


def _gender_from_meta(meta):
    """meta.data.genders(['M','W'] 등) → '공용'/'남성'/'여성'. 없으면 빈 문자열.
    랭킹 수집처럼 목록에 성별이 없을 때 상세에서 보강하는 용도."""
    g = set(meta.get("genders") or [])
    if {"M", "W"} <= g:
        return "공용"
    if g == {"M"}:
        return "남성"
    if g == {"W"}:
        return "여성"
    if g == {"K"}:  # 키즈(추정)
        return "키즈"
    return ""


def _parse_next_data(html):
    """상세 HTML __NEXT_DATA__ → {main, mid, sub, season, gender}. 실패 시 빈 값."""
    out = {"main": "", "mid": "", "sub": "", "season": "", "gender": ""}
    m = _NEXT_RE.search(html)
    if not m:
        return out
    try:
        meta = (((json.loads(m.group(1)).get("props") or {})
                 .get("pageProps") or {}).get("meta") or {}).get("data") or {}
    except (json.JSONDecodeError, ValueError):
        return out
    cat = meta.get("category") or {}
    out["main"] = cat.get("categoryDepth1Name") or cat.get("categoryDepth1Title") or ""
    out["mid"] = cat.get("categoryDepth2Name") or cat.get("categoryDepth2Title") or ""
    out["sub"] = cat.get("categoryDepth3Name") or cat.get("categoryDepth3Title") or ""
    sy, sn = meta.get("seasonYear") or "", meta.get("season") or ""
    out["season"] = f"{sy}/{sn}" if (sy or sn) else ""
    out["gender"] = _gender_from_meta(meta)
    return out


# === 상세 base API 확장 필드 (연관태그·소재속성·상품고시 일부) ===
# goods-detail.musinsa.com/api2/goods/{no}          → goodsMaterial(소재 속성)
# goods-detail.musinsa.com/api2/goods/{no}/tags     → 연관 태그(data.tags)
# goods-detail.musinsa.com/api2/goods/{no}/essential → 상품 고시(제품소재/색상/치수)

# enrich 로 채우는 확장 컬럼 (빈 값 기본). build_row 는 몰라도 되고 여기서 in-place 주입.
EXTRA_COLUMNS = [
    "TAGS", "MATERIAL",
    "NOTICE_MATERIAL", "NOTICE_COLOR", "NOTICE_SIZE",
]


def _map_essentials(essentials):
    """/essential 의 name/value 배열 → NOTICE_* 컬럼 매핑. 이름 변형(∙ 등) 관대 매칭.
    상품 고시 정보안내 중 제품소재/색상/치수만 수집."""
    out = {}
    for e in essentials or []:
        name = e.get("name", "") or ""
        val = e.get("value", "") or ""
        if "소재" in name:
            out["NOTICE_MATERIAL"] = val
        elif "색상" in name:
            out["NOTICE_COLOR"] = val
        elif "치수" in name:
            out["NOTICE_SIZE"] = val
    return out


def _parse_material(goods_material):
    """goodsMaterial.materials 의 부위별 isSelected=true 속성만 요약.
    예: '핏:오버/사이즈 | 촉감:보통 | 계절:봄/여름/가을/겨울'."""
    parts = []
    for part in (goods_material or {}).get("materials", []) or []:
        sel = [i.get("name", "") for i in part.get("items", []) if i.get("isSelected")]
        if sel:
            parts.append(f"{part.get('name', '')}:{'/'.join(sel)}")
    return " | ".join(parts)


def fetch_goods_extra(sess, no, hdr, proxies):
    """base goods(소재속성) + tags(연관태그) + essential(상품고시) 수집 → dict.
    실패해도 빈 dict 유지 (부분 실패 허용)."""
    out = {c: "" for c in EXTRA_COLUMNS}
    # 1) base goods → 소재 속성(핏/촉감/신축성 등)
    try:
        gr = sess.get(f"https://goods-detail.musinsa.com/api2/goods/{no}",
                      headers=hdr, proxies=proxies, timeout=30)
        if gr.status_code == 200:
            d = gr.json().get("data") or {}
            out["MATERIAL"] = _parse_material(d.get("goodsMaterial"))
    except Exception:
        pass
    # 2) tags → 연관 태그
    try:
        tr = sess.get(f"https://goods-detail.musinsa.com/api2/goods/{no}/tags",
                      headers=hdr, proxies=proxies, timeout=20)
        if tr.status_code == 200:
            tags = ((tr.json().get("data") or {}).get("tags")) or []
            out["TAGS"] = " ".join(f"#{t}" for t in tags if t)
    except Exception:
        pass
    # 3) essential → 상품 고시 (제품소재/색상/치수)
    try:
        er = sess.get(f"https://goods-detail.musinsa.com/api2/goods/{no}/essential",
                      headers=hdr, proxies=proxies, timeout=20)
        if er.status_code == 200:
            essentials = ((er.json().get("data") or {}).get("essentials")) or []
            out.update(_map_essentials(essentials))
    except Exception:
        pass
    return out


def enrich_details_http(rows, proxies, delay, limit):
    """각 row 의 PRODUCT_NO 에 대해 상세 __NEXT_DATA__(카테고리/시즌) + stat API
    (조회수/판매수 원본값) 를 HTTP 로 받아 채운다 (in-place). 브라우저 없음.

    상세 HTML 은 plain requests 가 403 이라 curl_cffi(impersonate=chrome)로 페치한다.
    VIEW_COUNT/SELL_COUNT 는 stat API 의 pageViewTotal/purchaseTotal 원본 정수값
    (옛 Playwright DOM 버킷텍스트 "10만 회 이상" 보다 정밀)."""
    targets = rows[:limit] if limit else rows
    total = len(targets)
    print(f"\n[2단계] 상세 보강 {total}개 (HTTP, curl_cffi — Playwright 없음)")

    sess = cffi.Session(impersonate="chrome")
    hdr = {"Accept": "application/json", "Accept-Language": "ko-KR,ko;q=0.9",
           "Origin": "https://www.musinsa.com", "Referer": "https://www.musinsa.com/"}
    ok = fail = 0
    consecutive_fail = 0
    CIRCUIT_BREAK = 15  # 연속 실패 임계 — 차단/네트워크 이상 조기 감지
    try:
        for idx, row in enumerate(targets, 1):
            no = row["PRODUCT_NO"]
            succeeded = False
            # 1) 상세 HTML → 카테고리/시즌 (__NEXT_DATA__)
            try:
                dr = sess.get(f"https://www.musinsa.com/products/{no}",
                              headers={"Accept-Language": "ko-KR,ko;q=0.9"},
                              proxies=proxies, timeout=30)
                if dr.status_code == 200:
                    d = _parse_next_data(dr.text)
                    row["MAIN_CATEGORY"] = d["main"]
                    row["MID_CATEGORY"] = d["mid"]
                    row["SUB_CATEGORY"] = d["sub"]
                    row["SEASON"] = d["season"]
                    # GENDER 는 목록에서 이미 채웠으면(브랜드 수집) 유지, 비었으면
                    # (랭킹 수집) 상세 sex 로 보강.
                    if not row.get("GENDER"):
                        row["GENDER"] = d["gender"]
                    succeeded = True
                else:
                    print(f"  [{idx}/{total}] {no} 상세 status={dr.status_code}")
            except Exception as e:
                print(f"  [{idx}/{total}] {no} 상세 실패: {str(e)[:50]}")
            # 2) stat API → 조회수/판매수 (원본 정확값)
            try:
                sr = sess.get(f"https://goods-detail.musinsa.com/api2/goods/{no}/stat",
                              headers=hdr, proxies=proxies, timeout=30)
                if sr.status_code == 200:
                    stat = (sr.json().get("data") or {})
                    row["VIEW_COUNT"] = stat.get("pageViewTotal", "")
                    row["SELL_COUNT"] = stat.get("purchaseTotal", "")
                    succeeded = True
                else:
                    print(f"  [{idx}/{total}] {no} stat status={sr.status_code}")
            except Exception as e:
                print(f"  [{idx}/{total}] {no} stat 실패: {str(e)[:50]}")
            # 3) base goods + tags → 연관태그/소재/판매자고시/상세이미지
            try:
                extra = fetch_goods_extra(sess, no, hdr, proxies)
                row.update(extra)
                if any(extra.values()):
                    succeeded = True
            except Exception as e:
                print(f"  [{idx}/{total}] {no} extra 실패: {str(e)[:50]}")

            if succeeded:
                ok += 1
                consecutive_fail = 0
                if idx <= 3 or idx % 50 == 0:
                    print(f"  [{idx}/{total}] {no} → {row['MAIN_CATEGORY']}/{row['MID_CATEGORY']} "
                          f"시즌={row['SEASON']} 조회={row['VIEW_COUNT']} 판매={row['SELL_COUNT']}")
            else:
                fail += 1
                consecutive_fail += 1
                if consecutive_fail >= CIRCUIT_BREAK:
                    print(f"  ✗ 연속 {CIRCUIT_BREAK}개 실패 — 차단 추정. "
                          f"2단계 조기 중단 (성공 {ok}개는 보존)")
                    break
            time.sleep(delay)
    except KeyboardInterrupt:
        print(f"\n  [중단] Ctrl+C — 2단계 {ok}개까지 처리, 정리 후 저장")
    print(f"[2단계 완료] 성공 {ok}, 실패 {fail}")


def save_csv(all_rows, now, prefix="musinsa_products"):
    """VALUE(c1..cN) 채우고 CSV 저장. 파일 잠김 시 시각 붙여 새 파일. out_path 반환.
    부분 데이터도 안전하게 저장되도록 모든 종료 경로(finally)에서 호출됨.
    prefix 로 출력 파일명 구분(브랜드=musinsa_products, 랭킹=musinsa_ranking 등)."""
    for row in all_rows:
        fill_value(row)
    os.makedirs(OUTPUT_DIR, exist_ok=True)
    out_path = os.path.join(OUTPUT_DIR, f"{prefix}_{now.strftime('%Y%m%d')}.csv")

    def _write(path):
        with open(path, "w", encoding="utf-8-sig", newline="") as f:
            w = csv.DictWriter(f, fieldnames=COLUMNS)
            w.writeheader()
            w.writerows(all_rows)

    try:
        _write(out_path)
    except PermissionError:
        # 기존 파일이 다른 앱(엑셀/미리보기)에서 열려 잠김 → 시각 붙여 새 파일
        out_path = os.path.join(
            OUTPUT_DIR, f"{prefix}_{now.strftime('%Y%m%d_%H%M%S')}.csv")
        print(f"  ⚠ 기존 CSV 잠김 — 새 파일로 저장: {os.path.basename(out_path)}")
        _write(out_path)
    return out_path


# 판매수량순 기간 → (sortCode, 파일 suffix, 라벨). API 실측 검증된 값.
SALES_SORT = {
    "1m": ("SALE_ONE_MONTH_COUNT", "sale_1m", "판매수량순 1개월"),
    "3m": ("SALE_THREE_MONTH_COUNT", "sale_3m", "판매수량순 3개월"),
    "1y": ("SALE_ONE_YEAR_COUNT", "sale_1y", "판매수량순 1년"),
}


def collect_brands(session, brands, proxies, args, now, ymd,
                   sort_code, top, rank_mode, prefix):
    """브랜드 목록을 sort_code로 수집→랭킹→좋아요→상세→CSV 저장 (out_path 반환).
    rank_mode: 'realtime'=브랜드 REALTIME 랭킹 매핑 / 'list'=정렬 리스트 순서를 RANKING으로."""
    all_rows = []
    for slug, gf in brands:
        print(f"\n=== [1단계][{slug}] gf={gf} sort={sort_code} top={top or '전체'} ===")
        goods = fetch_goods_list(session, slug, gf, proxies, args.max_pages,
                                 args.delay, sort_code=sort_code, top=top)
        if not goods:
            print(f"  ⚠ 상품 없음 — slug 확인 필요")
            continue
        if rank_mode == "list":
            # 정렬 리스트 순서 = 랭킹 (판매수량순 1위, 2위 ...)
            rank_map = {str(g.get("goodsNo")): i for i, g in enumerate(goods, 1)
                        if g.get("goodsNo")}
        else:
            rank_map = fetch_ranking(session, slug, gf, proxies)
            print(f"  랭킹 {len(rank_map)}개 매핑")
        nos = [str(g.get("goodsNo")) for g in goods if g.get("goodsNo")]
        like_map = fetch_like_counts(session, nos, proxies)
        print(f"  좋아요 {len(like_map)}개 수집")
        for g in goods:
            all_rows.append(build_row(g, slug, gf, rank_map, like_map, ymd))
        print(f"  → {slug}: {len(goods)}개 행")

    if not all_rows:
        print("\n[결과] 수집된 행 없음")
        return None

    # [2단계] 상세 enrich — 무엇이 터져도 finally에서 1단계 데이터까지 저장
    out_path = None
    try:
        if not args.no_detail:
            enrich_details_http(all_rows, proxies, args.detail_delay, args.detail_limit)
    except KeyboardInterrupt:
        print("\n[중단] 사용자 Ctrl+C — 여기까지 모은 데이터 저장")
        save_csv(all_rows, now, prefix=prefix)
        raise
    except Exception as e:
        print(f"\n[2단계 오류] {type(e).__name__}: {str(e)[:120]}")
        print("  → 1단계 수집분은 그대로 저장 (2단계 일부 필드만 비어있을 수 있음)")
    finally:
        out_path = save_csv(all_rows, now, prefix=prefix)

    liked = sum(1 for r in all_rows if r["LIKE_COUNT"] != "")
    catd = sum(1 for r in all_rows if r["MAIN_CATEGORY"] != "")
    viewd = sum(1 for r in all_rows if r["VIEW_COUNT"] != "")
    print(f"\n[완료] {len(all_rows)}개 행 → {out_path}")
    print(f"  채움률 — LIKE {liked}, CATEGORY {catd}, VIEW {viewd} / {len(all_rows)}")
    return out_path


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--brand", help="단일 브랜드 slug (brands.txt 무시)")
    ap.add_argument("--gf", default="A", help="--brand와 함께 쓰는 성별필터 (A/M/F)")
    ap.add_argument("--sales", action="store_true",
                    help="판매수량순 기간별 수집 (기본 추천순 대신). --periods로 기간 지정")
    ap.add_argument("--periods", default="1m,3m,1y",
                    help="--sales 기간 (콤마): 1m=1개월, 3m=3개월, 1y=1년. 각 기간별 파일 분리")
    ap.add_argument("--top", type=int, default=0,
                    help="브랜드당 상위 N개 (0=전체). --sales 기본 100")
    ap.add_argument("--no-proxy", action="store_true")
    ap.add_argument("--max-pages", type=int, default=0, help="브랜드당 목록 페이지 상한 (0=전체)")
    ap.add_argument("--delay", type=float, default=1.0, help="요청 간 딜레이(초)")
    ap.add_argument("--no-detail", action="store_true", help="2단계(상세 페이지) 생략")
    ap.add_argument("--detail-limit", type=int, default=0, help="2단계 상품 수 상한 (0=전체, 테스트용)")
    ap.add_argument("--detail-delay", type=float, default=0.8, help="상세 페이지 간 딜레이(초)")
    args = ap.parse_args()

    if args.brand:
        brands = [(args.brand, args.gf)]
    else:
        if not os.path.exists(BRANDS_FILE):
            print(f"[FAIL] {BRANDS_FILE} 없음")
            sys.exit(1)
        brands = load_brands(BRANDS_FILE)
    if not brands:
        print("[FAIL] 대상 브랜드 없음 (brands.txt 확인)")
        sys.exit(1)

    use_proxy = not args.no_proxy
    proxies = build_proxies_requests() if use_proxy else None
    session = requests.Session()
    session.headers.update(HEADERS)

    now = datetime.now()
    ymd = (now.year, now.month, now.day)
    print(f"\n대상 브랜드 {len(brands)}개: {[b[0] for b in brands]}")

    if args.sales:
        # 판매수량순 기간별 — 기간마다 별도 파일 (top 기본 100)
        top = args.top or 100
        periods = [p.strip() for p in args.periods.split(",") if p.strip()]
        outputs = []
        for pkey in periods:
            if pkey not in SALES_SORT:
                print(f"  ⚠ 알 수 없는 기간 '{pkey}' (1m/3m/1y 중 선택) — 건너뜀")
                continue
            sort_code, suffix, label = SALES_SORT[pkey]
            print(f"\n########## {label} top{top} ##########")
            out = collect_brands(session, brands, proxies, args, now, ymd,
                                 sort_code, top, "list", f"musinsa_products_{suffix}")
            if out:
                outputs.append((label, out))
        print(f"\n[전체 완료] {len(outputs)}개 파일:")
        for label, out in outputs:
            print(f"  {label} → {out}")
    else:
        # 기존 추천순(POPULAR) 흐름 — REALTIME 랭킹 매핑
        collect_brands(session, brands, proxies, args, now, ymd,
                       "POPULAR", args.top, "realtime", "musinsa_products")


if __name__ == "__main__":
    main()
