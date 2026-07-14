"""무신사 크롤링 대상 브랜드 목록.

BRANDS 리스트에 브랜드명(slug)만 한 줄씩 넣으면 된다.
  - 예: "adidas", "nike"
  - 성별필터가 필요하면 "adidas M" 처럼 뒤에 A/M/F (기본 A=전체)
  - 브랜드 페이지 URL 통째로 넣어도 slug·gf 자동 인식
비어 있으면 crawl_brands.py 는 brands.txt 를 대신 읽는다.
"""
BRANDS = [
  "lululemon",
  "thenorthface",
  "newbalance",
  "marithefrancoisgirbaud",
  "salomon",
  "sierradesigns",
  "adidas",
  "standoil",
  "margesherwood",
]
