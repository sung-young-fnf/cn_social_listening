# 샤오홍슈(XHS) 크롤링 — 현재 운영 상태

작성일: 2026-05-14
작성: 다른 PC/협업자/Claude 세션이 이어받을 때 참조용

## 1. 최종 목표

- 샤오홍슈 인플루언서 ~210명의 게시물 메타 데이터 주간 수집
- 19컬럼 schema parquet → S3(`svc-fnf-cn-mkt-s3`) → Snowflake `STRG_SCL.RED_PROFILE_POST` 적재
- 매주 1회 사람이 QR 스캔 후 자동 수집

## 2. 운영 정책 (확정)

| 항목 | 값 |
|---|---|
| 출구 IP | **Oxylabs 한국 주거 IP (`cc-kr`)** — 회사 IP 절대 노출 X |
| 브라우저 | **시스템 Chrome** (`channel="chrome"`) — 번들 Chromium X |
| 프로필 진입 | **검색 박스 → keyboard.type → Enter → href 추출 → goto** |
| 노트 상세 진입 | **새 탭 (context.new_page) + rednote.com URL goto** |
| 인증 | **매번 QR** (`--reset-session`) — cookie 영속 안 함 |
| 봇 마커 회피 | `--disable-blink-features=AutomationControlled` |
| WebRTC IP 누수 차단 | `--force-webrtc-ip-handling-policy=disable_non_proxied_udp` |
| 자격증명 | fail-closed (.env의 OXYLABS_USERNAME/PASSWORD 필수) |
| 회사 IP 차단 | COMPANY_IP_PREFIX 매칭 시 즉시 종료 |
| 계정 간 지터 | 4-7초 랜덤 |
| 배치 휴식 | 10명/배치, 10분 휴식 (기본) |
| 노트 detail 간 지터 | 3-7초 랜덤 |

## 3. 동작 원리 — 전체 흐름

```
[사람] QR 1회 스캔 (스크립트 시작 시)

[스크립트] 매 인플루언서마다 자동:
  A. 검색으로 프로필 진입
     1. rednote.com/explore 진입 (검색 박스 노출)
     2. 검색박스 click → xhs가 검색 overlay 모달 띄움 → 모달 input에 focus
     3. page.keyboard.type(nickname) → 모달 input에 자동 입력
        (★ element.fill()은 stale 참조라 모달 input에 안 들어감 — keyboard 필수)
     4. Enter → 검색 결과 페이지
     5. 결과에서 user_id 매칭 link href 추출 → tab=note 제거 → page.goto
        (★ 클릭하면 새 탭 열려서 page 변수 못 따라감 → href + goto)
     6. 프로필 페이지 진입 (xsec_source=pc_search URL)

  B. 노트 목록 + 프로필 메타 수집
     7. listener로 user_posted API 응답 캡처 (note_id + xsec_token 등)
     8. __INITIAL_STATE__.user.userPageData에서 프로필 메타 추출
        (fans / follows / interaction / desc / avatar / red_id / ip_location)
     9. 지난주 필터 (note_id 앞 8자리 hex → unix timestamp)

  C. 노트 상세 추출 (필터 통과한 노트, --detail-count 최대 N개)
     10. 같은 context의 새 탭 열기 (context.new_page)
     11. 새 탭에서 rednote.com/explore/<note_id>?xsec_token=...&xsec_source=pc_user goto
         (★ xsec_token은 listener 응답의 note별 token, URL encoding 필수)
     12. noteDetailMap hydrate 폴링 (최대 15초)
     13. desc / comment_count / share_count / collected_count / ip_location / image_urls 추출
     14. 새 탭 close (메모리 정리)
     15. 3-7초 랜덤 sleep → 다음 노트

  D. 저장
     16. cover 이미지 다운로드 (Oxylabs 경유, requests)
     17. CSV + notes.json + creator.json 생성

  E. 다음 인플루언서
     18. 4-7초 랜덤 지터 → 다음 계정 (A부터 반복)
```

## 4. 핵심 트릭 정리

### 검색 박스 입력 — `page.keyboard.type()` 필수
xhs는 검색 박스 click 시 **overlay 모달**을 띄우고 모달 input에 focus를 옮김. 
`search_input.fill()`은 stale 참조 (헤더 input 가리킴)라 모달에 안 들어감.
`page.keyboard.type()`은 **focused element**에 입력하므로 모달 input에 정확히 들어감.

### 프로필 진입 — href 추출 + page.goto
검색 결과 user 카드 click 시 새 탭 열림 (target="_blank") → page 변수가 못 따라감.
**href 추출 → 같은 page에서 goto** 패턴으로 우회.
단 `&tab=note` 파라미터는 제거 (page.goto 시 "no posts" 빈 페이지 반환).

### 노트 detail 진입 — 새 탭 + rednote.com goto
xhs WAF가 `page.goto(xhs.com/explore/...)` 차단. 프로필에서 thumbnail click도 lazy-load로 실패 빈번.
**같은 context의 새 탭** 열어 **rednote.com/explore/<note_id>?xsec_token=...&xsec_source=pc_user** 직접 goto.
listener 응답의 **note별 xsec_token** 사용 — URL encoding 필수 (특수문자 안전).

### 로그인 판별 — `loggedIn._value` 최우선
`unread` cookie 잔재로 거짓 통과 사례 있음. `__INITIAL_STATE__.user.loggedIn._value`를 최우선 신호로 사용.
명시 False면 다른 cookie 신호 무시하고 비로그인 확정.

## 5. 핵심 파일

```
runners/grab_xhs.py                    ← 메인 (Phase 3 직전 상태 — click 기반 detail)
runners/grab_xhs_copy.py               ← 검증 완료 (새 탭 detail 패턴, prod 후보)
runners/grab_xhs_version2.py           ← 세션 수명 측정 데몬 (별건)
crawlers/mediacrawler-config/xhs_config.py  ← 210명 인플루언서 URL + 닉네임 (주석)
uploaders/s3_upload_xhs_post.py        ← S3 업로더 (notes.json + 이미지)
uploaders/s3_upload_xhs_account.py     ← S3 업로더 (creator.json + 아바타)
```

## 6. .env 필수 항목

```
# Oxylabs (fail-closed)
OXYLABS_USERNAME=customer-prcs_data1_LpjIC
OXYLABS_PASSWORD=...
OXYLABS_COUNTRY=kr

# 회사 IP 차단 (권장)
COMPANY_IP_PREFIX=...

# Chrome 경로 (자동 탐지 실패 시만)
# CHROME_PATH=C:\Program Files\Google\Chrome\Application\chrome.exe
```

## 7. 실행 방법

`grab_xhs.py` = **운영 전용** (xhs_config.py 전체 자동 크롤링, uid 인자 없음).
`grab_xhs_refactor.py` = **검증/디버그용** (특정 uid 박아서 1-N계정 테스트).

### 7.1 운영 — `grab_xhs.py` (xhs_config.py 전체 자동)

#### 전주 크롤링 (매주 월요일 운영)
xhs_config.py에 등록된 모든 creator를 지난주(월~일) 기준으로 크롤링. detail 10개 + 이미지/영상 다운로드.
```bash
python runners/grab_xhs.py --reset-session --detail-count 10
```

#### 특정 주차 백필
한 달 전 데이터부터 일주일씩 끊어서 백필. `--week` 지정 시 `--detail-count`는 자동으로 `10`.
```bash
python runners/grab_xhs.py --reset-session --week 0420
python runners/grab_xhs.py --reset-session --week 0427
python runners/grab_xhs.py --reset-session --week 0504
```

#### keep-open (운영 중 브라우저 살림 — 진단/검증용)
```bash
python runners/grab_xhs.py --reset-session --detail-count 10 --keep-open
```

### 7.2 검증/디버그 — `grab_xhs_refactor.py` (특정 uid 지정)

운영 전에 1-N계정 테스트로 흐름 검증. uid 직접 박음.
```bash
# 단일 계정 검증
python runners/grab_xhs_refactor.py 5aae4070e8ac2b068d00451d \
  --reset-session --detail-count 5

# 여러 계정 (콤마)
python runners/grab_xhs_refactor.py 5a16311de8ac2b349577ec8e,5aae4070e8ac2b068d00451d \
  --reset-session --detail-count 5

# F12 분석용 (브라우저 살림)
python runners/grab_xhs_refactor.py 5a16311de8ac2b349577ec8e \
  --reset-session --detail-count 5 --keep-open
```

### 7.3 키워드 검색 — `grab_xhs_keyword.py` (검증/시범)

특정 키워드 검색 결과 페이지에서 listener(`/search/notes`) 캡처. CREATOR 모드와 분리.
- 검색박스 keyboard.type → 결과 페이지 → UI 클릭 정렬(`最热` + `一周内`)
- 응답에 좋아요/댓글/이미지 다 들어있어서 detail은 옵션 (default `-1` = 전체)
- 출력: `output/red-keyword-YYMMDD/<keyword>/`

```bash
# 단일 키워드 검증
python runners/grab_xhs_keyword.py 鞋 --reset-session

# 여러 키워드 (콤마)
python runners/grab_xhs_keyword.py 鞋,包,运动鞋 --reset-session

# detail 안 들어가고 목록만 빠르게
python runners/grab_xhs_keyword.py 鞋 --reset-session --detail-count 0

# F12 분석용
python runners/grab_xhs_keyword.py 鞋 --reset-session --keep-open
```

★ 검증 후 운영 자동화는 `xhs_config.py`의 `XHS_KEYWORD_LIST`를 읽는 방향으로 별건 진행.

### 7.4 공통 옵션
```
--reset-session       — user_data_dir + cookie 삭제 (QR 다시)
--detail-count N      — 노트당 detail 진입 개수 (0=skip, -1=전체). 미지정 시 자동:
                        지난주 자동 모드면 0, 특정 날짜 범위 지정 시 10
--no-images           — 이미지/영상 다운로드 OFF
--week MMDD/YYMMDD    — 주차 명시 (기본: 지난주 자동)
--date-start/end      — 임의 날짜 범위 (yyyy-mm-dd)
--days N              — 최근 N일
--all                 — 날짜 필터 OFF
--batch-size N        — N명/배치 (기본 10)
--batch-rest SEC      — 배치 휴식 초 (기본 600=10분)
--gap-min/max SEC     — 계정 간 지터 (기본 4-7)
--keep-open           — 종료 시 브라우저 살림 (F12 디버그용)
--image-concurrency N — 이미지 동시 다운로드 수 (기본 5)
```

## 8. 출력 구조

```
output/red-weekly-YYMMDD/                ← S3 업로더 직접 입력
  ├── <user_id>/
  │   ├── notes.json                     ← 노트 메타데이터 (19+ 컬럼)
  │   ├── creator.json                   ← 프로필 정보 (fans/follows 등)
  │   └── <note_id>/
  │       └── 0.jpg                      ← cover 이미지
  └── <user_id>/
      └── ...

output/xhs_notes_<user_id>.csv         ← CSV (검증/디버그용)
output/xhs_session_state.json          ← sessid + last_ip
```

## 9. notes.json / creator.json 스키마

### notes.json (검증 통과 — 5/14 不潘 케이스)
```json
{
  "note_id": "69fb11e1000000003501e39b",
  "user_id": "5aae4070e8ac2b068d00451d",
  "nickname": "不潘",
  "title": "终于轮到我拍这个婚礼转场了",
  "desc": "",                         ← video 노트라 빈 값 (정상)
  "type": "video",
  "liked_count": "2.1万",
  "collected_count": "1742",          ← ✅ detail에서 받음
  "comment_count": "295",             ← ✅
  "share_count": "1391",              ← ✅
  "time": "2026-05-06",               ← ✅ 정확한 createTime
  "ip_location": "浙江",              ← ✅
  "image_list": "http://...",         ← cover URL
  "note_url": "..."
}
```

### creator.json
```json
{
  "user_id": "...",
  "nickname": "豆豆_Babe",
  "desc": "💕小号@豆豆本豆 ...",
  "avatar": "https://sns-avatar-qc.xhscdn.com/...",
  "gender": 1,
  "ip_location": "上海",
  "red_id": "422520418",
  "fans": "3921155",
  "follows": "202",
  "interaction": "20385057",
  "tag_list": [...]
}
```

## 10. 검증 상태 (5/14)

### ✅ 작동 검증
- QR 로그인 + 안정화 (`loggedIn._value` 우선)
- 검색 박스 진입 (`keyboard.type` 패턴)
- listener API 캡처 (note_id + xsec_token 포함)
- 프로필 메타데이터 추출 (fans/follows/interaction 등)
- 지난주 필터 (note_id hex decode)
- **노트 detail 추출 — 새 탭 + rednote.com goto** ★ Phase 4 완료
  - comment_count / share_count / collected_count / time / ip_location 진짜 값 받음
- cover 이미지 다운로드 (Oxylabs 경유)
- MediaCrawler 호환 포맷 출력

### ⏸️ 다음 단계
- 4계정 multi-influencer 안정성 검증
- 50계정 중간 테스트 (배치/휴식 안정성)
- 210계정 풀 운영
- `grab_xhs_copy.py` 변경을 `grab_xhs.py`에 반영 
   -> 반영 완료

## 11. S3 업로드

```bash
# dry-run 먼저
python uploaders/s3_upload_xhs_post.py ../output/red-weekly-YYMMDD --dry-run

# 실제 업로드
python uploaders/s3_upload_xhs_post.py ../output/red-weekly-YYMMDD
```

업로드 경로:
- parquet: `xiaohongshu/profile/post/p_year=YYYY/p_month=MM/p_day=DD/p_keyword=<uid>/<uid>.parquet`
- 이미지: `xiaohongshu/profile/image/<uid>/<note_id>/<note_id>_1.jpg`

## 12. 트러블슈팅 빠른 참조

| 증상 | 원인 | 대처 |
|---|---|---|
| `검색 결과에 user_id 없음` | 검색 박스 fill 안 통함 (overlay 모달) | `keyboard.type` 패턴 적용 (grab_xhs_copy.py) |
| `note link 못 찾음 (스크롤 밖)` | lazy-load로 thumbnail unrender | detail은 새 탭 패턴 사용 (click 회피) |
| `Failed to launch ... user data directory is already in use` | 이전 --keep-open Chrome 살아있음 | `Get-Process chrome \| Stop-Process -Force` |
| `[FAIL] OXYLABS_USERNAME 환경변수 필수` | .env 미설정 | .env에 자격증명 박기 |
| `검색 진입 OK 후 listener 0개` | URL `&tab=note` 박힌 채 진입 → "no posts" | tab=note 자동 제거 (이미 적용) |
| 자동 로그인 출력 후 익명 함정 | `unread` cookie 잔재로 거짓 True | `is_real_login` 엄격화 (loggedIn._value 우선) — 이미 적용 |
| 세션 죽음 감지 → 중단 | xhs WAF 차단 | `--reset-session` 후 30분+ 대기 |

## 13. 운영 시나리오 — 매주 월요일

```
[06:00] PC 켜고 터미널 열기
[06:01] python runners/grab_xhs_copy.py uid1,...,uid210 --reset-session --detail-count 10
[06:02] QR 모달 표시 → 폰으로 스캔 (30초)
[06:03 ~ 21:00] 자동 진행
  - 21배치 × 10분 휴식
  - 각 계정: 검색 진입 + 노트 캡처 + detail × 10 (3-7초 간격) + 이미지 다운로드
  - 약 15시간 (이미지 + detail 포함, 추정)
[21:00] python uploaders/s3_upload_xhs_post.py output/red-weekly-YYMMDD
```

**매주 사람 개입**: QR 스캔 30초 + S3 업로드 명령 1회.

## 14. 메모리 (Claude 세션 자동 참조)

`~/.claude/projects/.../memory/MEMORY.md`에 정책 정리. 주요:
- 한국 IP 정책 (cc-kr) — 회사 IP 절대 노출 X
- 매번 QR 진입 — cookie 영속 안 함
- CDP 모드 금지 (한국 IP 노출 위험)
- 3회 fail 시 즉시 중단
- 익명 함정 위험 (`unread` cookie 잔재)
- 코드 작업 전 사용자 승인 필수

## 15. 새 PC / 새 환경에서 시작

```
1. .env 파일에 OXYLABS_USERNAME / PASSWORD 박기
2. 시스템 Chrome 설치 확인
3. python runners/grab_xhs_copy.py <uid> --reset-session --keep-open
   → QR 모달 폰 스캔 → 데이터 받는지 확인
4. output/red-weekly-YYMMDD/<uid>/notes.json + creator.json + 이미지 확인
5. uploaders/s3_upload_xhs_post.py로 S3 dry-run 검증
```

## 16. 알아둘 점

- **한국 IP**에서 xhs.com 접근 시 **rednote.com**으로 자동 redirect (정상)
- cookie 도메인 `xiaohongshu.com` + `rednote.com` 양쪽 다 처리 (`is_xhs_cookie()` 헬퍼)
- `web_session` 38자가 정상 (rednote 사양). `unread` cookie + `loggedIn._value`로 보완 판단
- **video 노트는 `desc` 빈 값**이 정상 (제목만 있음, 본문 텍스트 없음)
- `--keep-open`은 검증/디버그용. 운영 자동화에선 박지 말 것
- `crawlers/MediaCrawler/`는 submodule — 손대지 말 것
- 호이(`kheom0512@fnfcorp.com`)도 같은 레포 commit
- **노트 detail의 xsec_token은 listener `user_posted` 응답에서 받은 note별 token**. 프로필 URL의 token과 다름
- URL에 xsec_token 넣을 때 `urllib.parse.quote(safe="")` 필수 (특수문자 안전)
