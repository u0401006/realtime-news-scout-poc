#!/usr/bin/env python3
"""Smoke test：CNA 一小時視窗選稿（CNA Agent skill 主判斷版）。

從 CNA Google News sitemap 讀取新聞清單，限制在 --start / --end 時間窗內，
對每篇文章呼叫 getArticle(pid) 補內文，
再透過 headline-selection / check-points skill adapter 進行主判斷，
最後輸出 JSONL 與 summary。

## 計分公式（score: 0–100）

    score = clamp(50 + Σ(triggered check-point weights), 0, 100)

    正向 check-points（命中加分）：
      cp-breaking       +30   突發 / 重大事件
      cp-political      +25   政治顯著性
      cp-economic       +20   經濟影響力
      cp-international  +15   國際關注度

    負向 check-points（命中扣分）：
      cp-category       −25   低優先類別
      cp-completeness   −30   內容缺失 / 過短

    門檻：score >= 50 → selected=True

    若所有 check-points 皆未觸發 → score=50, selected=True, reason 註記 fallback。

用法範例：
    python ingestion/scripts/smoke_cna_window.py \\
        --start 2026-03-16T15:00:00+08:00 \\
        --end   2026-03-16T16:00:00+08:00 \\
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
from ingestion.adapters.headline_selection import evaluate  # noqa: E402


# ---------------------------------------------------------------------------
# Record builder
# ---------------------------------------------------------------------------


def build_record(
    entry: SitemapEntry,
    full_text: str | None,
    selected: bool,
    reason: str,
    score: int,
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
        "score": score,
        "reason": reason,
    }


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------


def main() -> None:  # noqa: C901
    parser = argparse.ArgumentParser(
        description="CNA smoke test：時間窗選稿（skill 主判斷）",
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

    # Step 3: 逐篇補內文 + skill 主判斷
    records: list[dict[str, Any]] = []
    selected_count = 0
    fallback_count = 0

    for i, entry in enumerate(windowed, 1):
        print(
            f"   [{i}/{len(windowed)}] {entry.pid} {entry.title[:30]}…",
            file=sys.stderr,
        )
        article = get_article(entry.pid, entry.url)
        full_text = article.full_text if article else None

        # 透過 headline-selection / check-points adapter 做主判斷
        verdict = evaluate(entry, full_text)

        if verdict.selected:
            selected_count += 1
        if verdict.is_fallback:
            fallback_count += 1

        rec = build_record(
            entry, full_text, verdict.selected, verdict.reason, verdict.score,
        )
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
    scores = [r["score"] for r in records]
    avg_score = sum(scores) / len(scores) if scores else 0

    print("\n" + "=" * 60, file=sys.stderr)
    print("📊 Smoke Test Summary（skill 主判斷）", file=sys.stderr)
    print("=" * 60, file=sys.stderr)
    print(f"   時間窗      ：{start_dt.isoformat()} → {end_dt.isoformat()}", file=sys.stderr)
    print(f"   sitemap 總量：{len(all_entries)}", file=sys.stderr)
    print(f"   窗內稿件    ：{len(windowed)}", file=sys.stderr)
    print(f"   選稿數      ：{selected_count}", file=sys.stderr)
    print(f"   淘汰數      ：{len(windowed) - selected_count}", file=sys.stderr)
    print(f"   fallback 數 ：{fallback_count}", file=sys.stderr)
    print(f"   平均 score  ：{avg_score:.1f}", file=sys.stderr)
    print(f"   score 範圍  ：{min(scores)}–{max(scores)}", file=sys.stderr)
    if output_path:
        print(f"   輸出檔案    ：{output_path}", file=sys.stderr)
    print("=" * 60, file=sys.stderr)

    # 列出選稿清單
    print("\n✅ 選稿清單：", file=sys.stderr)
    for rec in records:
        mark = "✅" if rec["selected"] else "❌"
        print(
            f"   {mark} [{rec['pid']}] score={rec['score']:3d}  "
            f"{rec['title'][:40]}  → {rec['reason'][:60]}",
            file=sys.stderr,
        )


if __name__ == "__main__":
    main()
