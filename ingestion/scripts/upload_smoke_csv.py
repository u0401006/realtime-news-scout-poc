#!/usr/bin/env python3
"""upload_smoke_csv.py — 將 smoke test JSONL 結果轉為 CSV 並 push 到 GitHub。

規則：
- 同一天的資料：最新一輪放最上方，舊輪次往下堆疊
- 換日（CSV 中最新日期 != 今天）：清空舊資料，從頭開始
- CSV 欄位：window_start, window_end, pid, title, score, threshold, selected, reason,
  news_type(空白,由編輯填), gtrend_boost, economic_boost, ip_matches, tier, url
"""

from __future__ import annotations

import argparse
import csv
import io
import json
import subprocess
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

CSV_FILENAME = "output/daily_smoke.csv"
CSV_HEADER = [
    "window_start",
    "window_end",
    "pid",
    "title",
    "score",
    "threshold",
    "selected",
    "reason",
    "news_type",
    "gtrend_boost",
    "economic_boost",
    "ip_matches",
    "tier",
    "url",
]

TZ = timezone(timedelta(hours=8))


def _today_str() -> str:
    return datetime.now(TZ).strftime("%Y-%m-%d")


def _parse_jsonl(path: Path) -> list[dict]:
    records = []
    with open(path, encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            records.append(json.loads(line))
    return records


def _record_to_row(rec: dict) -> dict:
    content = rec.get("content", {})
    scoring = rec.get("scoring", {})
    classification = rec.get("classification", {})
    metadata = rec.get("metadata", {})

    return {
        "window_start": metadata.get("windowStart", ""),
        "window_end": metadata.get("windowEnd", ""),
        "pid": content.get("pid", ""),
        "title": content.get("title", ""),
        "score": scoring.get("newsValue", 0),
        "threshold": scoring.get("effectiveThreshold", 0),
        "selected": "Y" if scoring.get("selected") else "N",
        "reason": scoring.get("reason", ""),
        "news_type": "",  # 留空給編輯
        "gtrend_boost": scoring.get("gtrendBoost", 0),
        "economic_boost": scoring.get("economicBoost", 0),
        "ip_matches": ", ".join(scoring.get("ipMatches", [])),
        "tier": classification.get("contentTier", ""),
        "url": content.get("url", ""),
    }


def _read_existing_csv(csv_path: Path) -> tuple[list[dict], str | None]:
    """讀取現有 CSV，回傳 (rows, 最新日期 or None)。"""
    if not csv_path.exists():
        return [], None

    rows = []
    latest_date = None
    with open(csv_path, encoding="utf-8") as f:
        reader = csv.DictReader(f)
        for row in reader:
            rows.append(row)
            ws = row.get("window_start", "")
            if ws and len(ws) >= 10:
                d = ws[:10]
                if latest_date is None or d > latest_date:
                    latest_date = d

    return rows, latest_date


def _write_csv(csv_path: Path, rows: list[dict]) -> None:
    csv_path.parent.mkdir(parents=True, exist_ok=True)
    with open(csv_path, "w", encoding="utf-8", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=CSV_HEADER)
        writer.writeheader()
        writer.writerows(rows)


def _git_push(project_root: Path, csv_path: Path) -> None:
    """Add, commit, push."""
    rel = csv_path.relative_to(project_root)
    subprocess.run(["git", "add", str(rel)], cwd=project_root, check=True)

    result = subprocess.run(
        ["git", "diff", "--cached", "--quiet"],
        cwd=project_root,
    )
    if result.returncode == 0:
        print("No changes to commit.", file=sys.stderr)
        return

    now = datetime.now(TZ).strftime("%Y-%m-%d %H:%M")
    subprocess.run(
        ["git", "commit", "-m", f"data: smoke CSV update {now}"],
        cwd=project_root,
        check=True,
    )
    subprocess.run(["git", "push"], cwd=project_root, check=True)
    print("Pushed to GitHub.", file=sys.stderr)


def main() -> None:
    parser = argparse.ArgumentParser(description="Upload smoke JSONL to CSV on GitHub")
    parser.add_argument("--input", required=True, help="JSONL file path")
    parser.add_argument("--no-push", action="store_true", help="Skip git push")
    args = parser.parse_args()

    project_root = Path(__file__).resolve().parent.parent.parent
    jsonl_path = Path(args.input)
    if not jsonl_path.is_absolute():
        jsonl_path = project_root / jsonl_path
    csv_path = project_root / CSV_FILENAME

    # Parse new records
    new_records = _parse_jsonl(jsonl_path)
    if not new_records:
        print("No records in JSONL, skipping.", file=sys.stderr)
        return

    new_rows = [_record_to_row(r) for r in new_records]

    # Determine today
    today = _today_str()

    # Read existing CSV
    existing_rows, latest_date = _read_existing_csv(csv_path)

    # If different day, clear
    if latest_date and latest_date != today:
        print(f"New day ({today} vs {latest_date}), clearing CSV.", file=sys.stderr)
        existing_rows = []

    # Prepend new rows (newest round on top)
    combined = new_rows + existing_rows

    _write_csv(csv_path, combined)
    print(f"Wrote {len(combined)} rows to {csv_path}", file=sys.stderr)

    if not args.no_push:
        _git_push(project_root, csv_path)


if __name__ == "__main__":
    main()
