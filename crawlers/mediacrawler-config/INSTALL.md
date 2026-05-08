# MediaCrawler 패치 설치 가이드

이 폴더는 `cn-social-listening` 레포가 관리하는 **MediaCrawler 커스텀 설정 + 패치 백업**입니다.
MediaCrawler 본체는 외부 OSS(NanmiCoder/MediaCrawler)이고, 클론 후 우리 패치를 덮어써야 동작.

## 폴더 구조

```
crawlers/mediacrawler-config/
├── INSTALL.md                           ← 이 문서
├── base_config.py                       ← MediaCrawler/config/base_config.py 대체
├── xhs_config.py                        ← MediaCrawler/config/xhs_config.py 대체
├── oxylabs_proxy.py                     ← MediaCrawler/proxy/providers/oxylabs_proxy.py (신규)
├── proxy_types.py                       ← MediaCrawler/proxy/types.py 대체
├── proxy_proxy_ip_pool.py               ← MediaCrawler/proxy/proxy_ip_pool.py 대체
├── tools_httpx_util.py                  ← MediaCrawler/tools/httpx_util.py 대체
├── media_platform_xhs_login.py          ← MediaCrawler/media_platform/xhs/login.py 대체
├── media_platform_xhs_client.py         ← MediaCrawler/media_platform/xhs/client.py 대체
└── media_platform_xhs_core.py           ← MediaCrawler/media_platform/xhs/core.py 대체
```

---

## 새 PC에서 처음부터 셋업하는 법

### Step 1: MediaCrawler 클론

```bash
cd cn-social-listening/crawlers
git clone https://github.com/NanmiCoder/MediaCrawler.git
cd MediaCrawler
```

### Step 2: venv + 의존성 설치

```bash
# venv 생성
python -m venv venv

# 활성화
# Windows PowerShell
.\venv\Scripts\Activate.ps1
# Git Bash / Linux
source venv/Scripts/activate

# MediaCrawler 본체 의존성 (playwright는 1.45.0으로 핀됨, 강제로 1.45.0 됨)
pip install -r requirements.txt

# 우리 추가 의존성 (TLS 위조)
pip install curl_cffi

# Playwright 브라우저 (CDP 모드 끔으로 인해 번들 Chromium 필요)
# requirements.txt가 playwright를 1.45.0으로 다운그레이드하므로 chromium-1124 새로 받아야 함
playwright install chromium
```

### Step 3: 우리 패치 적용 (9파일)

`cn-social-listening` 레포 루트 기준으로 실행:

#### Windows PowerShell
```powershell
$src = "crawlers\mediacrawler-config"
$dst = "crawlers\MediaCrawler"

Copy-Item "$src\base_config.py"                  "$dst\config\base_config.py" -Force
Copy-Item "$src\xhs_config.py"                   "$dst\config\xhs_config.py" -Force
Copy-Item "$src\oxylabs_proxy.py"                "$dst\proxy\providers\oxylabs_proxy.py" -Force
Copy-Item "$src\proxy_types.py"                  "$dst\proxy\types.py" -Force
Copy-Item "$src\proxy_proxy_ip_pool.py"          "$dst\proxy\proxy_ip_pool.py" -Force
Copy-Item "$src\tools_httpx_util.py"             "$dst\tools\httpx_util.py" -Force
Copy-Item "$src\media_platform_xhs_login.py"     "$dst\media_platform\xhs\login.py" -Force
Copy-Item "$src\media_platform_xhs_client.py"    "$dst\media_platform\xhs\client.py" -Force
Copy-Item "$src\media_platform_xhs_core.py"      "$dst\media_platform\xhs\core.py" -Force
```

#### Git Bash / Linux / macOS
```bash
SRC="crawlers/mediacrawler-config"
DST="crawlers/MediaCrawler"

cp "$SRC/base_config.py"                 "$DST/config/base_config.py"
cp "$SRC/xhs_config.py"                  "$DST/config/xhs_config.py"
cp "$SRC/oxylabs_proxy.py"               "$DST/proxy/providers/oxylabs_proxy.py"
cp "$SRC/proxy_types.py"                 "$DST/proxy/types.py"
cp "$SRC/proxy_proxy_ip_pool.py"         "$DST/proxy/proxy_ip_pool.py"
cp "$SRC/tools_httpx_util.py"            "$DST/tools/httpx_util.py"
cp "$SRC/media_platform_xhs_login.py"    "$DST/media_platform/xhs/login.py"
cp "$SRC/media_platform_xhs_client.py"   "$DST/media_platform/xhs/client.py"
cp "$SRC/media_platform_xhs_core.py"     "$DST/media_platform/xhs/core.py"
```

### Step 4: 추가 패키지 설치 (하이브리드 흐름용)

```bash
pip install xhs
```

---

## 파일별 변경 내역

### 1) `base_config.py`
- `LOGIN_TYPE = "qrcode"` (기본 — fresh QR로 cookie/IP 매칭 보장. cookie 재사용 시 `"cookie"`로 변경)
- `COOKIES = "..."` (직접 박는 cookie. LOGIN_TYPE=cookie일 때만 사용)
- `ENABLE_IP_PROXY = True`
- `IP_PROXY_PROVIDER_NAME = "oxylabs"`
- `ENABLE_CDP_MODE = False` (⚠️ 중요 — CDP 모드는 proxy 미적용 → 진짜 IP 노출)
- `CDP_CONNECT_EXISTING = False` (신규 추가, 누락 시 startup crash)
- `CRAWLER_MAX_SLEEP_SEC = 15` (인간 패턴)
- `CRAWLER_MAX_NOTES_COUNT = 5` (테스트 기본. 운영시 100으로)
- `CRAWLER_DATE_START / END = ""` (날짜 필터, 실제 코드 미사용 — runner가 박을 때만 의미)
- `XHS_INTERNATIONAL` 처리 (xhs_config.py로 이전)

### 2) `xhs_config.py`
- `XHS_INTERNATIONAL = False` (신규 추가, xiaohongshu.com 사용)
- `XHS_CREATOR_ID_LIST` (210명 인플루언서 URL)

### 3) `oxylabs_proxy.py` (신규)
- Oxylabs Backconnect provider 구현 — 도우인 자격증명 재사용
- **sticky session 강제** — sessid 자동 생성 + sesstime 30분
  - sessid 미설정 시 매 connection마다 IP 로테이션 → cookie 발급 IP ≠ 사용 IP → WAF 461
  - 이걸 막기 위해 `secrets.token_hex(8)` 자동 생성 또는 `OXYLABS_SESSID` env로 고정
- 환경변수: `OXYLABS_HOST/PORT/USERNAME/PASSWORD/SESSID/SESSTIME`

### 4) `proxy_types.py`
- `ProviderNameEnum.OXYLABS_PROVIDER = "oxylabs"` 추가

### 5) `proxy_proxy_ip_pool.py`
- `from proxy.providers.oxylabs_proxy import new_oxylabs_proxy` 추가
- `IpProxyProvider` dict에 OXYLABS_PROVIDER 등록

### 6) `tools_httpx_util.py`
- `curl_cffi.requests.AsyncSession` 사용 (Chrome TLS 위조)
- curl_cffi 미설치 시 httpx로 자동 폴백

### 7) `media_platform_xhs_login.py` (신규 패치)
- `login_by_cookies`가 **모든 쿠키 주입**하도록 변경 — 원본은 `web_session`만 박음
- 원본 동작: a1 누락 → xhshow sign 실패 → WAF 461
- 변경 후: a1, web_session, gid, acw_tc, websectiga 등 전부 주입

### 8) `media_platform_xhs_client.py` (신규 패치)
- `_fetch_via_page()` 신규 메서드 — Playwright 페이지에서 `fetch()` 직접 호출
  - httpx 우회 → 진짜 Chromium TLS/HTTP2 fingerprint → WAF 통과 시도
  - `credentials: 'include'`로 모든 cookie 자동 동봉
- `get_notes_by_creator()`가 `_fetch_via_page` 사용하도록 변경 — `user_posted` 엔드포인트 461 우회용
- 빈 `xsec_token`/`xsec_source`를 query에서 제외 (빈 값 명시는 WAF 봇 시그널)
- 461 응답에 진단 로그 추가 (응답 헤더 + body, `xhs-real-ip` 확인용)

### 9) `media_platform_xhs_core.py` (신규 패치)
- `page.goto` timeout 30초 → 90초 (xiaohongshu.com 무거워서 30초 자주 부족)
- `_save_cookies_to_output()` 신규 메서드 — 로그인 후 자동으로 `cn_social_listening/output/`에 저장
  - `xhs_cookie.txt` (재사용 가능한 단일 라인)
  - `xhs_session.json` (메타데이터 + cookie 객체 전체)
- 로그인 flow 끝에서 자동 호출

---

## 검증 — 패치 잘 적용됐나 확인

```bash
cd crawlers/MediaCrawler

# venv 활성화 후
python -c "from proxy.types import ProviderNameEnum; print(ProviderNameEnum.OXYLABS_PROVIDER.value)"
# 출력: oxylabs

python -c "from proxy.providers.oxylabs_proxy import new_oxylabs_proxy; print(new_oxylabs_proxy())"
# 출력: [OxylabsProxy] sessid=... sesstime=30m
#       <proxy.providers.oxylabs_proxy.OxylabsProxy object at ...>

python -c "from tools.httpx_util import _HAS_CURL_CFFI; print('curl_cffi:', _HAS_CURL_CFFI)"
# 출력: curl_cffi: True (False면 pip install curl_cffi)

python -c "from media_platform.xhs.client import XiaoHongShuClient; print('client patched:', hasattr(XiaoHongShuClient, '_fetch_via_page'))"
# 출력: client patched: True
```

---

## 환경변수 (선택)

Oxylabs 자격증명을 코드에 박지 않고 env로 관리하려면:

```bash
# Windows PowerShell
$env:OXYLABS_HOST = "pr.oxylabs.io"
$env:OXYLABS_PORT = "7777"
$env:OXYLABS_USERNAME = "customer-prcs_data1_LpjIC-cc-cn"
$env:OXYLABS_PASSWORD = "Prcsdata_1234"
$env:OXYLABS_SESSID = "df0fd16fa363d490"   # sticky IP 고정 (선택)
$env:OXYLABS_SESSTIME = "30"                # 분, residential 최대 30

# Git Bash / Linux
export OXYLABS_HOST=pr.oxylabs.io
export OXYLABS_PORT=7777
export OXYLABS_USERNAME=customer-prcs_data1_LpjIC-cc-cn
export OXYLABS_PASSWORD=Prcsdata_1234
export OXYLABS_SESSID=df0fd16fa363d490
export OXYLABS_SESSTIME=30
```

미설정 시 기본값(도우인과 동일) 사용. **`OXYLABS_SESSID`만 설정하면 cookie 발급 IP와 API 호출 IP를 같게 유지**할 수 있음 (단 sesstime 만료되면 자동 회전).

---

## 트러블슈팅

| 증상 | 원인 | 해결 |
|------|------|------|
| `module 'config' has no attribute 'XHS_INTERNATIONAL'` | xhs_config.py 패치 안 됨 | Step 3 다시 |
| `module 'config' has no attribute 'CDP_CONNECT_EXISTING'` | base_config.py 패치 안 됨 | Step 3 다시 |
| `IpProxyProvider.get('oxylabs')` returns None | proxy_types.py + proxy_proxy_ip_pool.py 패치 안 됨 | Step 3 다시 |
| `curl_cffi: False` | curl_cffi 미설치 | `pip install curl_cffi` |
| `Executable doesn't exist at .../chromium-1124/...` | playwright 다운그레이드 후 chromium 미설치 | `playwright install chromium` |
| `ImportError: xhs` | xhs 라이브러리 미설치 | `pip install xhs` |
| `Page.goto: net::ERR_TIMED_OUT` | Oxylabs 출구 IP가 xhs CDN에 차단됨 | `unset OXYLABS_SESSID` 후 재실행 (새 sessid → 새 IP) |
| `CAPTCHA appeared, request failed, Verifytype: 301` | xhs WAF가 봇 의심으로 슬라이더 캡차 강제 | (1) `OXYLABS_SESSID` 새로 받기 (2) `HEADLESS=False`라 브라우저에서 직접 슬라이더 풀기 |
| 로그인 후 `pong` False 반복 | cookie 발급 IP ≠ 현재 IP | `OXYLABS_SESSID` 환경변수 고정 |
| browser_data 깨졌을 때 | persistent session 부패 | `rm -rf crawlers/MediaCrawler/browser_data/xhs_user_data_dir` |

---

## 실행 — 두 가지 흐름

### A. MediaCrawler 흐름 (전통)

```bash
# venv 활성, MediaCrawler 디렉토리에서
cd crawlers/MediaCrawler

# 첫 실행 — QR 로그인 + 크롤
python main.py --platform xhs --lt qrcode --type creator

# cookie 살아있을 때 — 재사용 (output/xhs_cookie.txt를 base_config.COOKIES에 박은 뒤)
python main.py --platform xhs --lt cookie --type creator

# 또는 weekly 래퍼
python runners/run_xhs_weekly_local.py --week 0427
```

- 결과: `MediaCrawler/output/red-weekly-26{MMDD}/`에 creator/notes JSON 저장
- 로그인 후 cookie는 자동으로 `cn_social_listening/output/xhs_cookie.txt`에도 저장됨

### B. 하이브리드 흐름 (xhs 라이브러리, 영속 세션)

```bash
python runners/run_xhs_hybrid_test.py --user-id 5842afd75e87e7332ea90fda --max-notes 5
```

- 첫 실행 시 QR 1회 → user_data_dir 영속
- 이후 자동 진행 (cookie 만료 신경 X)
- MediaCrawler 거의 안 씀 (xhs library + Playwright만)
- ⚠️ 현재 xhs 라이브러리의 sign 알고리즘 outdated — WAF 401/406 가능. 운영은 A 권장.

---

## 업데이트 — 추가 패치 시

MediaCrawler 본체 파일을 수정하면 **반드시 백업본도 같이 갱신**:

```bash
# 예: tools/httpx_util.py 수정 후
cp crawlers/MediaCrawler/tools/httpx_util.py crawlers/mediacrawler-config/tools_httpx_util.py
git add crawlers/mediacrawler-config/tools_httpx_util.py
git commit -m "Update httpx_util patch"
```

또는 모든 패치 한 번에 백업:

```bash
SRC="crawlers/MediaCrawler"
DST="crawlers/mediacrawler-config"

cp "$SRC/config/base_config.py"               "$DST/base_config.py"
cp "$SRC/config/xhs_config.py"                "$DST/xhs_config.py"
cp "$SRC/proxy/providers/oxylabs_proxy.py"    "$DST/oxylabs_proxy.py"
cp "$SRC/proxy/types.py"                      "$DST/proxy_types.py"
cp "$SRC/proxy/proxy_ip_pool.py"              "$DST/proxy_proxy_ip_pool.py"
cp "$SRC/tools/httpx_util.py"                 "$DST/tools_httpx_util.py"
cp "$SRC/media_platform/xhs/login.py"         "$DST/media_platform_xhs_login.py"
cp "$SRC/media_platform/xhs/client.py"        "$DST/media_platform_xhs_client.py"
cp "$SRC/media_platform/xhs/core.py"          "$DST/media_platform_xhs_core.py"
```

이렇게 하면 다른 PC에서 `git pull` 후 같은 패치 적용 가능.
