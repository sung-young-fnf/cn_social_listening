29CM Best 월간 API 추적 결과

📡 핵심 API 엔드포인트
메인 상품 데이터 API (POST)
POST https://display-bff-api.29cm.co.kr/api/v1/plp/best/items
Content-Type: application/json
Request Body 구조:
json{
  "pageRequest": {
    "page": 1,
    "size": 100
  },
  "userSegment": {
    "gender": "F",       // F, M, ALL
    "age": "THIRTIES"    // TWENTIES, THIRTIES, FORTIES, FIFTIES, ALL
  },
  "facets": {
    "categoryFacetInputs": [
      {
        "largeId": 269100100,    // 대카테고리 코드
        "middleId": 269101100    // 중카테고리 코드 (선택)
      }
    ],
    "periodFacetInput": {
      "type": "MONTHLY",         // DAILY, WEEKLY, MONTHLY
      "order": "DESC"
    },
    "rankingFacetInput": {
      "type": "POPULARITY"       // POPULARITY, LIKE
    }
  }
}
보조 API (GET):

GET https://recommend-api.29cm.co.kr/api/v4/best/category-groups — 전체 카테고리 그룹 목록
GET https://recommend-api.29cm.co.kr/api/v4/best/categories?categoryList={largeId} — 중카테고리 목록


🏗️ API 호출 구조 (Next.js BFF)
브라우저 → www.29cm.co.kr (Next.js App Router, SSR)
           → display-bff-api.29cm.co.kr  (실제 데이터 API)
           → recommend-api.29cm.co.kr   (카테고리 메타)
페이지 최초 로드는 SSR(서버에서 호출)이고, 카테고리 탭 클릭 시 클라이언트에서 직접 POST 호출합니다.

✅ 수집 가능 여부 정리

필드명 | 설명 | 수집 가능 여부 | API 응답 필드 경로
VALUE | 고유 식별값 (직접 생성) | ✅ 직접 생성 | —
YEAR / MONTH / DAY | 수집 날짜 (직접 생성) | ✅ 직접 생성 | — 
MAIN_CATEGORY | 대카테고리 | ✅ 수집 가능 | itemEvent.eventProperties.largeCategoryName
MID_CATEGORY | 중카테고리 |✅ 수집 가능 | itemEvent.eventProperties.middleCategoryName
SUB_CATEGORY | 소카테고리 | ❌ 불가 | API 응답에 소카테고리 없음 | (대/중 2depth까지만 제공)
GENDER_FILTER | 성별 필터 (A/M/F) |✅ 수집 가능 | request body userSegment.gender 파라미터 값
RANKING | 상품 랭킹 | ✅ 수집 가능 | 응답 list 배열 인덱스 순서 (1~100위)
SEASON | 시즌 정보 | ❌ 불가 | API 응답에 season 필드 없음
BRAND | 브랜드명 | ✅ 수집 가능 | itemInfo.brandName
GENDER | 성별 (상품 자체) | ❌ 불가 | 개별 상품에 gender 필드 없음 (필터 값만 존재)
PRODUCT_NUMBER | 상품 번호 | ✅ 수집 가능 | itemId (= itemEvent.eventProperties.itemNo)
PRODUCT_NAME | 상품명 | ✅ 수집 가능 | itemInfo.productName
PRICE | 정가 | ✅ 수집 가능 | itemInfo.originalPrice
DISCOUNT_PRICE | 할인가 | ✅ 수집 가능 | itemInfo.sellPrice (쿠폰 미적용 할인가)
DISCOUNT_COUPON_VALUE | 쿠폰 할인율 | ⚠️ 간접 계산 | 직접 필드 없음. is_cart_coupon_item=true인 경우 (sellPrice - displayPrice) / sellPrice × 100으로 계산 가능
REVIEW_COUNT | 리뷰 수 | ✅ 수집 가능 | itemInfo.reviewCount
LIKE_COUNT | 좋아요 수 | ✅ 수집 가능 | itemInfo.likeCount
VIEW_COUNT | 조회수 | ❌ 불가 | API 응답에 조회수 필드 없음
SELL_COUNT | 판매수 | ❌ 불가 | API 응답에 판매수 필드 없음
IMAGE_URL | 상품 이미지 URL | ✅ 수집 가능 | itemInfo.thumbnailUrl
PRODUCT_NO | 상품 고유번호 | ✅ 수집 가능 | itemId (itemEvent.eventProperties.itemNo와 동일)