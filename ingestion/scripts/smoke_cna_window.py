#!/usr/bin/env python3
"""Smoke test：CNA 一小時視窗選稿。

從 CNA Google News sitemap 讀取新聞清單，限制在 --start / --end 時間窗內，
對每篇文章呼叫 getArticle(pid) 補內文，並依簡單規則判斷是否選稿，
最後輸出 JSONL 與 summary。

用法範例：
    python ingestion/scripts/smoke_cna_window.py \
        --start 2026-03-16T15:00:00+08:00 \
        --end   2026-03-16T16:00:00+08:00 \
        --output output/smoke_result.jsonl
"""

from __future__ import annotations

import argparse
import json
import sys
import time
from datetime import datetime
from pathlib import Path
from typing import Any

# 讓 imports 能從專案根目錄載入
_PROJECT_ROOT = Path(__file__).resolve().parent.parent.parent
sys.path.insert(0, str(_PROJECT_ROOT))

from ingestion.adapters.cna_article import get_article  # noqa: E402
from ingestion.adapters.cna_sitemap import (  # noqa: E402
    SitemapEntry,
    fetch_sitemap,
    filter_by_window,
)

# ---------------------------------------------------------------------------
# 選稿規則（簡單 v0）
# ---------------------------------------------------------------------------

# 高優先關鍵字（標題或內文含任一即選）
HIGH_PRIORITY_KEYWORDS: list[str] = [
    "總統",
    "行政院",
    "國防",
    "地震",
    "颱風",
    "外交",
    "兩岸",
    "中國",
    "美國",
    "日本",
    "半導體",
    "台積電",
    "AI",
    "人工智慧",
    "選舉",
    "罷免",
    "爆炸",
    "傷亡",
    "疫情",
    "股市",
]

# 低優先（可能不選）的 sitemap keywords
LOW_PRIORITY_CATEGORIES: set[str] = {
    "entertainment",
    "sport",
    "lifestyle",
}


def evaluate_candidate(
    entry: SitemapEntry,
    full_text: str | None,
) -> tuple[bool, str]:
    """依簡單規則判斷是否選稿。

    回傳 (selected, reason)。
    """
    title = entry.title

    # Rule 1: 標題含高優先關鍵字
    for kw in HIGH_PRIORITY_KEYWORDS:
        if kw in title:
            return True, f"標題含高優先關鍵字「{kw}」"

    # Rule 2: 內文含高優先關鍵字
    if full_text:
        for kw in HIGH_PRIORITY_KEYWORDS:
            if kw in full_text:
                return True, f"內文含高優先關鍵字「{kw}」"

    # Rule 3: sitemap keywords 全屬低優先類別 → 不選
    entry_cats = {k.lower() for k in entry.keywords}
    if entry_cats and entry_cats.issubset(LOW_PRIORITY_CATEGORIES):
        return False, f"類別皆為低優先（{', '.join(entry.keywords)}）"

    # Rule 4: 內文太短 → 不選
    if full_text and len(full_text) < 100:
        return False, "內文過短（<100 字）"

    # Rule 5: 無法取得內文
    if not full_text:
        return False, "無法取得內文"

    # 預設：選入（寧多勿漏）
    return True, "預設選入（未觸發排除規則）"


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------


def build_record(
    entry: SitemapEntry,
    full_text: str | None,
    selected: bool,
    reason: str,
) -> dict[str, Any]:
    """組裝單筆 JSONL record。"""
    return {
        "pid": entry.pid,
        "url": entry.url,
        "title": entry.title,
        "published_at": entry.published_at.isoformat(),
        "keywords": entry.keywords,
        "has_body": full_text is not None,
        "body_length": len(full_text) if full_text else 0,
        "selected": selected,
        "reason": reason,
    }


def main() -> None:  # noqa: C901
    parser = argparse.ArgumentParser(
        description="CNA smoke test：時間窗選稿",
    )
    parser.add_argument(
        "--start",
        required=True,
        help="起始時間（ISO 8601，含時區），例如 2026-03-16T15:00:00+08:00",
    )
    parser.add_argument(
        "--end",
        required=True,
        help="結束時間（ISO 8601，含時區），例如 2026-03-16T16:00:00+08:00",
    )
    parser.add_argument(
        "--output",
        default=None,
        help="JSONL 輸出路徑（預設 stdout）",
    )
    parser.add_argument(
        "--delay",
        type=float,
        default=0.5,
        help="每篇文章抓取間隔秒數（預設 0.5）",
    )
    args = parser.parse_args()

    start_dt = datetime.fromisoformat(args.start)
    end_dt = datetime.fromisoformat(args.end)

    print(f"⏱  時間窗：{start_dt.isoformat()} → {end_dt.isoformat()}", file=sys.stderr)

    # Step 1: 抓 sitemap
    print("📡 正在讀取 CNA sitemap …", file=sys.stderr)
    all_entries = fetch_sitemap()
    print(f"   sitemap 共 {len(all_entries)} 筆", file=sys.stderr)

    # Step 2: 過濾時間窗
    windowed = filter_by_window(all_entries, start_dt, end_dt)
    print(f"   時間窗內 {len(windowed)} 筆", file=sys.stderr)

    if not windowed:
        print("⚠️  時間窗內無稿件，結束。", file=sys.stderr)
        sys.exit(0)

    # Step 3: 逐篇補內文 + 選稿
    records: list[dict[str, Any]] = []
    selected_count = 0

    for i, entry in enumerate(windowed, 1):
        print(
            f"   [{i}/{len(windowed)}] {entry.pid} {entry.title[:30]}…",
            file=sys.stderr,
        )
        article = get_article(entry.pid, entry.url)
        full_text = article.full_text if article else None

        selected, reason = evaluate_candidate(entry, full_text)
        if selected:
            selected_count += 1

        rec = build_record(entry, full_text, selected, reason)
        records.append(rec)

        if i < len(windowed):
            time.sleep(args.delay)

    # Step 4: 輸出 JSONL
    out_fh = sys.stdout
    output_path: Path | None = None
    if args.output:
        output_path = Path(args.output)
        output_path.parent.mkdir(parents=True, exist_ok=True)
        out_fh = open(output_path, "w", encoding="utf-8")  # noqa: SIM115

    for rec in records:
        out_fh.write(json.dumps(rec, ensure_ascii=False) + "\n")

    if out_fh is not sys.stdout:
        out_fh.close()

    # Step 5: Summary
    print("\n" + "=" * 60, file=sys.stderr)
    print("📊 Smoke Test Summary", file=sys.stderr)
    print("=" * 60, file=sys.stderr)
    print(f"   時間窗      ：{start_dt.isoformat()} → {end_dt.isoformat()}", file=sys.stderr)
    print(f"   sitemap 總量：{len(all_entries)}", file=sys.stderr)
    print(f"   窗內稿件    ：{len(windowed)}", file=sys.stderr)
    print(f"   選稿數      ：{selected_count}", file=sys.stderr)
    print(f"   淘汰數      ：{len(windowed) - selected_count}", file=sys.stderr)
    if output_path:
        print(f"   輸出檔案    ：{output_path}", file=sys.stderr)
    print("=" * 60, file=sys.stderr)

    # 列出選稿清單
    print("\n✅ 選稿清單：", file=sys.stderr)
    for rec in records:
        mark = "✅" if rec["selected"] else "❌"
        print(
            f"   {mark} [{rec['pid']}] {rec['title'][:40]}  → {rec['reason']}",
            file=sys.stderr,
        )


if __name__ == "__main__":
    main()
