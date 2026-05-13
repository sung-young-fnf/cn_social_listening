# 샤오홍슈(XHS) 크롤링 — 현재 상황 정리

작성일: 2026-05-13 (최종 업데이트)
작성: 다른 PC/협업자/Claude 세션이 이어받을 때 참조용

## 1. 최종 목표

- 샤오홍슈 인플루언서 ~210명의 게시물 메타 데이터 (제목/좋아요/댓글수/즐겨찾기/공유 등) 주간 수집
- 19컬럼 schema CSV → S3(`svc-fnf-cn-mkt-s3`) → Snowflake `STRG_SCL.RED_PROFILE_POST` 적재
- 운영 자동화 (Airflow DAG 또는 cron)

## 2. 운영 정책 (확정)

| 항목 | 값 |
|---|---|
| 출구 IP | **Oxylabs 한국 주거 IP (`cc-kr`)** — 회사 IP 절대 노출 X |
| 브라우저 | **시스템 Chrome** (`channel="chrome"`) — Playwright 번들 Chromium X |
| 봇 마커 회피 | `--disable-blink-features=AutomationControlled` |
| WebRTC IP 누수 차단 | `--force-webrtc-ip-handling-policy=disable_non_proxied_udp` |
| Sticky session | OXYLABS_SESSID 영속 (한 번 만들면 같은 IP 풀 재사용) |
| 자격증명 | fail-closed (`.env`의 `OXYLABS_USERNAME`/`PASSWORD` 필수, 기본값 X) |
| 회사 IP 차단 | `COMPANY_IP_PREFIX` 매칭 시 즉시 종료 (옵션) |
| 요청 간격 | 5초 |

## 3. 시간순 진단/해결

### 5/8 — MediaCrawler 시도, 차단 누적
- MediaCrawler 흐름 + Oxylabs **중국** IP + 다양한 SESSID 시도
- 4회 연속 실패 패턴: ERR_TIMED_OUT → cookie 무효화 → 페이지 데이터 누락 → 로그인 강제 리다이렉트
- 결정적 단서: `query_self` 응답 `code -104 "您当前登录的账号没有权限访问"` = **계정 단위 차단**
- 근본 원인: **한국 사용자 계정 + 중국 IP 미스매치** → 도난 의심 보안 차단

### 5/11 — 한국 IP 정책 전환
- 사용자 폰 앱(한국)은 정상 동작 = 한국 IP에서 xhs 접근은 OK
- Oxylabs `cc-cn` → `cc-kr`로 전환
- 첫 시도에서 노트 데이터 받음 (목록)
- 그런데 **노트 ID가 빈 값** → CSV의 `unique_hash` 비어있음
- 진단: xhs가 페이지 진입 즉시 **anonymous web_session** 자동 발급, 우리 코드가 이걸 로그인으로 오인 ("익명 세션 함정")
- 해결: `is_real_login()` 강화 — placeholder의 `登录` negative check + `loggedIn._value` positive check

### 5/12 — red_crawler 패턴 채택
- 사용자 별도 폴더(`red_crawler/`)에서 성공 코드 발견:
  - `xhs-collect-all.js`, `xhs-console-v2.js`, `xhs-scrapling-v3.py`
  - 공통 패턴: **시스템 Chrome + 직접 IP + 3-way 추출(API+State+DOM)**
- `runners/grab_xhs.py` 통합 헬퍼 작성:
  - 시스템 Chrome 사용 (`channel="chrome"`)
  - Oxylabs KR + sessid 영속
  - cookie 영속 + `is_real_login` (URL `/login` + web_session 50자 + unread + placeholder)
  - 회사 IP 보호 (자격증명 fail-closed, IP 검증 fail-closed, WebRTC 차단)
  - `rednote.com` redirect 처리 (cookie 도메인 양쪽 포함)
- **검증 통과**: 32개 노트 추출 (`author: 白昼小熊`, `noteId: 6812293800...` 진짜 값)

### F12 Network 분석 결과 (사용자 확인)
- 진짜 API host: **`webapi.rednote.com`** (edith.xiaohongshu.com 아니었음)
- 페이지의 axios가 자동으로 박는 보안 헤더:
  - `x-s`, `x-s-common`, `x-rap-param`, `x-b3-traceid`
- 우리가 fetch 직접 호출 시 이 헤더 못 박아서 차단됨
- 정답: **페이지가 자체 호출하는 응답을 listener로 받기**

### 5/13 — cookie 재사용 fail 진단 + 운영 패턴 확정
사용자 질문: "cookie 저장돼있다는데 왜 매번 QR 모달이 또 떠?"
- **진단 추가**: `grab_xhs.py`에 `[diag]` 블록(IP 비교, cookie 4지표, `loggedIn._value`) 추가하여 1차/2차 직접 비교
- **결과 (1차 QR + 2차 즉시 재실행)**:
  - 같은 IP 유지 (`211.254.135.210`) — Sticky 30분 안에 들어옴
  - cookie 31개 모두 저장본 + 추가 발급분 살아있음 (`unread=O`, `id_token=O`, `web_session=38자`)
  - 그런데 **`__INITIAL_STATE__.user.loggedIn._value: False`**
  - State 첫 노트 `nc_noteId: ''` (빈 ID) — **익명 렌더**
  - 시각적으로 QR 모달이 자동으로 떴음 — xhs 자체가 비로그인 인식
- **결론**: cookie/IP/Sticky는 문제 아님. xhs가 **cookie 재로드 자체를 새 익명 세션으로 처리**.
  - `is_real_login()`이 OR 조건의 `unread` cookie 잔재만 보고 True 거짓 통과 (코드 버그)
  - 실제론 비로그인 → 페이지가 익명 렌더 → noteId 가림 (메모리 `project_xhs_anonymous_session` 정확히 재현)

### 5/13 — 운영 가능성 재평가 + version2 측정 데몬 도입
- **100% 무인 자동화 불가능 확정** — xhs는 매 세션마다 신선한 인증 컨텍스트 요구
- 사용자 결정: **"한 번 QR 찍은 Chrome 세션을 오래 살려두고, 그 안에서 주기 크롤링"** 방식
- 신규 파일 `runners/grab_xhs_version2.py` 작성:
  - 크롤링 X, 로그인 신호만 N분 간격으로 기록
  - 매 시작마다 QR 강제 (cookie 자동 로드 안 함 — 함정 회피)
  - `output/xhs_session_log.csv`에 모든 신호 append
  - 만료 의심 신호 2회 연속 시 자동 종료 + 알림
- 목적: **QR 1회 후 xhs 세션이 정확히 몇 시간 살아있는지 측정** → Case A/B 정책 결정 근거

### 5/13 — grab_xhs.py 운영 기능 강화 ★
운영 자동화는 못 해도 **사람 개입 최소화** 위해 다음 4가지 추가:
1. **MediaCrawler 호환 출력 포맷** — `output/red-weekly-YYMMDD/<user_id>/notes.json + creator.json` 자동 생성. `uploaders/s3_upload_xhs_post.py`가 그대로 읽음. (CSV는 검증용 그대로 유지)
2. **날짜 필터** — `--week`, `--date-start/--date-end`, `--days N`, `--all`. 인자 0개면 **지난주(월~일) 자동**.
3. **조기 종료** — listener가 연속 10개 older 노트 감지 시 스크롤 lazy-load 중단 (도우인 v5와 동일 패턴, 효율 ↑)
4. **배치 + 휴식 + 지터** — 210계정 한 번에 burst하지 않고 분산 처리:
   - `--batch-size 10 --batch-rest 1800` (10명 처리 후 30분 휴식)
   - `--gap-min 4 --gap-max 7` (계정 간 랜덤 지터, 봇 패턴 회피)

### 5/13 — IP 정책 정리
- **Oxylabs Sticky 30분이 본질적 한계** — 정책 변경 불가 (Residential 상품 특성, 가정집 인터넷 임대 구조)
- **ISP Proxy 업그레이드 시** ($3-7/IP/월) 영구 고정 IP 가능 → cookie 영속 의미 살아남
- 현재 결정: **측정 데몬 결과 보고 결정** — in-session 24h+ 살아남으면 ISP 불필요

## 4. listener 등록 시점 — 핵심 해결 ★

### 이전 회귀 원인 (해결됨)

```
이전: main이 page.goto(profile_url) → 6초 대기 → collect_notes 진입
                                                  ↑
                                          여기서 listener 등록 (이미 늦음)

페이지의 user_posted 첫 호출은 page.goto 직후 일어남
→ 그 시점에 listener 없음 → 캡처 0건
```

### 정답 패턴

```
collect_notes(page, user_id):
    page.on("response", on_response)   ← 먼저 등록
    await page.goto(profile_url)        ← 그 다음 진입
    await sleep(8)                      ← 첫 자체 fetch 대기
    스크롤 lazy-load                    ← 추가 호출 트리거
    page.remove_listener(...)
    리턴
```

**listener는 반드시 `page.goto` 전에 등록**. 이게 보안 헤더(x-s, x-rap-param) 직접 만들지 않고 정상 응답 받는 유일한 안정 패턴.

### 보조 — State + DOM (이중 안전망)
listener가 못 받아도 페이지 SSR의 `__INITIAL_STATE__.user.notes` + DOM `a[href]`에서 fallback 추출. 3-way 합쳐서 중복 제거.

## 5. 핵심 파일

```
runners/grab_xhs.py                    ← 통합 헬퍼 (메인 — 운영용)
runners/grab_xhs_version2.py           ← 세션 수명 측정 데몬 (크롤링 X)
runners/grab_xsec_token.py             ← (구) 토큰 추출 (참조용)
runners/grab_notes_from_page.py        ← (구) B 방식 (참조용)
runners/grab_creator_via_search.py     ← (구) 검색 흐름 (참조용)
runners/grab_note_detail.py            ← (구) 노트 상세 진단 (참조용)
red_crawler/xhs-collect-all.js         ← 작동 검증된 Node.js 원본 (참조용)
red_crawler/xhs-console-v2.js          ← Chrome F12 직접 실행용 (참조용)
red_crawler/xhs-scrapling-v3.py        ← Python scrapling 버전 (참조용)
crawlers/mediacrawler-config/          ← MediaCrawler 흐름 (5/8 시점, 보관)
uploaders/s3_upload_xhs_post.py        ← S3 업로더 (grab_xhs.py 출력 그대로 입력)
```

## 6. .env 필수 항목

```
# Oxylabs (fail-closed — 기본값 X)
OXYLABS_USERNAME=customer-prcs_data1_LpjIC
OXYLABS_PASSWORD=...
OXYLABS_COUNTRY=kr

# 회사 IP 차단 (선택)
COMPANY_IP_PREFIX=...

# Chrome 경로 (자동 탐지 실패 시)
# CHROME_PATH=C:\Program Files\Google\Chrome\Application\chrome.exe
```

## 7. 실행 방법

### 기본 (지난주 자동 + 배치 + 지터)
```bash
# 인자 0개 = 지난주(월~일) 자동 필터, 10명/배치, 30분 휴식, 4-7초 지터
python runners/grab_xhs.py 5a8cf39111be10466d285d6b

# 여러 명 (콤마)
python runners/grab_xhs.py uid1,uid2,uid3

# detail 같이 받기 (comments/stars/shares/content)
python runners/grab_xhs.py uid1,uid2 --detail-count 5
```

### 날짜 옵션 (우선순위: --all > --date-start/end > --week > --days > 기본=지난주)
```bash
# 주차 명시 (MMDD 4자리 또는 YYMMDD 6자리)
python runners/grab_xhs.py uid --week 0504        # 올해 5/4 주
python runners/grab_xhs.py uid --week 260504      # 2026/5/4 주

# 날짜 직접 지정
python runners/grab_xhs.py uid --date-start 2026-05-04 --date-end 2026-05-10

# 최근 N일
python runners/grab_xhs.py uid --days 7

# 필터 OFF — 모든 노트 (기존 동작)
python runners/grab_xhs.py uid --all
```

### 배치 + 지터 커스텀
```bash
# 봇 감지 회피 강화 — 작은 배치 + 긴 휴식
python runners/grab_xhs.py uid1,...,uid210 \
  --batch-size 10 --batch-rest 1800 \
  --gap-min 4 --gap-max 7

# 빠른 디버그 — 휴식 없이
python runners/grab_xhs.py uid1,uid2 --batch-size 2 --batch-rest 0 --gap-min 1 --gap-max 2
```

### 세션 / 디버그 옵션
```bash
# cookie + user_data_dir 리셋 (QR 다시 1회 — cookie 만료 / redirect 감지 시)
python runners/grab_xhs.py uid --reset-session

# 디버그용 — 종료 시 브라우저 안 닫음 (F12 Network 분석)
python runners/grab_xhs.py uid --keep-open

# sessid도 같이 리셋 (IP 풀 자체 새로)
del output\xhs_session_state.json   # Windows
python runners/grab_xhs.py uid --reset-session
```

### 시나리오별 명령

| 시나리오 | 명령 |
|---|---|
| 첫 셋업 (cookie/sessid 둘 다 없음) | `python runners/grab_xhs.py <id>` → QR 1회 |
| 매주 운영 (지난주 데이터) | `python runners/grab_xhs.py uid1,...,uid210` |
| cookie 만료 또는 redirect 감지 | `python runners/grab_xhs.py <id> --reset-session` → QR 1회 |
| IP 풀 burnt (timeout 반복) | `del output\xhs_session_state.json` + `--reset-session` |
| 새 인플루언서 디버그 | `python runners/grab_xhs.py <id> --keep-open` |
| 세션 수명 측정 (별도 데몬) | `python runners/grab_xhs_version2.py --reset-session` |

## 8. 출력

### 크롤링 결과
```
output/red-weekly-YYMMDD/                ← S3 업로더 직접 입력
  ├─ <user_id_1>/
  │   ├─ notes.json                      ← MediaCrawler 호환 포맷
  │   └─ creator.json                    ← {user_id, nickname}
  └─ <user_id_2>/
      └─ ...
output/xhs_notes_<user_id>.csv         ← CSV (검증/디버그 — 19+3컬럼)
```

### 세션 / 상태
```
output/xhs_logged_in_cookies.json      ← cookie 영속 (다른 PC 이식용)
output/xhs_session_state.json          ← sessid + last_ip (1차/2차 IP 비교용)
output/xhs_session_log.csv             ← version2 측정 데몬 출력 (14컬럼)
```

### 측정 데몬 (version2) 컬럼
```
timestamp, elapsed_min, checkpoint, ip, current_url, login_redirect,
cookie_count, web_session_len, has_id_token, has_unread, domains,
logged_in_value, is_real_login, note
```

### S3 업로드
```bash
# notes.json + creator.json만 있어도 업로드 OK (이미지 없으면 parquet만 올라감)
python uploaders/s3_upload_xhs_post.py output/red-weekly-260504 --dry-run
python uploaders/s3_upload_xhs_post.py output/red-weekly-260504
```

## 9. 검증된 동작

- ✅ 자동 로그인 (cookie 살아있을 때 — 단, **2차 재실행 시 익명 함정 빈번** — 5/13 진단)
- ✅ cookie 만료 시 QR 모달 자동 표시 (폰 스캔 1회)
- ✅ State + DOM 추출 (32개 노트, 진짜 noteId)
- ✅ 회사 IP 보호 (Oxylabs KR + WebRTC 차단)
- ✅ listener 패턴 — page.goto 전 등록 (175개 노트 캡처 검증, 1차 QR 직후 기준)
- ✅ 다중 user_id 순회 (5초 간격 → 5/13에 4-7초 랜덤 지터로 변경)
- ✅ 노트 상세 코드 (`--detail-count` 옵션) — desc/post_date/location/image_urls/video_url 채움
- ✅ MediaCrawler 호환 포맷 출력 (`notes.json` + `creator.json`)
- ✅ 날짜 필터 + 조기 종료 (지난주 자동, listener에서 연속 older 10개 시 스크롤 중단)
- ✅ 배치 + 휴식 + 지터 (210계정 분산 처리)
- ✅ `[diag]` 진단 로그 (1차/2차 cookie/IP/state 비교)
- ⚠️ `is_real_login` 거짓 통과 — `unread` cookie 잔재만으로 True 반환하는 버그 확인됨 (loggedIn._value False 무시)
- ⏸️ 댓글/즐겨찾기/공유 — 코드 OK, 실제 detail 진입 검증 미완
- ⏸️ S3 업로드 — uploader 호환 포맷 OK, 실제 적재 테스트 미실시
- ⏸️ 이미지 파일 다운로드 — `<note_id>/N.jpg` 미구현, parquet만 올라감 (수동 추가 필요 시)
- ⏸️ 세션 수명 측정 — `grab_xhs_version2.py` 데몬 돌리는 중, 결과 대기

## 10. 다음 작업 — 우선순위

1. **세션 수명 측정 결과 분석** — version2 CSV 보고 idle 수명 X시간 확정
   - 24h+ 살아남음 → ISP proxy 불필요, 매주 QR + 즉시 크롤링
   - 30분만에 죽음 → ISP proxy ($3-7/월) 또는 매주 QR 받아들임
2. **`is_real_login` 강화** — `loggedIn._value: false`를 강한 negative 신호로 처리 (AND 조건 또는 우선순위)
3. **댓글/별/공유 검증** — `--detail-count 5`로 한 계정 돌려 CSV에 진짜 값 채워지는지
4. **210계정 실제 운영 테스트** — 측정 결과 좋으면 1명 → 10명 → 50명 → 210명 점진 확대
5. **S3 업로더 end-to-end 테스트** — `s3_upload_xhs_post.py output/red-weekly-YYMMDD --dry-run` → 실제
6. **이미지 파일 다운로드** — 운영 시 필요하면 `<note_id>/N.jpg` 저장 로직 추가
7. **운영 자동화 형태 결정** — cron/Airflow + Slack 알림 (cookie 만료 / 세션 fail 시)

## 11. 세션 / IP / Cookie 주기 관리 ★

### 세 가지 영속 파일 — 수명 차이

| 파일 | 영속 단위 | 만료 신호 | 대처 |
|---|---|---|---|
| `output/xhs_session_state.json` (sessid) | Oxylabs 풀 IP 식별자 | 그 IP가 burnt (timeout / 403 반복) | 파일 삭제 → 자동 새 sessid 생성 |
| `output/xhs_logged_in_cookies.json` (cookie) | xhs 인증 토큰 | `is_real_login` False / `/login` redirect | `--reset-session`으로 QR 다시 |
| `crawlers/.../xhs_user_data_dir/` (브라우저 상태) | localStorage 등 클라이언트 상태 | 거의 안 깨짐 | `--reset-session` 시 같이 삭제 |

### Oxylabs Sticky Session 30분 한계

- `OXYLABS_SESSTIME=30` (최대값 — residential proxy 정책)
- 같은 sessid를 30분 안에 쓰면 → **같은 IP**
- 30분 지나면 풀에서 회전 → **다른 IP** 받을 수 있음
- 즉 **시간 갭 큰 재실행 = IP 바뀜 가능성**

### Cookie / IP 매칭 의심

```
cookie 발급 시점 IP (220.88)  ≠  사용 시점 IP (61.98)
    ↓
xhs WAF: "다른 IP에서 같은 cookie? 의심"
    ↓
페이지 진입 시 /login으로 자동 redirect
    ↓
우리 evaluate 호출 중 Execution context destroyed
```

이게 일어나면 콘솔에 `로그인 페이지로 redirect됨 — cookie/IP 미스매치. --reset-session 필요.` 출력.

### 운영 시나리오 권장

**Case A. 매주 자동 (간단)** — 사람 손 매주 1회
```
매주 월요일 06:00 cron/Airflow
  → python runners/grab_xhs.py <ids> --reset-session
  → 새 sessid + 새 cookie + 새 QR (사람이 폰 스캔 1회)
  → 그 한 실행 안에서 모든 user_id 처리 (sticky 30분 안에)
```

**Case B. cookie 영속 유지** — 사람 손 수 주~한 달 1회
```
매주 월요일 06:00 cron/Airflow
  → python runners/grab_xhs.py <ids>  (reset 없이)
  → cookie 살아있으면 자동 통과
  → 만료 시점에 Slack 알림 → 사람이 1회 QR
```

| 비교 | Case A | Case B |
|---|---|---|
| 사람 개입 빈도 | 매주 1회 | 수 주~한 달 1회 |
| 안정성 | 매번 깨끗한 cookie/IP | cookie 길게 유지 (가끔 의심) |
| 실패 위험 | 매번 새 QR이라 누적 의심 ↓ | cookie 만료 시 자동 fail |

### 한 실행 30분 안에 끝내기

`OXYLABS_SESSTIME=30` 한계로, **모든 작업은 한 실행에서 30분 안에 처리**하는 게 안전.
- 210명 × 노트당 3~5초 = 약 10~17분. 30분 안 들어감.
- 노트 상세 진입까지 한다면 더 길어질 수 있음 → 분할 실행 고려

### 새 PC / 새 환경에서 시작

```
1. .env 파일에 OXYLABS_USERNAME / PASSWORD 박기
2. 시스템 Chrome 설치 확인
3. python runners/grab_xhs.py <id>  (첫 실행 — QR 1회 필수)
4. cookie 저장 확인 (output/xhs_logged_in_cookies.json 생성됨)
5. 이후 실행은 cookie 살아있는 동안 자동
```

## 12. 알아둘 점

- **xhs가 rednote.com으로 자동 redirect** 됨 (한국 IP 사용 시) — 정상
- cookie 도메인 `xiaohongshu.com` + `rednote.com` 양쪽 다 처리 (`is_xhs_cookie()` 헬퍼)
- `web_session` 길이 38자가 정상일 수 있음 — 우리 임계 50자 미만이라 `unread` cookie + placeholder check로 보완
- **`unread` cookie**는 원래 진짜 로그인 시에만 발급. 다만 **5/13 진단에서 잔재 cookie로 거짓 통과 사례 확인** — OR 조건만 보지 말고 `loggedIn._value`와 같이 확인 권장
- 보안 헤더(x-s, x-rap-param) 직접 만들 생각 X — 페이지의 axios 인터셉터에 맡길 것
- `--keep-open`은 검증/디버그용. 운영 자동화에선 **절대 박지 말 것** (사람 입력 기다리느라 멈춤)
- **Execution context destroyed** 에러 = navigation 발생 (보통 cookie/IP 미스매치로 /login redirect). `--reset-session` 권장
- `crawlers/MediaCrawler/`는 submodule. 손대지 말 것 (이전 5/8 시점 코드 보관)
- 호이(다른 개발자)도 같은 레포 commit. 5/8 commit들은 호이 작업

## 13. 메모리 (Claude 세션 자동 참조)

`~/.claude/projects/.../memory/MEMORY.md`에 정책/진단 결과 정리됨. 다음 세션도 자동 참조.

주요 메모리:
- 한국 계정 + 중국 IP 미스매치 = 본질 차단 원인
- 안정 운영 = 고정 IP + 영속 환경 + 저장 cookie 재사용
- 익명 web_session 자동 발급 함정 + `loggedIn._value` 체크
- CDP 모드 금지 (한국 IP 노출)
- sessid 매번 진짜 새 값 (재사용 금지 시 — 사용자 직접 sessid 박을 때만 적용)
- XHS 시도 누적 금지 (한 세션 3회 fail 시 중단)

## 14. 자동 로그인 한계 — 솔직한 정리

### 100% 자동 로그인은 보장 X
xhs/rednote는 다음을 종합 판정:
- IP (Oxylabs sticky 30분, 회전 시 변경)
- cookie 도메인 (xiaohongshu.com + rednote.com 둘 다)
- localStorage / IndexedDB (user_data_dir에 영속)
- web_session / id_token 만료 시점
- 리스크 상태 (잦은 로그인 시도, fingerprint 변화)

### 자동 로그인 실패 흔한 원인 6가지
1. cookie 저장 시점이 너무 빨라서 일부 cookie(id_token, unread) 누락 → **5초 → 12초 대기로 수정 완료**
2. rednote/xiaohongshu cookie 도메인 일부 누락 → `is_xhs_cookie()` 헬퍼로 양쪽 다 저장 완료
3. user_data_dir의 localStorage 상태와 cookie 불일치
4. Oxylabs sticky IP 만료(30분)로 cookie 발급 IP ≠ 현재 IP
5. web_session/id_token 자체 만료 (수 주 단위)
6. is_real_login 판정 조건이 실제 상태 못 잡음

### 로그인 판단 시 봐야 할 신호 (강도 순)
| 신호 | 강도 | 우리 코드 상태 |
|---|---|---|
| URL `/login` redirect | ★★★ 비로그인 확정 | ✅ |
| `__INITIAL_STATE__.user.loggedIn._value: false` | ★★★ 비로그인 확정 (negative) | ⚠️ 현재 OR 조건이라 무시될 수 있음 |
| `__INITIAL_STATE__.user.loggedIn._value: true` | ★★★ 로그인 확정 (positive) | ✅ |
| `unread` cookie 존재 | ★ 약함 — 잔재로 거짓 통과 사례 있음 (5/13) | ✅ |
| 좌측 사이드바 "我" element 노출 | ★★ 강함 | ⏸️ 추가 가능 |
| `id_token` cookie 존재 | ★★ 강함 | ⏸️ 추가 가능 |
| `web_session` 길이 ≥ 50자 | ★ 약함 (rednote는 38자도 정상) | ✅ |
| placeholder `登录` 키워드 | ★★ 약 negative | ✅ |

**개선 방향** (TODO):
- `loggedIn._value: false`를 **AND 조건의 강제 negative**로 처리 — 다른 신호 무시하고 비로그인 확정
- 현재는 OR 조합이라 `unread` 잔재 cookie가 있으면 거짓 True 반환 (5/13 확인)

## 15. 트러블슈팅 빠른 참조

| 증상 | 원인 | 대처 |
|---|---|---|
| `Execution context destroyed` | 페이지가 evaluate 중 navigate (cookie/IP 미스매치로 /login redirect) | `--reset-session` |
| `cookie 저장본 로드` 후 `로그인 페이지로 redirect됨` | cookie 발급 IP ≠ 현재 IP | `--reset-session` |
| `ERR_TIMED_OUT` 반복 | Oxylabs IP가 xhs에 차단 (burnt) | `rm output/xhs_session_state.json` + `--reset-session` |
| `note_id 전부 빈 값` | 익명 세션 + 페이지가 ID 가림 | `is_real_login` 더 엄격 (loginUser.userId 강제) |
| `noteId 데이터 감지` 안 됨 (20초 polling 끝) | 페이지 hydrate 실패 또는 anonymous | `--reset-session` + 페이지 reload |
| `[FAIL] 시스템 Chrome 못 찾음` | Chrome 설치 안 됨 또는 경로 다름 | Chrome 설치 또는 `CHROME_PATH` 환경변수 박기 |
| `[FAIL] OXYLABS_USERNAME 환경변수 필수` | .env 미설정 | `.env`에 자격증명 박기 |
| `[FAIL] 출구 IP가 회사 IP 패턴 매칭` | 프록시 우회됨 (Playwright 옵션 누락 등) | proxy 옵션 + WebRTC 차단 확인 |
| `자동 로그인 OK 떴는데 노트 ID 빈 값` | `is_real_login`이 `unread` 잔재로 거짓 통과 → 실제는 익명 (5/13 확인) | `--reset-session` 또는 `is_real_login` 강화 (`loggedIn._value: false`면 무조건 비로그인) |
| `[diag] 이전 실행 IP: ... → ✗ 다름` 후 fail | Oxylabs Sticky 30분 지나 IP 회전 | 그 자체는 정보용 경고. cookie 재사용 안 하면 무관. cookie 재사용 fail이면 `--reset-session` |

## 16. grab_xhs.py 운영 옵션 — 봇 감지 회피 ★

### 날짜 필터 (인자 0개 = 지난주 자동)
```
우선순위: --all > --date-start/--date-end > --week > --days > 기본(지난주 월~일)
```
- 지난주 = 가장 최근에 끝난 주(월~일). 어느 요일에 실행해도 같은 주차.
- 필터 통과한 노트만 `notes.json` + CSV에 저장
- listener에서 연속 10개 older 노트 감지 시 **스크롤 lazy-load 조기 종료** (효율)

### 배치 + 휴식 + 지터
```bash
--batch-size 10      # N명/배치 (기본 10)
--batch-rest 1800    # 배치 사이 휴식 (초, 기본 1800=30분, 0이면 휴식 X)
--gap-min 4          # 계정 간 최소 지터 (초)
--gap-max 7          # 계정 간 최대 지터 (초)
```
- 봇 감지 회피의 핵심: **burst 대신 spread**
- 210계정을 한 번에 다 보내지 않고 분산 처리 → xhs가 보기에 "사람 활동 패턴"
- 마지막 배치는 휴식 안 함 (자동 종료)

### 운영 예시 — 매주 월 06:00
```bash
# 사람이 PC 켜고 QR 1회 스캔 후:
python runners/grab_xhs.py uid1,uid2,...,uid210
# → 지난주 데이터만 자동 필터
# → 21배치 × 30분 휴식 = 약 10.5h + 실작업 1-2h = 약 12시간
# → 그날 저녁 완료
# → output/red-weekly-YYMMDD/ 안에 정리

# 같은날 또는 다음날:
python uploaders/s3_upload_xhs_post.py output/red-weekly-YYMMDD
```

### 측정 결과에 따른 옵션 조정
| 측정 idle 수명 | 권장 옵션 |
|---|---|
| 24h+ | `--batch-size 20 --batch-rest 600` (20명 × 10분 = ~3h) |
| 12-24h | `--batch-size 10 --batch-rest 1800` (기본값, ~12h) |
| 5-12h | `--batch-size 5 --batch-rest 900` (5명 × 15분 = ~10h) |
| 2-5h | `--batch-rest 0 --gap-min 60 --gap-max 90` (한 번에 처리, 텀만 길게) |
| 2h 미만 | 분할 운영 (이번주 105 + 다음주 105) |

## 17. grab_xhs_version2.py — 세션 수명 측정 데몬

### 목적
QR 로그인 후 xhs 세션이 정확히 몇 시간 살아있는지 정량 측정.
브라우저 살려놓고 5분 간격(기본)으로 로그인 신호 CSV 기록.

### 특징
- 크롤링 X — 로그인 신호만 측정
- 매 시작마다 QR 강제 (cookie 자동 로드 안 함 — 익명 함정 회피)
- 만료 의심 신호 2회 연속 시 자동 종료
- `output/xhs_session_log.csv`에 14컬럼 append

### 실행
```bash
# 첫 측정 (깨끗한 시작 — 권장)
python runners/grab_xhs_version2.py --reset-session

# 옵션
--interval 5     # heartbeat 간격(분, 기본 5)
--max-hours 720  # 최대 측정 시간(시간, 기본 30일, 0=무한)
--ip-every 1     # N 사이클마다 IP 측정 (기본 1)
```

### 운영 시 주의
- xhs 탭/창 닫으면 데몬 즉시 죽음
- 로그아웃 버튼 누르면 인위적 만료 → 데이터 무가치
- PC 절전 모드 막아야 함 (`powercfg /change standby-timeout-ac 0`)
- 다른 작업은 새 탭/창에서 자유롭게 (영향 없음)

### 결과 → 정책 결정
- 24h+ 살아남음 → ISP proxy 불필요, 매주 QR + 즉시 크롤링
- 30분만에 죽음 → ISP proxy 필요 또는 매주 QR 받아들임
- crawling 시 빨리 죽음 → 행동 패턴 차단 → 분할 크롤링 + 더 긴 휴식
