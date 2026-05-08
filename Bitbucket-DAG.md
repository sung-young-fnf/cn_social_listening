# 운영 Airflow DAG (Bitbucket — `fnf-insight`)

| 필드 | 값 |
|---|---|
| 유형 | Pipeline |
| 위치 | **별도 Bitbucket 저장소** (`F&F_Et/fnf-insight/`) |
| DAG 파일 | `call_fne_insight_daily_batch.py` |
| 키워드 | `Bitbucket DAG`, `운영 DAG`, `call_fne_insight_daily_batch`, `매일 KST 06:00`, `00 21 * * *`, `Task Group`, `crawl_list_tasks`, `apify_crawling_tasks`, `postgres_etl_tasks`, `ai_analysis_tasks`, `Grok 웹검색`, `3-Stage 분석`, `Slack 알림` |
| 관련문서 | [[Airflow-DAG]], [[AI-분석-Gemini]], [[Grok-웹검색-통합]], [[데이터-보존-정책]], [[챌린지-종합분석]] |

> ⚠️ **중요**: 이전 wiki의 [[Airflow-DAG]]는 GitHub `fnf-entertainment-insights/airflow/dags/trend_pipeline/dag.py`를 다뤘는데, **그 DAG는 미러/예시일 뿐 실제 운영되는 것은 이 Bitbucket DAG**입니다. 진짜 매일 6시 자동 수집 → DB 적재는 이 문서의 DAG가 수행합니다.

---

## 1. DAG 메타

| 항목 | 값 |
|---|---|
| `dag_id` | `call_fne_insight_daily_batch` (파일명에서 자동 도출) |
| `schedule_interval` | `"00 21 * * *"` (UTC 21:00 = **KST 06:00**) |
| `catchup` | `False` |
| `max_active_runs` | `1` |
| `tags` | `['fne_crawler', 'insight', 'daily', 'apify']` |
| `owner` | `ChaCha` |
| Slack 알림 | `on_success_callback` + `on_failure_callback` + 파이프라인 요약 자동 전송 |

---

## 2. Task Group 6개 — 의존성 그래프

```
start
  │
  ▼
[TG1] extract_crawl_list             ← PostgreSQL crawl_targets 조회
  │
  ▼
[TG2] apify_crawling                  ← 4개 플랫폼 병렬 Apify + Adapter 변환 + S3 raw 저장
  │  (crawl_instagram / crawl_youtube / crawl_x / crawl_tiktok)
  │
  ▼
[TG3] s3_media_storage                ← 썸네일/비디오 S3 미러 업로드
  │
  ▼
[TG4] ai_analysis                     ← 3-Stage AI 분석 (Grok → 분류 → 상세)
  │
  ▼
[TG5] s3_analysis_storage             ← 분석 타입별 grouped archive + summary.json 저장
  │
  ▼
[TG6] postgres_etl                    ← S3 → DB raw_content + sp_run_daily_etl + dw_ai_analysis 동기화
  │
  ▼
challenge_summary_batch (SimpleHttpOperator)
  │  POST /server/trend/challenge-summary/batch-incremental → NestJS 트리거
  ▼
send_pipeline_summary                 ← Slack 요약 전송
  │
  ▼
clean_xcom                            ← XCom 정리
  │
  ▼
end
```

→ 전체가 **하나의 DAG에 통합된 6 Task Group + 4 Operator** 구조.

---

## 3. TG1 — 크롤링 리스트 추출 (`crawl_list_tasks.py`)

### 3.1 동작
PostgreSQL `crawl_targets` 테이블에서 `is_active = true`인 행을 조회 → 플랫폼별로 분리해 XCom에 저장.

```sql
SELECT platform, url, group_name
FROM insight_fne.crawl_targets
WHERE is_active = true
ORDER BY platform, group_name
```

### 3.2 XCom 저장
- `extract_crawl_targets`: 전체 리스트
- `crawl_list_instagram`, `crawl_list_youtube`, `crawl_list_x`, `crawl_list_tiktok`: 플랫폼별

### 3.3 Fallback
PostgreSQL 연결 실패 시 코드 내 기본 4개 (Cortis/ILLIT/TWS) 사용.

> 🔗 이전 wiki [[데이터-보존-정책]]에서 "Airflow Variable `CRAWL_TARGETS`와 DB `crawl_targets` 테이블이 분리되어 있다"고 적었는데 → **실제는 Bitbucket DAG가 DB를 직접 조회하므로 Variable 방식은 사용 안 함**.

---

## 4. TG2 — Apify 크롤링 + S3 Raw 저장 통합 (`apify_crawling_tasks.py`)

### 4.1 통합 설계
이전엔 `apify_crawling`과 `s3_raw_storage` 두 단계였는데, **하나로 합쳐짐**. XCom에는 **S3 경로만** 저장 (대용량 데이터 X).

### 4.2 단계
```
플랫폼별 task (4개 병렬):
1. XCom에서 crawl_list 가져오기
2. run_parallel_crawling(crawl_list, platform, build_input_func, apify_tokens)
   - APIFY_TOKEN_LIST를 로테이션 방식으로 사용 (rate limit 회피)
3. AdapterFactory.transform_batch(platform, raw_items) → UnifiedContent
4. sanitize_for_json (\x00, \r\n 등 제어문자 제거 — PostgreSQL JSONB 호환)
5. Instagram/X: contentType이 'video'인 것만 필터링
6. S3에 JSON 저장 (minify, 줄바꿈 없음)
   경로: {S3_RAW_DATA_PREFIX}/platform={platform}/std_date={std_date}/data.json
7. XCom에 raw_storage_result_{platform} = {s3_key, s3_url, item_count, ...}
```

### 4.3 플랫폼별 Apify Input (운영 실제값)

| 플랫폼 | 핵심 input |
|---|---|
| Instagram | `{username:[url], resultsType:'posts', onlyPostsNewerThan: crawl_std_date}` |
| YouTube | `{startUrls:[{url}], oldestPostDate:'14 days', maxResults:1000, maxResultsShorts:1000, includeShorts:true}` |
| X (Twitter) | `{startUrls:[url], onlyVideo:true, sort:'Latest', start: crawl_std_date, tweetLanguage:'ko', maxItems:100}` |
| TikTok | `{profiles:[url], oldestPostDateUnified:'14 days', resultsPerPage:200, shouldDownloadVideos:true, excludePinnedPosts:true}` |

→ wiki [[데이터-수집-Apify]]에 적힌 NestJS 측 input(50개)과 다름. **운영은 100~1000개**로 더 크게 가져옴.

---

## 5. TG3 — S3 미디어 저장 (`s3_media_storage_tasks.py`)

### 5.1 동작
- TG2의 S3 raw 경로(`raw_storage_result_{platform}.s3_key`)를 받아 S3에서 unified JSON을 다시 읽음
- 각 콘텐츠에서 미디어 URL 추출
- 플랫폼별 정책:
  - **Instagram**: `Image`/`Sidecar` 스킵, `Video`만 업로드
  - **YouTube**: **비디오는 다운로드/업로드 안 함** (롱폼/숏폼 모두 원본 YouTube URL 그대로 사용 — Gemini가 YouTube URL을 직접 분석할 수 있음). 썸네일은 S3 저장 가능.
  - **TikTok / X**: 썸네일 + Video 모두 미러
- 다운로드 → S3 미러 업로드 (병렬, `s3_helper.upload_media_batch_from_urls`)
- 결과: `s3VideoUrl` / `s3ThumbnailUrl`을 unified JSON에 in-place 갱신해서 다시 S3에 PUT

> ⚠️ 함수 docstring 일부에는 "YouTube 숏츠만 비디오 다운로드"라고 적혀있지만, **실제 코드는 `if platform == 'youtube': skip`**으로 롱폼/숏폼 구분 없이 비디오 다운로드를 모두 스킵한다 (`_extract_and_upload_media`, `_get_video_links`).

### 5.2 경로
업로드 경로는 NestJS S3 정책과 동일하게 [[S3-미디어-미러]] 1장 참고.

---

## 6. TG4 — AI 분석 (3-Stage) (`ai_analysis_tasks.py`)

⭐ **가장 큰 변경** — 이전 wiki [[AI-분석-Gemini]]에 적힌 2-Stage가 아니라 **3-Stage Pipeline**.

### 6.1 3-Stage 구조

```
Stage 0 (NEW): Grok 웹검색
  - xAI API (https://api.x.ai/v1/responses)
  - 모델: grok-4-1-fast
  - tools: web_search + x_search 동시 활성화
  - K-pop 컨텍스트(아티스트/곡/최근 활동/해시태그) 실시간 검색
  ↓
Stage 1: 카테고리 분류
  - Gemini (S3에서 category_classification.json 프롬프트 로드)
  - Grok 결과를 프롬프트 맨 앞에 주입 ([K-pop Context] 섹션)
  - 결과: 4가지 카테고리 중 하나
  ↓
Stage 2: 카테고리별 상세 분석
  - Gemini (S3에서 카테고리별 프롬프트 로드)
  - 동일하게 Grok 결과 주입
  - 결과: 카테고리별 5~6섹션 구조화 JSON
```

자세한 흐름: [[Grok-웹검색-통합]]

### 6.2 프롬프트 로딩 — S3에서 fresh load
매 분석 시작 시 5개 프롬프트 모두 S3에서 새로 로드:
- `https://svc-fne-insight-s3.s3.ap-northeast-2.amazonaws.com/analysis/prompt/{name}.json`
→ NestJS 측 `PromptConfigService`의 5분 캐시와 다른 동작 (DAG는 매번 fresh).

### 6.3 검색 쿼리 빌드 (`_build_search_query`)
```
hashtags(노이즈 제거 후 5개) + musicName + musicAuthor + authorName + "K-pop"
```
노이즈 태그 (검색에서 제외): `fyp`, `foryou`, `viral`, `trending`, `kpop`, `dance` 등

### 6.4 메타데이터 신뢰 지시
프롬프트 안에 자동 주입:
- `musicOriginal=true` → "플랫폼에 음원 미등록, 영상에서 음악 들리면 식별 시도"
- `musicOriginal=false` + musicName 있음 → "API 공식 데이터, 분석 결과에 반드시 반영"

### 6.5 동시성
- `AI_ANALYSIS_CONCURRENCY` 상수로 제어
- `concurrent.futures.ThreadPoolExecutor` 사용 (NestJS의 Semaphore와 다름)

### 6.6 키워드 정규화 (`_normalize_keywords`)
밈/챌린지 결과의 keywords를 PascalCase로 정리 — NestJS [[AI-분석-Gemini]] 6장과 동일 로직.

### 6.7 ⭐ per-content 분석 JSON S3 저장 (TG4 안에서 처리)

**TG4 자체가 per-content 분석 JSON을 S3에 저장한다** (TG5가 아님). 각 콘텐츠 분석이 성공하면 `concurrent.futures.as_completed` 루프 안에서 즉시 PUT:

```
s3_key = f"{S3_ANALYSIS_PREFIX}/{platform}/{content_id}/{content_id}.json"
s3_helper.upload_string(data=json_minified, s3_key=..., content_type="application/json")
```

→ 이 파일이 TG6의 `_load_ai_analysis_from_s3`가 누락분 적재 시 읽어가는 소스다.

---

## 7. TG5 — 그룹별 아카이브 + 요약 저장 (`s3_analysis_storage_tasks.py`)

### 7.1 동작 — TG4와 다른 산출물

TG5는 **per-content가 아니라 분석 타입별 grouped 아카이브 + 요약 파일**만 저장한다 (per-content는 §6.7에서 TG4가 이미 저장 완료).

- 4개 분석 유형(`AnalysisType`)별로 task 생성 (`_store_analysis_to_s3`)
- 같은 타입 안에서 **플랫폼별로 그룹화** → 한 파일에 여러 콘텐츠
- 그룹 아카이브 키: `{S3_ANALYSIS_PREFIX}/{analysis_type}/year=YYYY/month=MM/day=DD/{platform}_analysis.json`
- 요약 키: `{S3_ANALYSIS_PREFIX}/{analysis_type}/year=YYYY/month=MM/day=DD/summary.json`

### 7.2 산출물 비교 (TG4 vs TG5)

| 산출물 | 위치 | 누가 |
|---|---|---|
| per-content 분석 JSON (TG6 적재의 소스) | `{S3_ANALYSIS_PREFIX}/{platform}/{content_id}/{content_id}.json` | **TG4** (`ai_analysis_tasks.py`) |
| 분석 타입별 그룹 아카이브 | `{S3_ANALYSIS_PREFIX}/{type}/year=…/month=…/day=…/{platform}_analysis.json` | **TG5** (`s3_analysis_storage_tasks.py`) |
| 일자별 요약 | `{S3_ANALYSIS_PREFIX}/{type}/year=…/month=…/day=…/summary.json` | **TG5** |

### 7.3 sanitize_for_json
NestJS와 동일한 제어문자 제거 헬퍼 (PostgreSQL JSONB 적재 호환).

---

## 8. TG6 — PostgreSQL ETL (`postgres_etl_tasks.py`) ⭐

⭐ **이전 wiki에서 가장 잘못 추정했던 부분** — 실제로는 Airflow가 직접 DB 적재까지 함.

### 8.1 3단계 순차 실행

```
load_raw_content      → S3 → raw_content 테이블 (Python requests + psycopg2 직접)
       ↓
run_daily_etl         → CALL insight_fne.sp_run_daily_etl(KST date)
       ↓
load_ai_analysis      → S3 → dw_ai_analysis 테이블 (누락분만)
```

### 8.2 `_load_raw_content_from_s3` — Python 직접 적재 (boto3 아님)

> ⚠️ 함수 docstring에는 "boto3 + psycopg2"라고 적혀있지만, **실제 코드는 boto3를 import하지 않는다**. import는 `requests`, `psycopg2.extras.execute_values`, `airflow.providers.postgres.hooks.postgres.PostgresHook`. S3 파일은 public URL에 `requests.get()`으로 직접 접근.

흐름:
1. `pg_hook = PostgresHook(postgres_conn_id=POSTGRES_CONN_ID)` → `conn = pg_hook.get_conn()` → `cursor = conn.cursor()`
2. `requests.get(f"{S3_BASE_URL}/{s3_key}")` → `response.json()`로 JSON 다운로드 (PostgreSQL `aws_s3.table_import_from_s3` 확장 사용 안 함)
3. content_id 중복 제거 + 빈 ID 스킵
4. `psycopg2.extras.execute_values(cursor, insert_sql, rows, template="(%s, %s, %s, %s::jsonb)", page_size=1000)` — 한 번의 쿼리로 bulk INSERT
5. `ON CONFLICT (std_date, platform, content_id) DO UPDATE SET raw_json = EXCLUDED.raw_json, loaded_at = NOW()`

→ DAG 입장에서 [[데이터베이스-프로시저]] `sp_load_from_s3`를 **직접 호출하지 않는 대신** Python 코드가 같은 역할을 수행.

### 8.3 `_run_daily_etl` — 프로시저 호출 (PostgresHook.run)

```python
pg_hook = PostgresHook(postgres_conn_id=POSTGRES_CONN_ID)
sql = f"CALL {POSTGRES_SCHEMA}.sp_run_daily_etl('{kst_date}')"
pg_hook.run(sql)
```

→ `cursor.execute(...)`가 아니라 `PostgresHook.run()` 사용 (단발성 SQL이라 더 간결).

호출되면 프로시저 정의에 따라 `sp_load_from_s3` → `sp_upsert_dw_content` → `sp_sync_all_analysis`가 순차 실행되지만, 흐름 B 입장에서 **실질 효과는 `sp_upsert_dw_content`** (`raw_content` → `dw_content` UPSERT + `fact_engagement_history` INSERT). 자세히는 [[데이터베이스-프로시저]] §1.1.

### 8.4 `_load_ai_analysis_from_s3` — 누락분 동기화

```sql
SELECT c.platform, c.content_id
FROM dw_content c
WHERE NOT EXISTS (SELECT 1 FROM dw_ai_analysis a WHERE ...)
```

각 누락 콘텐츠마다 §6.7에서 TG4가 저장한 per-content JSON을 `requests.get()`으로 다운로드 → `cursor.execute` + `execute_values`로 `dw_ai_analysis`에 직접 UPSERT.

S3 소스 경로 (TG4가 저장): `{S3_ANALYSIS_PREFIX}/{platform}/{content_id}/{content_id}.json`

→ DAG 입장에서 [[데이터베이스-프로시저]] `sp_load_analysis_from_s3` / `sp_sync_all_analysis`를 **직접 호출하지 않음**. 다만 §8.3에서 `sp_run_daily_etl` 안의 `sp_sync_all_analysis`가 한 번 돌고, 그 직후 여기서 한 번 더 누락분을 잡는 **이중 안전망 구조**.

---

## 9. 챌린지 종합분석 트리거

ETL 완료 후 NestJS API 호출:

```python
SimpleHttpOperator(
    task_id='generate_challenge_summaries',
    http_conn_id='fne_insight_api',
    endpoint='/server/trend/challenge-summary/batch-incremental',
    method='POST',
    data='{}',
    response_check=lambda r: r.status_code == 202,
    extra_options={'timeout': 600},
)
```

→ NestJS [[챌린지-종합분석]] 배치를 자동 트리거. 매일 6시 ETL 완료 후 신규/변경 챌린지만 갱신.

---

## 10. Slack 알림 (`send_pipeline_summary`)

```
*<FnE Insight Daily Batch 완료 알람>*
📅 기준일: 2026-04-28

*📥 크롤링 결과:*
  • INSTAGRAM: 50건
  • YOUTUBE: 120건
  • X: 80건
  • TIKTOK: 200건

*💾 DB 저장 결과:*
  • INSTAGRAM: 0건  ← postgres_refresh_tasks가 주석 처리되어 0
  • ...

*🤖 AI 분석 결과:*
  • 총 분석: 450건
  • 성공: 430건
  • 스킵 (비디오 없음): 10건
  • 실패: 10건
```

> ⚠️ **postgres_refresh_tasks.py는 전체 주석 처리**되어 있어 DB 저장 결과는 항상 0건으로 보고됨. 실제 적재는 TG6 `postgres_etl`에서 처리. 슬랙 메시지 표기와 실제 동작이 어긋남 — 향후 수정 필요.

---

## 11. utils/ + constants/ 모듈 (이제 fnf-insight 안에 포함)

이전 wiki에서 "Bitbucket 저장소 다른 폴더로 추정"이라 적었던 모듈들이 **`fnf-insight/utils/`와 `fnf-insight/constants/`로 들어왔다**. 실제 시그니처/구성:

### 11.1 `constants/fne_insight_crawling_constants.py`

| 심볼 | 값 / 설명 |
|---|---|
| `Platform` (Enum) | `INSTAGRAM`, `TIKTOK`, `YOUTUBE`, `TWITTER`(`"x"`) |
| `ActorId` (Enum) | `INSTAGRAM_POST=nH2AHrwxeTRJoN5hX`, `INSTAGRAM_PROFILE=dSCLg0C3YEZ83HzYX`, `TIKTOK=GdWCkxBtKWOsKjdch`, `YOUTUBE=h7sDV53CddomktSi5`, `TWITTER=61RPP7dywgiy0JPD0` |
| `ACTOR_CONFIGS` | `{Platform: ActorConfig(actor_id, platform, slug)}` 딕셔너리 |
| `crawl_std_date` | **`(datetime.now() - timedelta(days=14))`** — DAG가 항상 **금일 -14일** 기준으로 크롤링 |
| `POSTGRES_CONN_ID` | `"postgres_fne_insight"` (Airflow Connection ID) |
| `POSTGRES_SCHEMA` | `"insight_fne"` |
| `S3_BUCKET` | `"op-milkyway-pub-s3"` (public 버킷) |
| `S3_BASE_URL` | `https://op-milkyway-pub-s3.s3.ap-northeast-2.amazonaws.com` |
| `S3_RAW_DATA_PREFIX` | `fnf_entertainment_dashboard/apify/raw_data` |
| `S3_MEDIA_PREFIX` | `fnf_entertainment_dashboard/media` |
| `S3_ANALYSIS_PREFIX` | `fnf_entertainment_dashboard/analysis` |
| `OXYLABS_PROXY_URLS` | 3개 프록시 (S3 미디어 다운로드 시 IP 회피용) |
| `APIFY_TOKEN_LIST` | **하드코딩된 단일 토큰** (`apify_api_eU…`) — 코드는 로테이션 가능 구조이나 현재 1개만 등록 |
| `AI_ANALYSIS_CONCURRENCY` | `5` (Gemini rate limit 고려) |
| 헬퍼 함수 | `get_kst_date_from_execution_date(execution_date) -> "%Y%m%d"`, `get_kst_date_formatted(execution_date, fmt='%Y-%m-%d')` |

### 11.2 `utils/apify_operator.py`

- `class ApifyService` — `run_actor(platform, input)`, `run_actor_by_id(actor_id, input)`, `start_actor_async(...)`, `fetch_dataset_items()`, `fetch_instagram_profiles(usernames)`, `extract_media_urls(items, platform)`(플랫폼별 sidecar/video/thumbnail 분기), `extract_post_id(url, platform)`(Instagram shortcode / TikTok video id / YouTube `?v=`/`/shorts/` / Twitter `/status/<id>` 추출)
- `class ApifyActorOperator(BaseOperator)` — 커스텀 Airflow Operator. 현재 DAG에선 사용 안 하고 함수형 `run_parallel_crawling()` 호출.
- `def run_parallel_crawling(crawl_list, platform, build_run_input_func, apify_tokens, max_workers, fetch_profiles=True)` — `ThreadPoolExecutor` 라운드로빈으로 토큰 분배. **Instagram에 한해 `_merge_instagram_profiles()` 자동 실행** — 게시물의 `ownerUsername` 추출 → Instagram Profile Actor 호출 → `post['_profile']`에 병합.

### 11.3 `utils/gemini_service.py`

- `class GeminiModel(Enum)`: `FLASH = "gemini-2.5-flash"`, `PRO = "gemini-3-pro-preview"`
- `@dataclass TokenUsage`: `input_tokens`, `output_tokens`, `total_tokens` 프로퍼티
- `class GeminiService` — 생성자 `api_key` fallback은 `Variable.get("CHN_MKT_GEMINI_API_KEY")`이지만, **실제 호출처(`ai_analysis_tasks.py:813`)는 `Variable.get("GEMINI_API_KEY")`로 명시 전달**해서 fallback은 죽은 코드. `analyze_video_structured(url, prompt, json_schema, model=PRO)` 등 메서드.

### 11.4 `utils/s3_presigned_helper.py`

- API endpoint: `https://ijd42e8h23.execute-api.ap-northeast-2.amazonaws.com/prod/generate` (사내 Lambda)
- API key: `Variable.get("S3_PRESIGNED_API_KEY")`
- `S3PresignedHelper` — `_get_presigned_url(method, s3_key, ...)`로 사내 Lambda에서 presigned URL 발급 후 `requests.put/get`. **PUT/GET/DELETE만 지원** (boto3 직접 사용 X).
- 미디어 다운로드 세션은 **Oxylabs 프록시 3개 통해서**: `build_media_session_with_oxylabs()`. `PARALLEL_DOWNLOAD_WORKERS = 3` (프록시 수와 동일).

> ⚠️ **누락된 의존성**: `s3_presigned_helper.py:10`이 `from utils.proxy_helper import build_media_session_with_oxylabs, get_all_proxy_urls, build_media_session_with_proxy`을 import하지만 **`fnf-insight/utils/proxy_helper.py`는 부재**. 운영 Bitbucket 저장소에서 이 파일을 추가로 가져와야 import 에러 없이 DAG 로딩 가능.

### 11.5 `utils/slack_connector.py`

- `class SlackConnector(channel_id, token_variable="slack_bot_token")` — DAG에서는 **`token_variable="fnf_slack_bot_token"`로 override**해서 사용.
- `post_message(text)` — `https://slack.com/api/chat.postMessage`로 POST.
- `write_tasks_message(**kwargs)` — DAG run의 task 결과 모아 메시지 빌드.

### 11.6 `utils/slack_alert.py`

- `class SlackAlert` — 생성자에서 `Variable.get("slack_bot_token")` (⚠️ DAG의 `fnf_slack_bot_token`과 **다른 Variable**).
- 콜백 메서드: `slack_success_alert(context)`, `slack_failure_alert(context)`, `slack_condition_alert(context)`, `slack_failure_alert_with_tag(context)`.
- 채널은 DAG `tags`로 자동 라우팅: `dms` → `#alert-prcs-airflow-batch-dms`, `external` → `#…-extdata`, `fnco_crawler` → `#airflow-fnco-influencer-batch`, 그 외 → `#alert-prcs-airflow-batch-general`. 실패 시 `#alert-prcs-airflow-batch-fail`에도 추가 발송.
- `slack-sdk WebClient` 사용 (SlackConnector의 raw `requests`와 다름).

---

## 12. 환경/Variable 의존 (정확한 매트릭스)

코드를 직접 추적해 확인한 Airflow Variable / Connection 목록:

| 이름 | 타입 | 출처 | 실제 사용처 |
|---|---|---|---|
| `slack_bot_token` | Variable | `SlackAlert.__init__` | DAG의 `on_success_callback` / `on_failure_callback` (utils/slack_alert.py:12) |
| `fnf_slack_bot_token` | Variable | `SlackConnector.__init__` (token_variable 인자) | DAG의 `send_pipeline_summary` 슬랙 메시지 발송 (call_fne_insight_daily_batch.py:49) |
| `slack_fne_insight_crawler` | Variable | DAG line 48 | 슬랙 채널 ID |
| `S3_PRESIGNED_API_KEY` | Variable | `PresignedURLConfig.__post_init__` | S3 PUT/GET용 사내 Lambda API 키 (utils/s3_presigned_helper.py:28) |
| `GEMINI_API_KEY` | Variable | `_run_ai_analysis` | Gemini 호출 (ai_analysis_tasks.py:813) |
| `GROK_API_KEY` | Variable | `_run_ai_analysis` | Grok 웹검색 호출 (ai_analysis_tasks.py:829) |
| `CHN_MKT_GEMINI_API_KEY` | Variable | `GeminiService.__init__` fallback | **현재 호출되지 않음** (api_key 인자가 항상 명시됨) |
| `postgres_fne_insight` | Connection | `PostgresHook(postgres_conn_id=POSTGRES_CONN_ID)` | TG6 PG 작업 |
| `fne_insight_api` | Connection (HTTP) | `SimpleHttpOperator(http_conn_id='fne_insight_api')` | NestJS 종합분석 트리거 |

> 💡 **Slack 토큰이 두 개**라는 점이 중요. callback 알림(SlackAlert)과 파이프라인 요약(SlackConnector)이 서로 다른 Variable을 보고 있어서 한쪽만 dummy면 다른 쪽이 정상 동작할 수 있음. 운영 토큰을 옮길 때 둘 다 잊지 말 것.

---

## 13. 이전 wiki 정정 사항

| 이전 wiki 내용 | 정정 (Bitbucket DAG 기준) |
|---|---|
| GitHub의 `airflow/dags/trend_pipeline/dag.py`가 매일 6시 실행 | ❌ 그건 미러/예시. 실제 운영은 Bitbucket의 `call_fne_insight_daily_batch.py` |
| Airflow가 DB에 적재하지 않고 파일에만 쌓음 | ❌ TG6에서 Python boto3 + psycopg2로 직접 raw_content/dw_ai_analysis에 INSERT |
| `CRAWL_TARGETS` Airflow Variable 사용 | ❌ Variable 방식 안 씀. `crawl_targets` DB 테이블 직접 조회 |
| Admin DB와 Airflow Variable 동기화 안 됨 | ✅ DB 단일 SoT — Admin UI 변경이 즉시 다음 6시 DAG에 반영됨 |
| AI 분석은 2-Stage Pipeline | ❌ Grok 웹검색을 추가한 **3-Stage** |
| `sp_load_from_s3` 프로시저 호출 | ⚠️ DAG는 **직접 호출하지 않고** Python 코드(`requests` + `execute_values`)로 적재. 다만 `sp_run_daily_etl`을 호출하므로 **정의에 따라 간접 실행**되고, 같은 PK에 대해 보조/중복으로 도는 형태 (자세히는 [[데이터베이스-프로시저]] §1.1) |
| 매일 자동 분석 후 별도 트리거 없음 | ❌ ETL 완료 후 NestJS `/trend/challenge-summary/batch-incremental` 자동 호출 |
| Apify 결과는 50개씩 | ⚠️ 운영 DAG는 100~1000개 (YouTube 1000, TikTok 200, X 100, Instagram resultsLimit 미지정) |

---

## 14. 한 줄 요약

> **Bitbucket의 `call_fne_insight_daily_batch.py`가 매일 KST 06:00에 실행되어 (1) `crawl_targets` DB에서 대상 조회 → (2) Apify 크롤링 + S3 raw 저장 → (3) S3 미디어 업로드(YouTube 비디오는 미러 안 하고 원본 URL 사용) → (4) Grok+Gemini 3-Stage AI 분석 + per-content JSON S3 저장 → (5) 분석 타입별 그룹 아카이브 + summary.json S3 저장 → (6) `requests`+`psycopg2.execute_values`로 `raw_content` 직접 INSERT, `PostgresHook.run("CALL sp_run_daily_etl")`, 누락분 `dw_ai_analysis` 직접 UPSERT → 마지막으로 NestJS `/trend/challenge-summary/batch-incremental` HTTP POST → Slack 알림 발송.** 이전 wiki에서 "DB로 안 흘러간다"고 추정했던 부분은 잘못된 정보였고, 실제로는 이 DAG가 모든 적재를 책임짐.

## 참고 파일

- DAG 메인: `F&F_Et/fnf-insight/call_fne_insight_daily_batch.py`
- TG1: `F&F_Et/fnf-insight/tasks/fne_insight/crawl_list_tasks.py`
- TG2: `F&F_Et/fnf-insight/tasks/fne_insight/apify_crawling_tasks.py`
- TG3: `F&F_Et/fnf-insight/tasks/fne_insight/s3_media_storage_tasks.py`
- TG4: `F&F_Et/fnf-insight/tasks/fne_insight/ai_analysis_tasks.py` (43KB)
- TG5: `F&F_Et/fnf-insight/tasks/fne_insight/s3_analysis_storage_tasks.py`
- TG6: `F&F_Et/fnf-insight/tasks/fne_insight/postgres_etl_tasks.py`
- (주석처리됨) `F&F_Et/fnf-insight/tasks/fne_insight/postgres_refresh_tasks.py`
- 어댑터: `F&F_Et/fnf-insight/tasks/fne_insight/adapters/{base,instagram,tiktok,twitter,youtube}_adapter.py`, `adapter_factory.py`, `unified_content.py`
- utils (§11.2~§11.6): `F&F_Et/fnf-insight/utils/{apify_operator,gemini_service,s3_presigned_helper,slack_alert,slack_connector}.py`
- 상수 (§11.1): `F&F_Et/fnf-insight/constants/fne_insight_crawling_constants.py`
- ⚠️ 의존성 누락: `F&F_Et/fnf-insight/utils/proxy_helper.py` (s3_presigned_helper가 import하지만 부재)

## 관련 문서
- [[Airflow-DAG]] (이전 wiki — GitHub 미러 DAG)
- [[AI-분석-Gemini]]
- [[Grok-웹검색-통합]]
- [[데이터베이스-프로시저]]
- [[챌린지-종합분석]]
- [[데이터-보존-정책]]
