"""Run a local-only Douyin crawler smoke test.

This wrapper does not modify crawlers/douyin-weekly-v5.js. It copies that file
to output/_douyin_local_test/, patches only the temporary copy, and runs the
temporary copy against a small account subset. S3 uploaders are not called.
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
WORK_DIR = REPO_ROOT / "output" / "_douyin_local_test"


def parse_args():
    parser = argparse.ArgumentParser(description="Local-only Douyin crawler smoke test")
    parser.add_argument("--account", help="Specific account name from data/douyin-accounts.json")
    parser.add_argument("--limit", type=int, default=1, help="Number of accounts to test")
    parser.add_argument("--week", default=datetime.now().strftime("%m%d"), help="Week start MMDD")
    parser.add_argument("--start", help="Start date yyyy-mm-dd")
    parser.add_argument("--end", help="End date yyyy-mm-dd")
    parser.add_argument("--download-videos", action="store_true", help="Also download videos")
    return parser.parse_args()


def week_to_dates(week):
    start = datetime(2026, int(week[:2]), int(week[2:]))
    end = start + timedelta(days=6)
    return start.strftime("%Y-%m-%d"), end.strftime("%Y-%m-%d")


def js_string(path):
    return json.dumps(path.as_posix())


def replace_config(content, key, value):
    pattern = rf'{key}:\s*(?:"[^"]*"|true|false|\d+)'
    replacement = f"{key}: {value}"
    content, count = re.subn(pattern, replacement, content, count=1)
    if count != 1:
        raise RuntimeError(f"Could not patch CONFIG.{key}")
    return content


def main():
    args = parse_args()
    date_start, date_end = (args.start, args.end) if args.start and args.end else week_to_dates(args.week)

    accounts = json.loads(ACCOUNTS_FILE.read_text(encoding="utf-8"))
    secuid_map = json.loads(SECUID_MAP_FILE.read_text(encoding="utf-8"))
    candidates = [name for name in dict.fromkeys(accounts) if secuid_map.get(name)]

    if args.account:
        if not secuid_map.get(args.account):
            raise SystemExit(f"Account has no sec_uid mapping: {args.account}")
        selected = [args.account]
    else:
        selected = candidates[: max(args.limit, 1)]

    if not selected:
        raise SystemExit("No testable Douyin accounts found.")

    run_id = datetime.now().strftime("%Y%m%d-%H%M%S")
    run_dir = WORK_DIR / run_id
    output_dir = run_dir / "output"
    run_dir.mkdir(parents=True, exist_ok=True)

    test_accounts_file = run_dir / "douyin-accounts.test.json"
    test_accounts_file.write_text(
        json.dumps(selected, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )

    temp_crawler = run_dir / "douyin-weekly-v5.local-test.js"
    content = CRAWLER.read_text(encoding="utf-8")
    content = replace_config(content, "dateStart", json.dumps(f"{date_start}T00:00:00+08:00"))
    content = replace_config(content, "dateEnd", json.dumps(f"{date_end}T23:59:59+08:00"))
    content = replace_config(content, "downloadVideos", "true" if args.download_videos else "false")
    content = replace_config(content, "outputDir", js_string(output_dir))
    content = replace_config(content, "accountsFile", js_string(test_accounts_file))
    content = replace_config(content, "secuidMapFile", js_string(SECUID_MAP_FILE))
    content = replace_config(content, "maxAccountsPerSession", "1")
    temp_crawler.write_text(content, encoding="utf-8")

    print("Douyin local test")
    print(f"  accounts: {', '.join(selected)}")
    print(f"  period:   {date_start} ~ {date_end}")
    print(f"  output:   {output_dir}")
    print(f"  videos:   {'on' if args.download_videos else 'off'}")
    print()

    env = os.environ.copy()
    result = subprocess.run(["node", str(temp_crawler)], cwd=REPO_ROOT, env=env)
    if result.returncode != 0:
        raise SystemExit(result.returncode)

    print(f"\nLocal test output: {output_dir}")


if __name__ == "__main__":
    main()
