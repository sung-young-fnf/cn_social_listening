# CN Social Listening

중국 SNS(도우인, 샤오홍슈) 계정 크롤링 및 S3 업로드 파이프라인

## 구조

```
cn-social-listening/
├── crawlers/                    # 크롤러
│   ├── douyin-weekly-v5.js      #   도우인 주간 크롤러 (Hyperbrowser + Puppeteer)
│   └── mediacrawler-config/     #   샤오홍슈 크롤러 설정 (MediaCrawler용)
│       ├── base_config.py
│       └── xhs_config.py
│
├── uploaders/                   # S3 업로드
│   ├── s3_upload_douyin_account.py  # 도우인 프로필 parquet + 이미지
│   ├── s3_upload_douyin_post.py     # 도우인 게시물 parquet + 썸네일
│   ├── s3_upload_xhs_account.py     # 샤오홍슈 프로필 parquet + 이미지
│   ├── s3_upload_xhs_post.py        # 샤오홍슈 게시물 parquet + 이미지
│   └── s3_list_bucket.py            # S3 버킷 조회 유틸리티
│
├── runners/                     # 주간 실행 래퍼 (크롤링 + 업로드 자동화)
│   ├── run_douyin_weekly.py
│   └── run_xhs_weekly.py
│
├── data/                        # 계정 목록 및 매핑
│   ├── douyin-accounts.json         # 도우인 계정 목록 (125개)
│   ├── douyin-secuid-map.json       # 닉네임 → sec_uid 매핑
│   ├── 도우인 계정 리스트.csv        # 도우인 PROFILE_TYPE 분류
│   └── 샤오홍슈 계정 리스트.csv      # 샤오홍슈 PROFILE_TYPE 분류
│
├── tools/                       # 유틸리티
│   └── generate_mst_profile.py      # MST_PROFILE 마스터 엑셀 생성
│
├── .env.example                 # API 키 템플릿
├── package.json                 # Node.js 의존성
└── requirements.txt             # Python 의존성
```

## 사전 준비

### 1. 환경 변수 설정

```bash
cp .env.example .env
# .env 파일에 실제 API 키 입력
```

| 변수 | 용도 |
|------|------|
| `HYPERBROWSER_API_KEY` | 도우인 크롤러 (브라우저 자동화) |
| `S3_API_KEY` | S3 presigned URL API |

### 2. Python 의존성 설치

```bash
pip install -r requirements.txt
```

### 3. Node.js 의존성 설치

```bash
npm install
```

### 4. 샤오홍슈 크롤러 설치 (MediaCrawler)

MediaCrawler는 별도 설치가 필요합니다:

```bash
# 1. MediaCrawler 클론
git clone https://github.com/NanmiCoder/MediaCrawler.git crawlers/MediaCrawler

# 2. 의존성 설치
cd crawlers/MediaCrawler
pip install -r requirements.txt

# 3. 설정 파일 덮어쓰기
cp ../mediacrawler-config/base_config.py config/base_config.py
cp ../mediacrawler-config/xhs_config.py config/xhs_config.py
```

## 사용법

### 주간 크롤링 + S3 업로드 (권장)

래퍼 스크립트로 크롤링부터 S3 업로드까지 한번에 실행합니다.

```bash
# 도우인 — 03/23 주차 크롤링 + 업로드
python runners/run_douyin_weekly.py --week 0323

# 샤오홍슈 — 03/23 주차 크롤링 + 업로드
python runners/run_xhs_weekly.py --week 0323
```

### 옵션

```bash
# 미리보기 (실제 실행 없이 확인)
python runners/run_douyin_weekly.py --week 0323 --dry-run

# 이미 크롤링 완료된 데이터 → 업로드만
python runners/run_douyin_weekly.py --week 0323 --upload-only

# 날짜 직접 지정
python runners/run_douyin_weekly.py --week 0323 --start 2026-03-23 --end 2026-03-29
```

### 개별 실행

필요 시 각 단계를 따로 실행할 수 있습니다.

```bash
# 도우인 크롤링만
node crawlers/douyin-weekly-v5.js

# 도우인 프로필 업로드
python uploaders/s3_upload_douyin_account.py output/douyin-weekly-0323-v5

# 도우인 게시물 업로드
python uploaders/s3_upload_douyin_post.py output/douyin-weekly-0323-v5

# 샤오홍슈 게시물 업로드
python uploaders/s3_upload_xhs_post.py output/red-weekly-260323

# S3 버킷 조회
python uploaders/s3_list_bucket.py douyin/profile/post/ 20
```

### MST_PROFILE 마스터 엑셀 생성

대시보드 연동을 위한 마스터 테이블 엑셀을 생성합니다.

```bash
python tools/generate_mst_profile.py
```

## S3 저장 경로

버킷: `svc-fnf-cn-mkt-s3`

### 도우인

| 구분 | 경로 | p_keyword |
|------|------|-----------|
| 프로필 | `douyin/account/p_year={YYYY}/p_month={MM}/p_day={DD}/p_keyword={nickname}/` | nickname |
| 게시물 | `douyin/profile/post/p_year={YYYY}/p_month={MM}/p_day={DD}/p_keyword={uniqueId}/` | uniqueId |
| 프로필 이미지 | `douyin/account/image/{nickname}/` | - |
| 게시물 썸네일 | `douyin/profile/image/{uniqueId}/{awemeId}/` | - |

### 샤오홍슈

| 구분 | 경로 | p_keyword |
|------|------|-----------|
| 게시물 | `xiaohongshu/profile/post/p_year={YYYY}/p_month={MM}/p_day={DD}/p_keyword={user_id}/` | user_id |
| 게시물 이미지 | `xiaohongshu/profile/image/{user_id}/{note_id}/` | - |

### Parquet 스키마 (19 컬럼)

| 컬럼 | 타입 | 설명 |
|------|------|------|
| keyword | string | PROFILE_ID (p_keyword와 동일) |
| author | string | 작성자명 |
| content | string | 게시물 본문 |
| likes | int64 | 좋아요 수 |
| stars | int64 | 즐겨찾기/수집 수 |
| comments | int64 | 댓글 수 |
| images_captured | int64 | 캡처된 이미지 수 |
| post_date | string | 게시일 (yyyy-mm-dd) |
| location | string | 위치 |
| post_type | string | 게시물 유형 (동영상/일반) |
| recommendations | int64 | 추천 수 |
| shares | int64 | 공유 수 |
| key | string | 고유 키 |
| timestamp | string | 수집 시각 |
| note_title | string | 노트 제목 |
| note_text | string | 노트 본문 |
| unique_hash | string | 게시물 고유 ID |
| thumbnail_path | string | S3 썸네일 경로 |
| post_url | string | 원본 게시물 URL |

## 대시보드 연동

- 대시보드: `cntrend-dev.fnf.co.kr`
- S3 → Airflow External Table → Snowflake → 대시보드
- `MST_PROFILE` 테이블에 계정 마스터 INSERT 필요 (PROFILE_ID + PROFILE_TYPE)
- PROFILE_ID 기준으로 `DW_POST`와 JOIN

## 주의사항

- Windows 환경에서 한글 인코딩 문제 시 `python -X utf8` 옵션 사용
- 도우인 크롤러는 Hyperbrowser 유료 서비스 + Oxylabs 프록시 필요
- 샤오홍슈 크롤러는 로그인 쿠키 필요 (base_config.py의 COOKIES)
- 크롤링 결과는 `output/` 폴더에 저장되며 .gitignore로 제외됨
