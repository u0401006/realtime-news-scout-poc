#!/usr/bin/env python3
"""SkillEvo v1 — 從 smoke test 結果回填訓練資料。

讀取 smoke_result.jsonl，依照 score 門檻自動標註：
  - score >= HIGH_THRESHOLD  → positive（smoke_auto）
  - score <= LOW_THRESHOLD   → negative（smoke_auto）
  - 其餘                      → unlabeled

用法：
    python -m training_data.backfill_from_smoke \
        --input output/smoke_result.jsonl \
        --output training_data/samples/seed_20260316.jsonl \
        [--high-threshold 65] [--low-threshold 35]
"""

from __future__ import annotations

import argparse
import json
import sys
from datetime import datetime, timezone
from pathlib import Path

# 讓專案根目錄可 import
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from training_data.schema import Label, TrainingSample  # noqa: E402

HIGH_THRESHOLD = 65  # >= 此分數自動標 positive
LOW_THRESHOLD = 35   # <= 此分數自動標 negative


def smoke_line_to_sample(
    raw: dict[str, object],
    *,
    high: int = HIGH_THRESHOLD,
    low: int = LOW_THRESHOLD,
    now: datetime | None = None,
) -> TrainingSample:
    """將單行 smoke 輸出轉為 TrainingSample。"""
    now = now or datetime.now(tz=timezone.utc)
    score = int(raw.get("score", 0))

    if score >= high:
        label = Label.POSITIVE
    elif score <= low:
        label = Label.NEGATIVE
    else:
        label = Label.UNLABELED

    return TrainingSample(
        pid=str(raw.get("pid", "")),
        url=str(raw.get("url", "")),
        title=str(raw.get("title", "")),
        published_at=raw.get("published_at", now.isoformat()),  # type: ignore[arg-type]
        keywords=raw.get("keywords", []),  # type: ignore[arg-type]
        body_length=int(raw.get("body_length", 0)),
        score=score,
        reason=str(raw.get("reason", "")),
        label=label,
        label_source="smoke_auto",
        labeled_at=now,
    )


def backfill(
    input_path: Path,
    output_path: Path,
    *,
    high: int = HIGH_THRESHOLD,
    low: int = LOW_THRESHOLD,
) -> dict[str, int]:
    """批次轉換 smoke JSONL → training JSONL，回傳統計。"""
    stats: dict[str, int] = {"positive": 0, "negative": 0, "unlabeled": 0, "total": 0}
    now = datetime.now(tz=timezone.utc)

    output_path.parent.mkdir(parents=True, exist_ok=True)

    with input_path.open("r", encoding="utf-8") as fin, \
         output_path.open("w", encoding="utf-8") as fout:
        for line in fin:
            line = line.strip()
            if not line:
                continue
            raw = json.loads(line)
            sample = smoke_line_to_sample(raw, high=high, low=low, now=now)
            fout.write(sample.model_dump_json() + "\n")
            stats[sample.label.value] += 1
            stats["total"] += 1

    return stats


def main() -> None:
    """CLI 入口。"""
    parser = argparse.ArgumentParser(
        description="從 smoke test 結果回填 SkillEvo 訓練資料",
    )
    parser.add_argument(
        "--input", "-i",
        type=Path,
        default=Path("output/smoke_result.jsonl"),
        help="smoke_result.jsonl 路徑",
    )
    parser.add_argument(
        "--output", "-o",
        type=Path,
        default=Path("training_data/samples/seed.jsonl"),
        help="輸出 training JSONL 路徑",
    )
    parser.add_argument("--high-threshold", type=int, default=HIGH_THRESHOLD)
    parser.add_argument("--low-threshold", type=int, default=LOW_THRESHOLD)
    args = parser.parse_args()

    stats = backfill(
        args.input,
        args.output,
        high=args.high_threshold,
        low=args.low_threshold,
    )
    print(f"✅ 回填完成: {stats}")
    print(f"   positive={stats['positive']}, negative={stats['negative']}, "
          f"unlabeled={stats['unlabeled']}, total={stats['total']}")


if __name__ == "__main__":
    main()
