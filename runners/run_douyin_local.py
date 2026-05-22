"""도우인 주간 데이터 로컬 backfill runner.

운영 Airflow 배치가 실패/누락한 계정을 로컬에서 채워넣을 때 사용.
원본 crawlers/douyin-weekly-v5.js 는 건드리지 않고, 임시 사본을 패치해서 실행한다.

--week 의미는 운영 Airflow 배치와 동일:
    "운영 배치가 트리거되는 월요일 날짜" — 데이터는 그 직전 한 주(월~일).

사용법:
    python runners/run_douyin_local.py --week 0518
        → 운영 배치가 5/18 (월) 트리거할 때 처리하는 데이터 (5/11~5/17)
        → output/_douyin_local_260511/ 에 저장 (데이터 시작일 라벨)

    python runners/run_douyin_local.py --week 0518 --no-videos
        → 영상 다운로드 끄고 메타데이터만

    python runners/run_douyin_local.py --start 2026-05-11 --end 2026-05-17
        → 윈도우 직접 지정 (--week 없이)

폴더명 컨벤션:
    데이터 시작일(월) 기준의 6자리(YYMMDD) — 운영 배치 폴더 `douyin-weekly-260511-v5` 와 동일
    → uploaders 의 parse_date_from_dirname 이 그대로 정확한 partition 추출

동작:
    - data/douyin-accounts.json 의 모든 계정 처리
    - progress.json 기반 skipExisting=true → 같은 폴더에 재실행 시
      이미 완료된 계정은 자동 skip → 실패한 것만 재시도
    - S3 업로드는 별도. 완료 후 사용자가 직접:
        python uploaders/s3_upload_douyin_account.py output/_douyin_local_260511
        python uploaders/s3_upload_douyin_post.py    output/_douyin_local_260511
"""
import argparse
import json
import os
import re
import subprocess
import sys
from datetime import datetime, timedelta
from pathlib import Path


REPO_ROOT = Path(__file__).resolve().parents[1]
CRAWLER = REPO_ROOT / "crawlers" / "douyin-weekly-v5.js"
ACCOUNTS_FILE = REPO_ROOT / "data" / "douyin-accounts.json"
SECUID_MAP_FILE = REPO_ROOT / "data" / "douyin-secuid-map.json"


def parse_args():
    parser = argparse.ArgumentParser(description="도우인 주간 로컬 backfill")
    parser.add_argument(
        "--week",
        help="운영 배치 트리거 MMDD (월요일). 데이터는 그 직전 주 월~일. "
             "예: 0518 → 5/11~5/17",
    )
    parser.add_argument("--start", help="수집 시작일 yyyy-mm-dd (--week 대신)")
    parser.add_argument("--end", help="수집 종료일 yyyy-mm-dd (--week 대신)")
    parser.add_argument("--no-videos", action="store_true", help="영상 다운로드 끔 (메타데이터만)")
    parser.add_argument("--year", default="2026", help="--week 사용 시 연도 (기본 2026)")
    args = parser.parse_args()

    if not args.week and not (args.start and args.end):
        parser.error("--week MMDD 또는 (--start + --end) 중 하나는 필수")
    return args


def week_to_dates(week: str, year: str):
    """--week MMDD = 운영 배치 트리거 월요일. 데이터 윈도우는 그 직전 주 월~일.

    예: --week 0518 → trigger=5/18(Mon) → end=5/17(Sun) → start=5/11(Mon)
    """
    trigger = datetime(int(year), int(week[:2]), int(week[2:]))
    end = trigger - timedelta(days=1)       # 직전 일요일
    start = end - timedelta(days=6)         # 직전 월요일
    return start.strftime("%Y-%m-%d"), end.strftime("%Y-%m-%d")


def js_string(p: Path) -> str:
    """JS 문자열 리터럴로 안전하게 변환 (Windows 백슬래시 → forward slash)."""
    return json.dumps(p.as_posix())


def replace_config(content: str, key: str, value: str) -> str:
    pattern = rf'{key}:\s*(?:"[^"]*"|true|false|\d+)'
    replacement = f"{key}: {value}"
    new_content, count = re.subn(pattern, replacement, content, count=1)
    if count != 1:
        raise RuntimeError(f"CONFIG.{key} 패치 실패")
    return new_content


def main():
    args = parse_args()

    # 윈도우 계산
    if args.start and args.end:
        date_start, date_end = args.start, args.end
    else:
        date_start, date_end = week_to_dates(args.week, args.year)

    # 폴더 라벨은 *데이터 시작일* 기준 — 운영 배치 폴더(douyin-weekly-260511-v5) 와 동일 컨벤션.
    # 그래서 uploaders 가 폴더명에서 추출한 partition 이 자동으로 정확.
    m = re.match(r"(\d{4})-(\d{2})-(\d{2})", date_start)
    if not m:
        raise SystemExit(f"date_start 형식 오류 (yyyy-mm-dd 필요): {date_start}")
    yy = m.group(1)[-2:]
    mmdd = m.group(2) + m.group(3)
    folder_label = f"{yy}{mmdd}"   # 260511 형식 (데이터 시작일 = 그 주의 월요일)

    # 출력 폴더 (운영 배치 결과와 구분되는 prefix)
    output_dir = REPO_ROOT / "output" / f"_douyin_local_{folder_label}"
    output_dir.mkdir(parents=True, exist_ok=True)

    # 임시 크롤러 사본 (원본 안 건드림)
    temp_crawler = output_dir / "_runner_crawler.js"
    content = CRAWLER.read_text(encoding="utf-8")

    content = replace_config(content, "dateStart", json.dumps(f"{date_start}T00:00:00+08:00"))
    content = replace_config(content, "dateEnd", json.dumps(f"{date_end}T23:59:59+08:00"))
    content = replace_config(content, "downloadVideos", "false" if args.no_videos else "true")
    content = replace_config(content, "outputDir", js_string(output_dir))
    content = replace_config(content, "accountsFile", js_string(ACCOUNTS_FILE))
    content = replace_config(content, "secuidMapFile", js_string(SECUID_MAP_FILE))

    temp_crawler.write_text(content, encoding="utf-8")

    accounts = json.loads(ACCOUNTS_FILE.read_text(encoding="utf-8"))
    secuid_map = json.loads(SECUID_MAP_FILE.read_text(encoding="utf-8"))
    targetable = sum(1 for a in dict.fromkeys(accounts) if secuid_map.get(a))

    trigger_info = f"  운영 트리거:  --week {args.week} (월요일)\n" if args.week else ""
    print("도우인 로컬 backfill")
    print(trigger_info, end="")
    print(f"  수집 기간:    {date_start} ~ {date_end}  (CST, 월~일)")
    print(f"  폴더 라벨:    {folder_label}  (YYMMDD = 데이터 시작일, 운영 컨벤션)")
    print(f"  대상 계정:    {targetable}개 (sec_uid 보유)")
    print(f"  영상:         {'OFF (메타만)' if args.no_videos else 'ON'}")
    print(f"  출력 폴더:    {output_dir}")
    print(f"  재실행 시:    progress.json 기반 자동 skip → 실패한 것만 재시도")
    print()

    env = os.environ.copy()
    result = subprocess.run(["node", str(temp_crawler)], cwd=REPO_ROOT, env=env)
    if result.returncode != 0:
        print(f"\nnode 종료 코드 {result.returncode}")
        raise SystemExit(result.returncode)

    print()
    print("=" * 60)
    print("로컬 backfill 완료. 업로드는 별도 명령으로:")
    print(f"  python uploaders/s3_upload_douyin_account.py {output_dir}")
    print(f"  python uploaders/s3_upload_douyin_post.py    {output_dir}")
    print("=" * 60)


if __name__ == "__main__":
    main()
