#!/usr/bin/env python3
"""Smoke test for V2 Scorer.

Runs the V2Scorer on current CNA sitemap data.
"""

import argparse
import json
import sys
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

# Add project root to sys.path
_PROJECT_ROOT = Path(__file__).resolve().parent.parent.parent
sys.path.insert(0, str(_PROJECT_ROOT))

from ingestion.adapters.cna_article import get_article
from ingestion.adapters.cna_sitemap import fetch_sitemap, filter_by_window
from ranking.model.v2_scorer import V2Scorer

def main():
    parser = argparse.ArgumentParser(description="V2 Scorer Smoke Test")
    parser.add_argument("--hours", type=int, default=1, help="Hours back to check")
    parser.add_argument("--output", default="output/smoke_v2_result.jsonl", help="Output path")
    args = parser.parse_args()

    # Time window
    tz = timezone(timedelta(hours=8))
    now = datetime.now(tz)
    start_dt = now - timedelta(hours=args.hours)
    end_dt = now

    print(f"⏱  Time window: {start_dt.isoformat()} -> {end_dt.isoformat()}")

    # Initialize Scorer
    # Note: Using default paths, might need adjustment if weights/entities move
    scorer = V2Scorer(
        firebase_project_id="medialab-356306",
        # gtrend_csv="google_trend_12hr.csv" # Skip if not found
    )

    # Step 1: Fetch sitemap
    print("📡 Fetching CNA sitemap...")
    all_entries = fetch_sitemap()
    windowed = filter_by_window(all_entries, start_dt, end_dt)
    print(f"   Items in window: {len(windowed)}")

    if not windowed:
        print("⚠️ No items in window.")
        return

    # Step 2: Process items
    records = []
    for i, entry in enumerate(windowed, 1):
        print(f"   [{i}/{len(windowed)}] {entry.pid} {entry.title[:40]}...")
        
        # Get article body for better scoring
        article = get_article(entry.pid, entry.url)
        body = article.full_text if article else ""

        # V2 Scoring
        result = scorer.score(
            title=entry.title,
            summary_text=body,
            topic_tags=entry.keywords
        )

        record = {
            "pid": entry.pid,
            "title": entry.title,
            "score": result.total_score,
            "eligible": result.headline_eligible,
            "threshold": result.effective_threshold,
            "adjustment": result.threshold_adjustment,
            "tier": str(result.content_tier.value) if hasattr(result.content_tier, 'value') else str(result.content_tier),
            "rules": result.matched_rules,
            "reason": result.headline_reason,
            "ip_matches": result.ip_strict_matches,
            "gtrend_boost": result.gtrend_boost,
            "economic_boost": result.economic_boost,
            "firebase_boost": result.firebase_boost,
        }
        records.append(record)
        time.sleep(0.1)

    # Step 3: Save and Summary
    output_path = Path(args.output)
    output_path.parent.mkdir(parents=True, exist_ok=True)
    with open(output_path, "w", encoding="utf-8") as f:
        for rec in records:
            f.write(json.dumps(rec, ensure_ascii=False) + "\n")

    print(f"\n✅ Results saved to {output_path}")
    print("\n" + "="*80)
    print(f"{'Status':<8} | {'Score':<5} | {'Thres':<5} | {'Title'}")
    print("-" * 80)
    for rec in sorted(records, key=lambda x: x['score'], reverse=True):
        status = "✅ PASS" if rec['eligible'] else "❌ FAIL"
        print(f"{status:<8} | {rec['score']:>5.1f} | {rec['threshold']:>5.1f} | {rec['title'][:50]}")
    print("="*80)

if __name__ == "__main__":
    main()
