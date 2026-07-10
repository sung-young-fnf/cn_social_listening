API 경로

GET https://goods-detail.musinsa.com/api2/goods/{goodsNo}

(이 상품은 goodsNo=5901521 → https://goods-detail.musinsa.com/api2/goods/5901521)

응답은 { meta: {...}, data: {...} } 구조이고, "상품 정보 더보기"와 관련된 필드는 data 안에 아래처럼 흩어져 있습니다.


필드명 | 내용
goodsContents | 상세 설명(이미지/HTML 본문)
company | 제조/판매자 고시 정보(상호, 대표자, 사업자번호, 통신판매신고번호, 주소, 연락처 등)
goodsMaterial | 소재 정보(부위별 혼용률 등)
specDesc | 사이즈/스펙 설명 텍스트
isGoodsFill / goodsFillInfo | "상품정보 제공고시" 표준 양식 사용 여부 및 그 내용(색상, 제조국, 세탁방법 등)
goodsLogisticsInfoV2 | 배송/반품/교환 관련 정보(택배사, 반품지 주소, 배송비 등)



연관 태그도 API로 바로 받아와집니다.
GET https://goods-detail.musinsa.com/api2/goods/6197079/tags
응답 구조는 data.tags에 문자열 배열로 들어있고



이번 상품의 실제 값은 이렇습니다.


## 예시 
goodsContents에는 이미지 3개(UPSO6G100BK.jpg, LFNT1.jpg, LFNT2.jpg, 도메인은 images.istockmall.com)를 감싼 짧은 HTML만 들어있고 별도 텍스트 설명은 없었습니다.

company는 상호 "주식회사 스탁컴퍼니", 대표자 "이영선", 사업자등록번호 "1268182701", 통신판매업신고번호 "2009-서울중구-1020", 전화 "18996670", 이메일 "stock_cs@naver.com", 주소 "서울 중구 소파로 141 (대한적십자) (남산동3가, 스타빌딩 3층)"으로 확인됩니다.

goodsMaterial은 materials: []로 비어있고, specDesc도 빈 문자열이라 이 상품은 소재/스펙 텍스트를 별도로 채워 넣지 않은 상태입니다. isGoodsFill이 false라 goodsFillInfo(표준 상품정보고시 양식)도 null입니다. 즉 이 특정 상품은 "상품 정보 더보기"에 이미지 3장과 사업자 고시 정보 정도만 노출되는 구조입니다.

goodsLogisticsInfoV2에는 기본 발송기간 3일, 반품택배사 "한진택배", 반품지 주소 "경기 안성시 원곡면 남북대로 1108 무신사 4센터 MFS", 편도 배송비 6,000원, 제주/도서산간 추가비 각 3,000원 같은 배송·반품 정책 값이 들어 있었습니다(배열의 첫 항목 기준이며 배송 조건별로 여러 항목이 있을 수 있습니다).
인증 없이 GET만으로 호출되므로, 다른 상품번호로 바꿔가며 동일한 구조로 데이터를 받아올 수 있습니다.
