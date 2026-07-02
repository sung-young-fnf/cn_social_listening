무신사 브랜드 상품 데이터 크롤링 - 사전 분석 정리

1. 수집 목표 데이터
무신사 브랜드 상품 목록 페이지(/brand/{brand}/products)에서 아래 필드들을 수집하는 것이 목표.
필드명설명VALUE고유 식별값 (직접 생성)YEAR / MONTH / DAY수집 날짜 (직접 생성)MAIN_CATEGORY대카테고리MID_CATEGORY중카테고리SUB_CATEGORY소카테고리GENDER_FILTER성별 필터 (A/M/F 등)RANKING상품 랭킹SEASON시즌 정보BRAND브랜드명GENDER성별PRODUCT_NUMBER상품 번호PRODUCT_NAME상품명PRICE정가DISCOUNT_PRICE할인가DISCOUNT_COUPON_VALUE쿠폰 할인율REVIEW_COUNT리뷰 수LIKE_COUNT좋아요 수VIEW_COUNT조회수SELL_COUNT판매수IMAGE_URL상품 이미지 URLPRODUCT_NO상품 고유번호

2. API 분석 결과
2-1. 상품 목록 API (핵심)
엔드포인트: https://api.musinsa.com/api2/dp/v2/plp/goods

브랜드 페이지 스크롤 시 자동 호출되는 메인 API
페이지네이션 지원 (page, size 파라미터)

수집 가능 필드:
필드 | API 응답 키 | 비고
PRODUCT_NAME |goodsName |✅ |
PRODUCT_NUMBER | goodsNo | ✅ |
PRODUCT_NO | goodsNo | ✅ (동일)
PRICE | price |✅ |
DISCOUNT_PRICE | salePrice | ✅ |
DISCOUNT_COUPON_VALUE | saleRate | ✅ (할인율 %)| 
REVIEW_COUNT | reviewCount | ✅ | 
IMAGE_URL | thumbnail | ✅ | 
BRAND | brandName | ✅ |
GENDER | genderValues | ✅ |

2-2. 랭킹 API
엔드포인트: https://api.musinsa.com/api2/dp/v1/brand/flagship/{brand}/ranking-goods

쿼리 파라미터: sortCode=REALTIME&size=30&gf=A
브랜드 랭킹 순위 데이터 제공

수집 가능 필드:
필드 |비고
RANKING | ✅ 상품별 랭킹 순위 포함

2-3. 조회수 / 판매수 API (문제 있음)
엔드포인트: https://goods.musinsa.com/api2/goods/{goodsNo}/stat

응답 데이터: { pageViewTotal: 1291, purchaseTotal: 0 }
VIEW_COUNT = pageViewTotal
SELL_COUNT = purchaseTotal

문제:

goods.musinsa.com은 내부 도메인으로 브라우저에서 직접 호출 시 CORS 차단
Next.js SSR(서버사이드)에서 호출 후 React Query 캐시로 프론트에 전달하는 구조
서버(Python requests 등)에서 호출 시 CORS 제한 없음 → 수집 가능


2-4. 좋아요 수 API (파라미터 미확인)
엔드포인트: https://like.musinsa.com/like/api/v2/liketypes/goods/counts

React Fiber 탐색으로 likeCount: 59 데이터 존재 확인
API 자체는 확인됐으나 정확한 요청 파라미터 구조 미파악
20가지 이상 파라미터 조합 시도 → 모두 "잘못된 파라미터입니다" 오류
로그인 후 브라우저 네트워크 탭에서 실제 요청 캡처하면 파라미터 확인 가능


2-5. 카테고리 / 쿠폰 관련 API
엔드포인트내용/api2/dp/v1/categories카테고리 목록/available-brand-coupon?brandId=trillion브랜드 쿠폰 정보/label?gf=A&goodsNoList={ids}상품 라벨 정보

MAIN/MID/SUB_CATEGORY는 상품 상세 API에서 확인 필요 (목록 API에는 없음)
SEASON 데이터는 현재까지 어떤 API에서도 미확인


3. 필드별 수집 가능 여부 종합
필드 | 가능 여부 | 비고
VALUE | ✅ | 직접 생성
YEAR / MONTH / DAY | ✅ | 수집 시점 날짜로 직접 생성
PRODUCT_NAME | ✅ | 목록 API
PRODUCT_NUMBER | ✅ | 목록 API
PRODUCT_NO | ✅ | 목록 API
PRICE | ✅ | 목록 API
DISCOUNT_PRICE | ✅ | 목록 API
DISCOUNT_COUPON_VALUE | ✅ | 목록 API (saleRate)
REVIEW_COUNT | ✅ | 목록 API
IMAGE_URL | ✅ | 목록 API
BRAND | ✅ | 목록 API
GENDER | ✅ | 목록 API
GENDER_FILTER | ✅ | URL 파라미터(gf=A)
RANKING | ✅ | 랭킹 API
LIKE_COUNT | ⚠️ | like API 파라미터 확인 필요
VIEW_COUNT | ⚠️ | stat API, 서버에서만 호출 가능
SELL_COUNT | ⚠️ | stat API, 서버에서만 호출 가능
MAIN_CATEGORY | ❓ | 상품 상세 API 추가 확인 필요
MID_CATEGORY | ❓ | 상품 상세 API 추가 확인 필요
SUB_CATEGORY | ❓ | 상품 상세 API 추가 확인 필요
SEASON | ❌ | 현재까지 어떤 API에서도 미확인

4. 구현 전 해결해야 할 사항
① LIKE_COUNT 파라미터 확인

방법: 무신사 로그인 후 상품 페이지 접근 → 브라우저 네트워크 탭에서 like.musinsa.com 요청 캡처
목적: 정확한 쿼리 파라미터 구조 파악

② VIEW_COUNT / SELL_COUNT 서버 호출 테스트

방법: Python requests로 https://goods.musinsa.com/api2/goods/{goodsNo}/stat 직접 호출
확인 사항: 인증 없이도 응답 오는지, 차단 여부

③ MAIN/MID/SUB_CATEGORY 확인

방법: 상품 상세 API(/api2/goods/{goodsNo}) 응답 구조 확인

④ IP 차단 대응 전략

요청 간 딜레이 (1~3초)
User-Agent 헤더 설정
필요 시 프록시 로테이션


5. 크롤링 아키텍처 (예정)
1. 브랜드 목록 페이지 순회
   → /api2/dp/v2/plp/goods (페이지네이션)
   → 기본 상품 정보 수집

2. 상품별 상세 API 호출
   → goods.musinsa.com/api2/goods/{id}/stat
   → VIEW_COUNT, SELL_COUNT 수집

3. 좋아요 API 호출
   → like.musinsa.com/like/api/v2/liketypes/goods/counts
   → LIKE_COUNT 수집

4. 랭킹 API 호출
   → /api2/dp/v1/brand/flagship/{brand}/ranking-goods
   → RANKING 수집

5. 수집 데이터 병합 후 저장

이 정도가 현재까지 파악된 전체 상황입니다. 이걸 바탕으로 코드 구현 들어가면 될 것 같고, LIKE_COUNT 파라미터 확인이랑 stat API 서버 테스트가 구현 전에 선행되면 가장 깔끔할 것 같아요.