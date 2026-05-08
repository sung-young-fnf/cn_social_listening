# cn-social-listening 구조 분석 리포트

중국 SNS(도우인 / 샤오홍슈) 주간 크롤링 → S3 업로드 파이프라인. **크롤링 동작 방식**을 중심으로 정리.

---

## 1. 전체 구조 한 눈에 보기

```
cn-social-listening/
├── crawlers/
│   ├── douyin-weekly-v5.js          ← 도우인 크롤러 본체 (Node.js, 자체 구현)
│   └── mediacrawler-config/         ← 샤오홍슈는 외부 OSS(MediaCrawler) 사용 → config만 보관
│       ├── base_config.py
│       └── xhs_config.py
├── runners/                          ← "크롤링 → 업로드" 한 번에 돌리는 래퍼
│   ├── run_douyin_weekly.py
│   └── run_xhs_weekly.py
├── data/                             ← 크롤링 입력 (계정 목록 / sec_uid 매핑)
│   ├── douyin-accounts.json          (125개)
│   ├── douyin-secuid-map.json        (닉네임 → sec_uid)
│   └── *.csv                         (PROFILE_TYPE 분류표)
├── uploaders/                        ← 크롤링 결과 → Parquet 변환 → S3 업로드
└── tools/generate_mst_profile.py     ← 마스터 엑셀 생성
```

핵심: **두 플랫폼의 크롤링 방식이 완전히 다름.**
- 도우인 = 자체 제작 Node.js 크롤러 (Hyperbrowser + Oxylabs + 브라우저 내 fetch)
- 샤오홍슈 = 외부 OSS [`MediaCrawler`](https://github.com/NanmiCoder/MediaCrawler) 그대로 사용 + config 덮어쓰기

---

## 2. 파이프라인 흐름 (runner 기준)

`runners/run_*_weekly.py`가 크롤러 설정 파일을 **정규식으로 직접 수정**한 뒤 자식 프로세스로 크롤러를 호출. 끝나면 업로더 2개를 순차 실행.

```
runners/run_douyin_weekly.py --week 0323
  ├─ (1) douyin-weekly-v5.js 의 outputDir / dateStart / dateEnd 를
  │      regex 치환으로 in-place 수정      ← runners/run_douyin_weekly.py:48-79
  ├─ (2) subprocess: node crawlers/douyin-weekly-v5.js
  ├─ (3) subprocess: python uploaders/s3_upload_douyin_account.py <폴더>
  └─ (4) subprocess: python uploaders/s3_upload_douyin_post.py    <폴더>

runners/run_xhs_weekly.py --week 0323
  ├─ (1) MediaCrawler/config/base_config.py 의 SAVE_DATA_PATH /
  │      CRAWLER_DATE_START / CRAWLER_DATE_END 를 regex 치환
  ├─ (2) subprocess: python main.py  (cwd=MEDIACRAWLER_DIR)
  ├─ (3) subprocess: python uploaders/s3_upload_xhs_account.py <폴더>
  └─ (4) subprocess: python uploaders/s3_upload_xhs_post.py    <폴더>
```

`--week 0323` → `2026-03-23 ~ 2026-03-29` 자동 계산 (`week_to_dates`). `--upload-only`로 업로드만 재시도 가능.

---

## 3. 도우인 크롤링 (`crawlers/douyin-weekly-v5.js`)

### 3.1 설계 철학 — "역할 분리로 비용 최소화"

v3는 Hyperbrowser(이하 HB) **올인** 구조였는데(~$82-95/주), v5에서 역할을 쪼개 **~75-80% 비용 절감**.

| 컴포넌트 | 역할 | 비고 |
|----------|------|------|
| **Hyperbrowser** | 세션 / 스텔스 / 캡차 풀이 | 프록시는 안 씀 |
| **Oxylabs 프록시** | HB 세션이 사용할 IP | 자체 보유 → 비용 $0 |
| **브라우저 내 `fetch`** | API 호출 (anti-bot 토큰 자동 포함) | 핵심 트릭 |
| **Node.js `https.request`** | 영상 CDN 직접 다운로드 | 프록시 미경유 → $0 |

세션 생성 시 두 개를 직접 묶어버림 (`crawlers/douyin-weekly-v5.js:419-425`):
```js
session = await client.sessions.create({
  useStealth: true, solveCaptchas: true,
  proxyServer: "pr.oxylabs.io:7777",
  proxyServerUsername: "customer-...", proxyServerPassword: "...",
  acceptCookies: true, locales: ["zh"], screen: { width: 1920, height: 1080 },
});
```

### 3.2 입력

- `data/douyin-accounts.json` — 닉네임 배열 (125개)
- `data/douyin-secuid-map.json` — `{ "닉네임": "sec_uid" }` 매핑. **sec_uid가 없는 계정은 자동으로 건너뜀** (`douyin-weekly-v5.js:605-614`).
  - sec_uid 추가 방법: 도우인 앱 프로필 공유 URL에서 추출 or `douyin.com` 검색 후 F12로 추출.

### 3.3 anti-bot 우회 핵심 트릭 — "브라우저 내 fetch"

도우인 API는 `X-Bogus`, `_signature` 같은 anti-bot 토큰을 요구. 이걸 직접 만들면 막힘.
대신 **브라우저 컨텍스트 안에서 `fetch`를 실행**하면, 도우인 페이지가 자동으로 토큰을 붙여줌. (`callApi` — `douyin-weekly-v5.js:103-123`)

```js
async function callApi(page, apiPath) {
  // 1) page.evaluate 안에서 fetch 호출 → 결과를 window.__api_xxx 에 저장
  await page.evaluate((p, k) => {
    fetch(p, { credentials: "include" })
      .then(r => r.text()).then(t => { window[k] = { ok:true, text:t }; });
  }, apiPath, key);

  // 2) 0.5s 간격으로 최대 20초 폴링하며 결과 수거
  for (let i = 0; i < 40; i++) {
    await sleep(500);
    const r = await page.evaluate(k => window[k], key);
    if (r) return JSON.parse(r.text);
  }
}
```

세션 시작 시 `https://www.douyin.com/`을 로드해서 쿠키 + JS 컨텍스트를 확보(`createSession` — `douyin-weekly-v5.js:435-442`). 오버레이/팝업은 `removeAllOverlays`로 z-index ≥1000 인 fixed/absolute 요소 강제 제거.

### 3.4 호출하는 도우인 내부 API

| 단계 | 엔드포인트 | 용도 |
|------|-----------|------|
| 프로필 | `/aweme/v1/web/user/profile/other/?sec_user_id=...` | 닉네임 / 팔로워 / 작품 수 / 아바타 (`collectProfile` — :140-162) |
| 게시물 리스트 | `/aweme/v1/web/aweme/post/?sec_user_id=...&max_cursor=...&count=35&sort_type={0\|1}` | 페이지네이션 (`paginateListApi` — :190-232) |
| 게시물 상세 | `/aweme/v1/web/aweme/detail/?aweme_id=...` | 영상 비트레이트 / CDN URL (`processAccount` Step 3 — :526-537) |

공통 파라미터: `device_platform=webapp`, `aid=6383`, `cookie_enabled=true`, `platform=PC`.

### 3.5 게시물 수집 로직 — "멀티소트 합집합"

한 정렬로는 누락 발생 → **`sort_type=0`(시간순) + `sort_type=1`(인기순)** 두 번 돌려서 `awemeId` 키로 합집합 (`collectWeeklyPosts` — :235-259).

페이지네이션 종료 조건 (`paginateListApi`):
- `data.has_more === false`
- 또는 **범위 이전(=date_start보다 오래된) 게시물이 연속 10개 등장** (`MAX_CONSECUTIVE_OLDER = 10`)
- 또는 페이지 15회 (`MAX_PAGES = 15`)
- 페이지 사이즈 35 (도우인 기본 20보다 큼)

날짜 범위는 `create_time` (unix sec)을 `DATE_START_TS / DATE_END_TS`와 비교. 범위 밖이면 스킵, 안이면 `buildPostObject`로 평탄화해서 push.

### 3.6 영상 다운로드 — "프록시 우회로 $0"

`/aweme/detail/` 응답의 `video.bit_rate[]`에서 화질 선택 (`selectVideoUrl` — :321-351, 기본 720p). 각 비트레이트는 여러 CDN URL을 갖고 있고, **`douyin.com` 도메인이 아닌 CDN을 우선**으로 정렬한 뒤 (`douyin-weekly-v5.js:324-326, 341-343`),

→ Node.js 의 `https.request`로 **직접 다운로드** (`downloadVideoDirect` — :358-405).

이 호출은 프록시를 안 타므로 트래픽 비용이 0. 헤더에 `Referer: https://www.douyin.com/` + Chrome UA만 박으면 통과. 10KB 미만이면 실패로 간주, 5회까지 redirect 추적, URL 리스트를 순서대로 시도해 첫 성공시 종료.

### 3.7 안정성 / 재시작 / 진행 상태

- **세션 분할**: 5계정마다 세션 재생성 (`maxAccountsPerSession: 5` — :42). 세션 도중 `Protocol`/`disconnect`/`closed` 에러 잡히면 즉시 세션 폐기 후 재생성 (:692-699).
- **재시도**: 세션 생성 자체는 3회까지 재시도 (`createSession(retries=3)` — :414).
- **레이트 리밋**: API 간 2s, 다운로드 간 1s, 계정 간 3s.
- **재시작**: `output/<폴더>/progress.json`에 계정별 status 저장. `skipExisting=true`면 `done` 계정 스킵 + 영상 파일 size > 10KB면 다운로드 스킵.
- **데이터 품질 분석**: `analyzeDateGaps`(:262-318)이 게시물 간 3일 이상 간격을 검출해 `gap_warnings.json`으로 저장 → 누락 의심 계정 식별.

### 3.8 산출물

```
output/douyin-weekly-{MMDD}-v5/
├── progress.json                     ← 계정별 상태 (재실행용)
├── summary.json                      ← 통계 / 비용 / 수집기간
├── gap_warnings.json                 ← 날짜 간격 경고
└── <계정명>/
    ├── data.json                     ← profile + posts[] (S3 업로더가 읽음)
    └── videos/<aweme_id>.mp4
```

---

## 4. 샤오홍슈 크롤링 (외부 MediaCrawler)

### 4.1 핵심 — 자체 구현 안 함

직접 만든 코드 없음. 오픈소스 [`NanmiCoder/MediaCrawler`](https://github.com/NanmiCoder/MediaCrawler)를 별도 클론(`crawlers/MediaCrawler/`)해서 사용.
이 레포가 관리하는 건 **`base_config.py` / `xhs_config.py` 두 개의 설정 파일**뿐 (`crawlers/mediacrawler-config/`).

설치 흐름 (README:67-81):
```bash
git clone https://github.com/NanmiCoder/MediaCrawler.git crawlers/MediaCrawler
cp ../mediacrawler-config/base_config.py crawlers/MediaCrawler/config/base_config.py
cp ../mediacrawler-config/xhs_config.py  crawlers/MediaCrawler/config/xhs_config.py
```

### 4.2 `base_config.py`로 통제하는 항목

| 키 | 값 | 의미 |
|----|----|----|
| `PLATFORM` | `"xhs"` | 샤오홍슈 모드 |
| `CRAWLER_TYPE` | `"creator"` | 크리에이터 홈피 모드 (검색/디테일이 아님) |
| `LOGIN_TYPE` | `"cookie"` | 쿠키 로그인 (`COOKIES`에 PC web 쿠키 박제) |
| `ENABLE_IP_PROXY` | `True` | Oxylabs 프록시 풀 사용 |
| `IP_PROXY_PROVIDER_NAME` | `"oxylabs"` | 도우인과 동일 프록시 |
| `IP_PROXY_POOL_COUNT` | `3` | 풀 크기 |
| `ENABLE_CDP_MODE` | `True` | 사용자 실제 Chrome/Edge에 CDP로 붙음 (anti-detection 강화) |
| `CDP_DEBUG_PORT` | `9222` | 점유 시 자동으로 다음 포트 시도 |
| `HEADLESS` | `False` | 헤드풀 (캡차 수동 통과 위해) |
| `SAVE_DATA_OPTION` | `"json"` | 결과 저장 형식 |
| `SAVE_DATA_PATH` | `output/red-weekly-{YYMMDD}` | runner가 매주 갱신 |
| `CRAWLER_MAX_NOTES_COUNT` | 100 | 상한 (날짜 필터로 조기 종료) |
| `CRAWLER_DATE_START / END` | yyyy-mm-dd | runner가 매주 갱신 |
| `MAX_CONCURRENCY_NUM` | 1 | 단일 동시성 (감지 회피) |
| `ENABLE_GET_MEIDAS` | `True` | 이미지/영상 같이 수집 |
| `ENABLE_GET_COMMENTS` | `False` | 댓글 미수집 |
| `CRAWLER_MAX_SLEEP_SEC` | 2 | 요청 간 sleep |

### 4.3 `xhs_config.py` — 수집 대상

`XHS_CREATOR_ID_LIST`에 **계정 프로필 URL을 하드코딩** (현재 ~210개, celeb 68 / influencer 75 / brand 15 / megapage 44).

```python
XHS_CREATOR_ID_LIST = [
    "https://www.xiaohongshu.com/user/profile/5842afd75e87e7332ea90fda",  # 虞书欣Esther
    ...
]
SORT_TYPE = "popularity_descending"
```

도우인과 다르게 **JSON 매핑 파일이 아니라 config 파일 자체에 박혀있음**. 계정 추가/제거는 이 파일 직접 수정.

### 4.4 runner의 처리

`run_xhs_weekly.py:51-83`이 `MediaCrawler/config/base_config.py`를 정규식으로 in-place 수정:
- `SAVE_DATA_PATH = "output/red-weekly-26{MMDD}"`
- `CRAWLER_DATE_START / CRAWLER_DATE_END`

수정 후 `python main.py`를 `cwd=MEDIACRAWLER_DIR`로 실행. 출력은 `MediaCrawler/output/red-weekly-26{MMDD}/` 아래.

---

## 5. 두 크롤러 비교 요약

| 항목 | 도우인 | 샤오홍슈 |
|------|--------|----------|
| 코드 소유 | **자체 구현** (Node.js, ~800줄) | 외부 OSS (MediaCrawler) |
| 브라우저 자동화 | Hyperbrowser + Puppeteer | Playwright + 사용자 Chrome (CDP) |
| 인증 | 페이지 로드로 자동 쿠키 확보 | `COOKIES` 문자열 박제 |
| 프록시 | Oxylabs (HB 세션에 직접 주입) | Oxylabs (MediaCrawler 풀) |
| anti-bot 우회 | 브라우저 내 `fetch`로 토큰 자동 포함 | MediaCrawler 내장 (xhs JS 시그니처 처리) |
| 캡차 | HB가 자동 풀이 | 사람이 수동 통과 (`HEADLESS=False`) |
| 계정 입력 | `data/*.json` 외부 파일 | `xhs_config.py` 하드코딩 |
| 페이지네이션 | `sort_type=0,1` 합집합 + cursor | MediaCrawler 내장 |
| 영상/미디어 다운로드 | Node.js HTTP 직접 ($0) | MediaCrawler 내장 (`ENABLE_GET_MEIDAS=True`) |
| 출력 위치 | `output/douyin-weekly-{MMDD}-v5/` | `MediaCrawler/output/red-weekly-26{MMDD}/` |
| 결과 포맷 | 계정별 `data.json` + `videos/*.mp4` | MediaCrawler json 스키마 |

---

## 6. 주의 / 알아둘 점

- **도우인은 sec_uid 의존**: `douyin-secuid-map.json`에 없으면 자동 스킵 → 신규 계정 추가 시 sec_uid 수집 선행 필요.
- **샤오홍슈는 쿠키 만료 시 멎음**: `base_config.py`의 `COOKIES`를 직접 갱신해야 함. CDP 모드라 사용자 PC에 Chrome 설치 필수.
- **runner가 설정 파일을 정규식으로 직접 덮어씀** → 수동으로 config를 편집한 게 있어도 매주 덮어써짐.
- **두 크롤러 모두 헤드풀 + Oxylabs 프록시** → 자동화 환경(서버) 이전 시 별도 작업 필요.
- 결과는 모두 `output/`(또는 MediaCrawler 쪽 `output/`)에 저장되며 `.gitignore` 처리.
- Hyperbrowser는 유료(세션/캡차 비용). v5에서 영상 트래픽을 빼서 ~$15-20/주 수준으로 감축됨 (`douyin-weekly-v5.js:780-789` summary.costEstimate).



python runners/run_xhs_weekly_local.py --week 0427


user_data_dir 정리 — "신선한 출발"

  지금 cdp_xhs_user_data_dir엔 봇으로 의심받은 세션 흔적이
  남아있습니다. 한 번 의심받으면 그 프로필은 한동안
  따라다닙니다. 깨끗하게 시작:

  Remove-Item -Recurse -Force
crawlers\MediaCrawler\browser_data\cdp_xhs_user_data_dir





 1. 프로젝트 구조 분석 + report.md 작성                       
   
  - 도우인 / 샤오홍슈 두 크롤러의 동작 방식 차이 정리          
  - 도우인은 자체 Node.js 구현 (Hyperbrowser + Oxylabs +
  브라우저 fetch), 샤오홍슈는 외부 OSS MediaCrawler 사용
  - S3에 뭐가 저장되는지(parquet 19컬럼 + 이미지) 도식화
  - 두 크롤러의 로그인 유지 방식 비교 (cookies.json vs
  user_data_dir)

  2. 로컬 테스트 환경 구축

  - runners/run_xhs_weekly_local.py 신규 작성
  - 기존 코드 안 건드리고 별도 러너로 분리
  - S3 업로드 대신 로컬 CSV 저장 (S3 parquet 스키마와 1:1 동일)
  - 결과물: test_post.csv (19컬럼) + test_account.csv (11컬럼)

  3. Oxylabs 프록시를 MediaCrawler에 적용

  - 진단: config는 oxylabs인데 실제 provider 코드가 없어서
  깨져있던 상태
  - proxy/providers/oxylabs_proxy.py 신규 작성 (도우인이랑 같은
   자격증명 재사용)
  - proxy/types.py에 enum 추가, proxy/proxy_ip_pool.py에 등록
  - 백업본을 mediacrawler-config/에도 보관 (재설치 대비)

  4. TLS 핑거프린팅 우회 적용 (Challenge #1)

  - tools/httpx_util.py를 curl_cffi 기반으로 교체
  - impersonate="chrome120"으로 JA3/JA4 위조 → Python TLS
  시그널 제거
  - httpx 폴백 유지 (curl_cffi 없으면 자동 전환)

  5. MediaCrawler 누락 config 보강

  실행 중 발견된 빠진 항목들 추가:
  - XHS_INTERNATIONAL = False (xiaohongshu.com vs rednote.com
  분기)
  - CDP_CONNECT_EXISTING = False (CDP 모드 동작 옵션)

  6. 로그인 + 크롤링 흐름 검증

  - LOGIN_TYPE 변경 흐름 정리 (cookie ↔ qrcode)
  - 폰 QR 스캔 → user_data_dir 영속 저장 흐름 확인
  - 첫 API(creator info)는 통과 → curl_cffi 효과 확인

  7. 진단 — 차단 원인 파악

  - user_posted API가 461 캡차로 막힘
  - 원인: xhs_config.py의 URL에 xsec_token / xsec_source 누락
  - 추가로 다중 시도로 계정/IP 단위 플래그 발생 → "무한 QR"
  처벌 모드 진입

  남은 작업 (다음 시도 전)

  1. 24~48시간 휴식 — 플래그 회복
  2. xsec_token 자동 추출 로직 — Playwright로 프로필 페이지
  방문해 페이지 상태에서 토큰 뽑아 API에 주입
  3. 행동 워밍업 — 로그인 직후 5~10초 스크롤/대기로 인간 패턴
  시뮬레이션
  4. 요청 간격 늘리기 — 2초 → 8~15초 + 랜덤화
  5. 테스트 규모 축소 — 처음엔 1명만 시도 → 점진 확대
  6. 백업 계정 준비 — 메인 계정 회복 안 될 경우 대비

  핵심 결정 사항

  - 도우인은 정상 동작 (Oxylabs 박혀있고 수집 잘 됨)
  - 샤오홍슈는 인프라(Oxylabs + curl_cffi + 로그인) 다
  적용됐지만 xsec_token 처리가 핵심 미해결 과제
  - 차단 회복 후 자동 토큰 추출 + 인간화 작업이 다음 우선순위