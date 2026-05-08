# 도우인 Airflow 자동화 — Plan v1 (확정본)

## Context — 왜 하는가

도우인 주간 크롤링은 현재 `cn-social-listening` 로컬에서 `python runners/run_douyin_weekly.py --week 0323`을 사람이 수동 실행. 같은 흐름을 `fnf-dt-data-de-pipeline`의 기존 패턴(`call_chn_trend_weekly_batch` / `call_fne_insight_daily_batch`)에 맞춰 Airflow DAG로 옮겨, 매주 월요일 06:00에 자동 크롤링 → S3 업로드 → Slack 보고가 사람 손 없이 굴러가도록 만든다.

## 사용자 확정 답변 정리

| 항목 | 답변 |
|---|---|
| Node.js 실행 | Airflow worker 이미지에 추가 (BashOperator) |
| 코드 위치 | cn-social-listening은 그대로, 필요 코드만 fnf-dt-data-de-pipeline에 복사 |
| 스케줄 | 매주 월 KST 06:00 = `"0 21 * * 0"` |
| 샤오홍슈 | 이번 작업 제외 |
| **운영 배포** | **로컬만 우선**. 운영 배포는 추후 — 관련 경로 변수는 빈 값으로 자리만 잡아둠 |
| **S3 버킷** | **`svc-fnf-cn-mkt-s3` 그대로** (스크린샷 확인: `douyin/account/`, `douyin/profile/`, `douyin/keyword/`). uploaders 코드 로직 그대로 유지 |
| **Oxylabs CN 프록시** | `-cc-cn` 서브계정 그대로 사용. 세션 5계정마다 로테이션, progress.json 이어하기, 영상 CDN 직접 다운로드 등 v5의 모든 안전/비용 최적화 그대로 |
| 부분 실패 정책 | **즉시 DAG fail**, 추가 Slack 경고 없음 (기존 `on_failure_callback`이 알아서 알림) |
| sec_uid 관리 | **정적 JSON 유지** (`data/douyin-secuid-map.json`). 추후 Postgres화는 별건으로 |

## 운영 배포 경로 (현재 보류)

`bitbucket-pipelines.yml`은 `dags/`만 S3로 업로드. Node.js 런타임 + 크롤러 코드 배포 방법이 결정 안 됐으므로 **운영 관련 변경은 모두 placeholder만 잡고 비활성**:

- `bitbucket-pipelines.yml` 수정 **안 함**
- Dockerfile 수정은 **로컬 docker-compose용**으로만
- 크롤러 코드는 저장소 루트의 `cn_social/`에 두고, 로컬 Dockerfile이 COPY/install
- 운영 배포 시점에 다시 결정: MWAA `plugins.zip` / 자체 워커 이미지 / DockerOperator

---

## A. 파일 배치

```
fnf-dt-data-de-pipeline/
├── dags/
│   ├── call_cn_social_listening_weekly_batch.py     ← 신규 DAG
│   ├── constants/
│   │   └── cn_social_constants.py                    ← KST/CST 헬퍼, S3 경로, 크롤러 바이너리 경로(로컬 기본값 + 운영용 placeholder)
│   ├── tasks/cn_social/
│   │   ├── __init__.py
│   │   ├── tg1_prepare_tasks.py                      ← sec_uid 점검, date_start/date_end 계산
│   │   ├── tg2_douyin_crawl_tasks.py                 ← BashOperator → run.sh
│   │   ├── tg3_upload_account_tasks.py               ← parquet 빌드 + S3 PUT (uploaders 로직 이식)
│   │   ├── tg4_upload_post_tasks.py                  ← parquet + 썸네일 업로드 (uploaders 로직 이식)
│   │   └── tg5_cleanup_tasks.py                      ← output 폴더 청소 (다GB)
│   └── utils/
│       └── cn_mkt_s3_presigned_helper.py             ← 신규: aviyup1kyk Lambda + svc-fnf-cn-mkt-s3 래퍼
├── cn_social/                                         ← 신규 (로컬 워커가 COPY해서 사용)
│   ├── crawlers/douyin-weekly-v5.js                   ← cn-social-listening에서 복사 + env-var화 수정
│   ├── data/douyin-accounts.json                      ← 복사
│   ├── data/douyin-secuid-map.json                    ← 복사
│   ├── package.json                                    ← 복사
│   └── run.sh                                          ← 신규: env 검증 + npm ci + node 실행
├── Dockerfile                                          ← Node.js 20 + npm ci 추가 (로컬 dev)
├── requirements.txt                                    ← pyarrow 추가
├── docker-compose.yaml                                 ← AIRFLOW_VAR_CN_SOCIAL_* 추가
└── bitbucket-pipelines.yml                             ← 손대지 않음 (운영 배포 보류)
```

## B. 크롤러 코드 복사 시 변경

### `crawlers/douyin-weekly-v5.js`
- **CONFIG 블록 (lines 27-52)** → `process.env.DOUYIN_DATE_START`, `DOUYIN_DATE_END`, `DOUYIN_OUTPUT_DIR` 사용. 다른 옵션(`maxAccountsPerSession=5`, `sortTypes=[0,1]`, `pageSize=35`, `gapWarningDays=3`, `delayBetweenApi=2000`, …)은 **그대로 유지** — 사용자 명시: 모든 안전/비용 최적화 보존
- **Oxylabs creds (lines 421-423)** → `process.env.OXYLABS_USERNAME` / `OXYLABS_PASSWORD`. **`-cc-cn` 접미사 포함된 값** 그대로 (China geo 라우팅 필수)
- **세션 로테이션, progress.json, CDN 직접 다운로드 로직** → 모두 유지

### `runners/run_douyin_weekly.py`
- **통째로 폐기**. regex 치환 패턴은 동시성에 위험. Airflow가 env-var로 주입.

### uploaders (`s3_upload_douyin_account.py`, `s3_upload_douyin_post.py`)
- 로직 자체는 그대로 — 같은 Lambda(`aviyup1kyk`), 같은 버킷(`svc-fnf-cn-mkt-s3`), 같은 parquet 19컬럼 스키마
- 변경: `os.getenv("S3_API_KEY")` → `Variable.get("CN_SOCIAL_S3_API_KEY")`, `dotenv` import 삭제
- `main()` 본문을 task 함수로 추출 (`tg3_upload_account_tasks.py:upload_accounts(data_dir, **context)`)
- `cn_mkt_s3_presigned_helper.py`로 presigned URL 발급 부분 추출

## C. DAG 구조

```
start
  → tg1_prepare                     # 계정/sec_uid 점검, date_start/date_end (CST) 계산
  → tg2_douyin_crawl               # BashOperator, ~1-2hr
                                    #   execution_timeout=3hr, retries=0
                                    #   pool=cn_social_crawler (slots=1)
  → [tg3_upload_account, tg4_upload_post]   # 병렬
  → tg5_cleanup_output             # rm -rf ${DOUYIN_OUTPUT_DIR}
  → send_pipeline_summary          # Slack 요약
  → clean_xcom
  → end
```

골격은 `dags/call_chn_trend_weekly_batch.py:138-196` 그대로:
- `SlackAlert()` callback (`on_success_callback`, `on_failure_callback`)
- `SlackConnector(channel_id, token_variable="fnf_slack_bot_token")` for 요약
- `DummyOperator` start/end + `cleanup_xcom`
- `max_active_runs=1`, `catchup=False`
- `schedule_interval = "0 21 * * 0"`
- `tags=['cn_social', 'weekly', 'douyin', 'crawler']`

**chn_trend와 다른 부분 (의도적)**:
- `tg2_douyin_crawl`: `execution_timeout=timedelta(hours=3)`, `retries=0`. 1-2시간짜리 외부 의존 task라 자동 재시도가 더 위험 (Hyperbrowser 비용 + 차단 위험). progress.json + skipExisting=true 덕에 수동 재시도가 안전
- 부분 실패 즉시 fail: tg2의 종료 코드가 0이 아니면 그대로 fail. tg3/tg4는 `trigger_rule='all_success'`(기본값). 사용자 결정대로

## D. Airflow Variable / Connection

| 이름 | 타입 | 비고 |
|---|---|---|
| `cn_social_hyperbrowser_api_key` | Variable | Hyperbrowser SDK |
| `cn_social_oxylabs_username` | Variable | `customer-prcs_data1_LpjIC-cc-cn` (CN geo) |
| `cn_social_oxylabs_password` | Variable | `Prcsdata_1234` |
| `cn_social_s3_presigned_api_key` | Variable | aviyup1kyk Lambda 키 (현재 .env의 S3_API_KEY) |
| `slack_cn_social_crawler` | Variable | 채널 ID — 기존 `slack_chn_trend_crawler` 명명 패턴 |
| `fnf_slack_bot_token` | Variable | 재사용 |

`docker-compose.yaml`에 line 17-24 패턴으로 `AIRFLOW_VAR_*` 추가.

## E. Slack 요약 메시지 포맷

```
*<CN Social Listening Weekly Batch 완료 알람>*
📅 기준일: 2026-04-28 ~ 2026-05-04 (CST)

*🐦 도우인 크롤링 결과:*
  • 대상 계정: 125개 (sec_uid 누락 스킵: 3)
  • 성공: 118개 / 실패: 4개
  • 수집 게시물: 2,543건
  • 다운로드 영상: 1,820개

*☁️ S3 업로드 (svc-fnf-cn-mkt-s3):*
  • 프로필 parquet: 118건
  • 게시물 parquet: 2,543건
  • 프로필 이미지: 118개
  • 게시물 썸네일: 2,543개

⏱️ 소요: 1h 47m
💰 Hyperbrowser 추정: ~$18
```

XCom으로 메타데이터만 흘리고 (parquet 바이트나 이미지 X), `crawlers/douyin-weekly-v5.js`가 만드는 `output/<폴더>/summary.json` 파싱해서 카운트 채움.

## F. 기존 코드 재사용 매트릭스

| 새로 짜는 것 | 기존 재사용 |
|---|---|
| DAG 골격 | `dags/call_chn_trend_weekly_batch.py:138-196` |
| Slack callback | `dags/utils/slack_alert.py` (`SlackAlert`) |
| Slack 요약 발송 | `dags/utils/slack_connector.py` (`SlackConnector.post_message`) |
| KST 헬퍼 | `dags/constants/chn_trend_constants.py:14-19` 패턴 복사 |
| 한자 숫자 파서 | `cn-social-listening/runners/run_xhs_weekly_local.py:69-82` `parse_chinese_number` |
| S3 helper 구조 | `dags/utils/s3_presigned_helper.py` 참고 (단, **새 helper로 별도 작성** — 버킷·엔드포인트가 다르므로 기존 것 파라미터화 X) |

## G. 검증 절차 (로컬 docker-compose만)

1. `cn_social/data/douyin-accounts.json`을 **2계정짜리 임시 파일**로 교체 (125개 풀 런은 1-2시간)
2. `docker-compose.yaml` `environment:` 블록에 `AIRFLOW_VAR_CN_SOCIAL_*` 추가 (line 17-24 패턴 그대로)
3. 로컬 `.env`에 실제 키 설정, 커밋 안 함
4. `docker compose down -v && docker compose build && docker compose up -d`
5. UI에서 **`node --version`, `npm --version` 확인용 더미 BashOperator** 한 번 돌려보기 (Dockerfile 빌드 검증)
6. `cn_social_s3_test_prefix` Variable로 dev 격리 (`dev/douyin/...`에 쓰게)
7. 개인 Slack 채널 ID로 `slack_cn_social_crawler` 세팅
8. UI에서 DAG 수동 트리거, conf로 `{"date_start":"2026-04-28","date_end":"2026-05-04"}` 오버라이드해 임의 주차 테스트
9. 통과 시 2계정 → 5계정 → 전체로 확장
10. XCom 페이로드 점검 — parquet/이미지 바이트가 새지 않는지

## H. Critical Files

**수정**
- `fnf-dt-data-de-pipeline/Dockerfile` — Node.js 20 + npm ci 추가 (로컬 dev용)
- `fnf-dt-data-de-pipeline/requirements.txt` — pyarrow 추가
- `fnf-dt-data-de-pipeline/docker-compose.yaml` — `AIRFLOW_VAR_CN_SOCIAL_*` 항목 추가

**손대지 않음 (운영 배포 보류)**
- `fnf-dt-data-de-pipeline/bitbucket-pipelines.yml`

**신규**
- `fnf-dt-data-de-pipeline/dags/call_cn_social_listening_weekly_batch.py`
- `fnf-dt-data-de-pipeline/dags/constants/cn_social_constants.py`
- `fnf-dt-data-de-pipeline/dags/tasks/cn_social/__init__.py`
- `fnf-dt-data-de-pipeline/dags/tasks/cn_social/tg1_prepare_tasks.py`
- `fnf-dt-data-de-pipeline/dags/tasks/cn_social/tg2_douyin_crawl_tasks.py`
- `fnf-dt-data-de-pipeline/dags/tasks/cn_social/tg3_upload_account_tasks.py`
- `fnf-dt-data-de-pipeline/dags/tasks/cn_social/tg4_upload_post_tasks.py`
- `fnf-dt-data-de-pipeline/dags/tasks/cn_social/tg5_cleanup_tasks.py`
- `fnf-dt-data-de-pipeline/dags/utils/cn_mkt_s3_presigned_helper.py`
- `fnf-dt-data-de-pipeline/cn_social/crawlers/douyin-weekly-v5.js` (env-var화 사본)
- `fnf-dt-data-de-pipeline/cn_social/data/douyin-accounts.json` (복사)
- `fnf-dt-data-de-pipeline/cn_social/data/douyin-secuid-map.json` (복사)
- `fnf-dt-data-de-pipeline/cn_social/package.json` (복사)
- `fnf-dt-data-de-pipeline/cn_social/run.sh` (신규 entrypoint)

**참조 (수정 안 함)**
- `fnf-dt-data-de-pipeline/dags/call_chn_trend_weekly_batch.py` — 골격 템플릿
- `fnf-dt-data-de-pipeline/dags/utils/slack_alert.py` / `slack_connector.py`
- `cn-social-listening/crawlers/douyin-weekly-v5.js` (원본, 사용자 지시대로 그대로 유지)
- `cn-social-listening/uploaders/s3_upload_douyin_*.py` (원본 그대로, 로직만 task 함수로 이식)
