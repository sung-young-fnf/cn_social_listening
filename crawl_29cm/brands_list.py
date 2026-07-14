"""29CM 크롤 대상 브랜드 목록.

⚠️ 29CM은 브랜드를 '숫자 brandId'로 식별한다 (무신사 slug와 다름).
각 항목은 (표시이름, brandId) 튜플.
  - brandId는 브랜드 페이지 URL의 brandId= 값:
    https://www.29cm.co.kr/store/brand/2178?brandId=2178  →  2178
  - 표시이름은 출력 CSV 파일명(29cm_<이름>_sale_날짜.csv)에만 쓰임.

부족한 브랜드는 29CM에서 검색 → 브랜드 페이지 URL의 brandId를 복사해 추가.
"""
BRANDS = [
    ("adidas", 2178),
    ("newbalance", 2177),
    ("thenorthface", 7789),
    ("salomon", 2799),
    ("sierradesigns", 20333),
    ("standoil", 5297),
    ("margesherwood", 2439),
    ("marithefrancoisgirbaud", 7001)
]


