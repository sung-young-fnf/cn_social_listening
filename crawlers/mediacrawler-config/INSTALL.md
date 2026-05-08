# MediaCrawler 패치 설치 가이드

이 폴더는 `cn-social-listening` 레포가 관리하는 **MediaCrawler 커스텀 설정 + 패치 백업**입니다.
MediaCrawler 본체는 외부 OSS(NanmiCoder/MediaCrawler)이고, 클론 후 우리 패치를 덮어써야 동작.

## 폴더 구조

```
crawlers/mediacrawler-config/
├── INSTALL.md                       ← 이 문서
├── base_config.py                   ← MediaCrawler/config/base_config.py 대체
├── xhs_config.py                    ← MediaCrawler/config/xhs_config.py 대체
├── oxylabs_proxy.py                 ← MediaCrawler/proxy/providers/oxylabs_proxy.py (신규)
├── proxy_types.py                   ← MediaCrawler/proxy/types.py 대체
├── proxy_proxy_ip_pool.py           ← MediaCrawler/proxy/proxy_ip_pool.py 대체
└── tools_httpx_util.py              ← MediaCrawler/tools/httpx_util.py 대체
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

# MediaCrawler 본체 의존성
pip install -r requirements.txt

# 우리 추가 의존성 (TLS 위조)
pip install curl_cffi

# Playwright 브라우저 (CDP 모드 끔으로 인해 번들 Chromium 필요)
playwright install chromium
```

### Step 3: 우리 패치 적용 (6파일)

`cn-social-listening` 레포 루트 기준으로 실행:

#### Windows PowerShell
```powershell
$src = "crawlers\mediacrawler-config"
$dst = "crawlers\MediaCrawler"

Copy-Item "$src\base_config.py"           "$dst\config\base_config.py" -Force
Copy-Item "$src\xhs_config.py"            "$dst\config\xhs_config.py" -Force
Copy-Item "$src\oxylabs_proxy.py"         "$dst\proxy\providers\oxylabs_proxy.py" -Force
Copy-Item "$src\proxy_types.py"           "$dst\proxy\types.py" -Force
Copy-Item "$src\proxy_proxy_ip_pool.py"   "$dst\proxy\proxy_ip_pool.py" -Force
Copy-Item "$src\tools_httpx_util.py"      "$dst\tools\httpx_util.py" -Force
```

#### Git Bash / Linux / macOS
```bash
SRC="crawlers/mediacrawler-config"
DST="crawlers/MediaCrawler"

cp "$SRC/base_config.py"           "$DST/config/base_config.py"
cp "$SRC/xhs_config.py"            "$DST/config/xhs_config.py"
cp "$SRC/oxylabs_proxy.py"         "$DST/proxy/providers/oxylabs_proxy.py"
cp "$SRC/proxy_types.py"           "$DST/proxy/types.py"
cp "$SRC/proxy_proxy_ip_pool.py"   "$DST/proxy/proxy_ip_pool.py"
cp "$SRC/tools_httpx_util.py"      "$DST/tools/httpx_util.py"
```

### Step 4: 추가 패키지 설치 (하이브리드 흐름용)

```bash
pip install xhs
```

---

## 파일별 변경 내역

### 1) `base_config.py`
- `LOGIN_TYPE = "cookie"` (또는 "qrcode")
- `COOKIES = "..."` (직접 박는 web_session)
- `ENABLE_IP_PROXY = True`
- `IP_PROXY_PROVIDER_NAME = "oxylabs"`
- `ENABLE_CDP_MODE = False` (⚠️ 중요 — 프록시 우회 방지)
- `CDP_CONNECT_EXISTING = False` (신규 추가, 누락 시 startup crash)
- `CRAWLER_MAX_SLEEP_SEC = 15` (인간 패턴)
- `XHS_INTERNATIONAL` 처리 (xhs_config.py로 이전)

### 2) `xhs_config.py`
- `XHS_INTERNATIONAL = False` (신규 추가, xiaohongshu.com 사용)
- `XHS_CREATOR_ID_LIST` (210명 인플루언서 URL)

### 3) `oxylabs_proxy.py` (신규)
- Oxylabs Backconnect provider 구현
- 도우인 자격증명 재사용 (환경변수로 override 가능)

### 4) `proxy_types.py`
- `ProviderNameEnum.OXYLABS_PROVIDER = "oxylabs"` 추가

### 5) `proxy_proxy_ip_pool.py`
- `from proxy.providers.oxylabs_proxy import new_oxylabs_proxy` 추가
- `IpProxyProvider` dict에 OXYLABS_PROVIDER 등록

### 6) `tools_httpx_util.py`
- `curl_cffi.requests.AsyncSession` 사용 (Chrome TLS 위조)
- curl_cffi 미설치 시 httpx로 자동 폴백

---

## 검증 — 패치 잘 적용됐나 확인

```bash
cd crawlers/MediaCrawler

# venv 활성화 후
python -c "from proxy.types import ProviderNameEnum; print(ProviderNameEnum.OXYLABS_PROVIDER.value)"
# 출력: oxylabs

python -c "from proxy.providers.oxylabs_proxy import new_oxylabs_proxy; print(new_oxylabs_proxy())"
# 출력: <OxylabsProxy object at ...>

python -c "from tools.httpx_util import _HAS_CURL_CFFI; print('curl_cffi:', _HAS_CURL_CFFI)"
# 출력: curl_cffi: True (False면 pip install curl_cffi)
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

# Git Bash / Linux
export OXYLABS_HOST=pr.oxylabs.io
export OXYLABS_PORT=7777
export OXYLABS_USERNAME=customer-prcs_data1_LpjIC-cc-cn
export OXYLABS_PASSWORD=Prcsdata_1234
```

미설정 시 `oxylabs_proxy.py`의 기본값(도우인과 동일) 사용.

---

## 트러블슈팅

| 증상 | 원인 | 해결 |
|------|------|------|
| `module 'config' has no attribute 'XHS_INTERNATIONAL'` | xhs_config.py 패치 안 됨 | Step 3 다시 |
| `module 'config' has no attribute 'CDP_CONNECT_EXISTING'` | base_config.py 패치 안 됨 | Step 3 다시 |
| `IpProxyProvider.get('oxylabs')` returns None | proxy_types.py + proxy_proxy_ip_pool.py 패치 안 됨 | Step 3 다시 |
| `curl_cffi: False` | curl_cffi 미설치 | `pip install curl_cffi` |
| `playwright._impl._errors.Error: Executable doesn't exist` | Chromium 미설치 | `playwright install chromium` |
| `ImportError: xhs` | xhs 라이브러리 미설치 | `pip install xhs` |

---

## 실행 — 두 가지 흐름

### A. MediaCrawler 흐름 (전통)

```bash
python runners/run_xhs_weekly_local.py --week 0427
```

- base_config.py의 LOGIN_TYPE에 따라 cookie 또는 qrcode 로그인
- 200명 인플루언서 일괄 크롤링
- 결과 → MediaCrawler/output/red-weekly-26{MMDD}/csv/

### B. 하이브리드 흐름 (xhs 라이브러리, 영속 세션)

```bash
python runners/run_xhs_hybrid_test.py --user-id 5842afd75e87e7332ea90fda --max-notes 5
```

- 첫 실행 시 QR 1회 → user_data_dir 영속
- 이후 자동 진행 (cookie 만료 신경 X)
- MediaCrawler 거의 안 씀 (xhs library + Playwright만)

---

## 업데이트 — 추가 패치 시

MediaCrawler 본체 파일을 수정하면 **반드시 백업본도 같이 갱신**:

```bash
# 예: tools/httpx_util.py 수정 후
cp crawlers/MediaCrawler/tools/httpx_util.py crawlers/mediacrawler-config/tools_httpx_util.py
git add crawlers/mediacrawler-config/tools_httpx_util.py
git commit -m "Update httpx_util patch"
```

이렇게 하면 다른 PC에서 `git pull` 후 같은 패치 적용 가능.
