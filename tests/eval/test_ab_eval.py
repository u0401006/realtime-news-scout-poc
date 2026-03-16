"""ab_eval.py 單元測試。"""

from __future__ import annotations

import json
import tempfile
from pathlib import Path

import pytest

from tests.eval.ab_eval import (
    ABResult,
    EditorDecision,
    GateVerdict,
    RankedItem,
    compute_false_positive_rate,
    compute_hit_rate,
    compute_miss_rate,
    compute_ndcg,
    evaluate_variant,
    judge_gate,
    DEFAULT_THRESHOLDS,
)


# ---------------------------------------------------------------------------
# compute_hit_rate
# ---------------------------------------------------------------------------

class TestHitRate:
    def test_perfect(self):
        assert compute_hit_rate({"a", "b"}, {"a", "b"}) == 1.0

    def test_half(self):
        assert compute_hit_rate({"a", "b"}, {"a"}) == 0.5

    def test_empty_recommended(self):
        assert compute_hit_rate(set(), {"a"}) == 0.0

    def test_no_overlap(self):
        assert compute_hit_rate({"a"}, {"b"}) == 0.0


# ---------------------------------------------------------------------------
# compute_false_positive_rate
# ---------------------------------------------------------------------------

class TestFalsePositiveRate:
    def test_zero_fp(self):
        assert compute_false_positive_rate({"a", "b"}, {"a", "b"}) == 0.0

    def test_all_fp(self):
        assert compute_false_positive_rate({"a", "b"}, set()) == 1.0

    def test_half_fp(self):
        assert compute_false_positive_rate({"a", "b"}, {"a"}) == 0.5


# ---------------------------------------------------------------------------
# compute_miss_rate
# ---------------------------------------------------------------------------

class TestMissRate:
    def test_zero_miss(self):
        assert compute_miss_rate({"a", "b"}, {"a", "b"}) == 0.0

    def test_all_miss(self):
        assert compute_miss_rate(set(), {"a", "b"}) == 1.0


# ---------------------------------------------------------------------------
# compute_ndcg
# ---------------------------------------------------------------------------

class TestNDCG:
    def test_perfect_order(self):
        gt = {"a": 2, "b": 1, "c": 0}
        ndcg = compute_ndcg(["a", "b", "c"], gt, k=3)
        assert ndcg == pytest.approx(1.0)

    def test_reversed_order(self):
        gt = {"a": 2, "b": 1, "c": 0}
        ndcg = compute_ndcg(["c", "b", "a"], gt, k=3)
        assert ndcg < 1.0

    def test_empty_gt(self):
        ndcg = compute_ndcg(["a", "b"], {}, k=2)
        assert ndcg == 0.0


# ---------------------------------------------------------------------------
# judge_gate — 門檻判定
# ---------------------------------------------------------------------------

def _make_result(
    variant: str,
    hit: float,
    ndcg: float,
    fp: float,
    miss: float,
) -> ABResult:
    return ABResult(
        variant=variant,
        hit_rate=hit,
        ndcg_at_k=ndcg,
        false_positive_rate=fp,
        miss_rate=miss,
        total_recommended=10,
        total_adopted=5,
        total_ground_truth=20,
        top_k=10,
    )


class TestGateVerdict:
    def test_upgrade(self):
        """candidate 全面優於 baseline → UPGRADE。"""
        b = _make_result("baseline", 0.60, 0.70, 0.20, 0.15)
        c = _make_result("candidate", 0.65, 0.75, 0.15, 0.10)
        v = judge_gate(b, c)
        assert v.action == "UPGRADE"

    def test_rollback_hit_rate_drop(self):
        """命中率暴跌 → ROLLBACK。"""
        b = _make_result("baseline", 0.60, 0.70, 0.20, 0.15)
        c = _make_result("candidate", 0.50, 0.70, 0.20, 0.15)
        v = judge_gate(b, c)
        assert v.action == "ROLLBACK"

    def test_rollback_fp_critical(self):
        """誤報率超過臨界值 → ROLLBACK。"""
        b = _make_result("baseline", 0.60, 0.70, 0.20, 0.15)
        c = _make_result("candidate", 0.65, 0.75, 0.55, 0.10)
        v = judge_gate(b, c)
        assert v.action == "ROLLBACK"

    def test_hold_insufficient_improvement(self):
        """小幅提升但不達升級門檻 → HOLD。"""
        b = _make_result("baseline", 0.60, 0.70, 0.20, 0.15)
        c = _make_result("candidate", 0.61, 0.71, 0.20, 0.15)
        v = judge_gate(b, c)
        assert v.action == "HOLD"

    def test_hold_mixed_signals(self):
        """命中率提升但誤報率也升高 → HOLD。"""
        b = _make_result("baseline", 0.60, 0.70, 0.20, 0.15)
        c = _make_result("candidate", 0.65, 0.75, 0.35, 0.10)
        v = judge_gate(b, c)
        assert v.action == "HOLD"


# ---------------------------------------------------------------------------
# evaluate_variant 整合
# ---------------------------------------------------------------------------

def _write_jsonl(path: Path, items: list[dict]) -> None:
    with open(path, "w", encoding="utf-8") as f:
        for item in items:
            f.write(json.dumps(item, ensure_ascii=False) + "\n")


class TestEvaluateVariant:
    def test_basic_flow(self):
        decisions = [
            EditorDecision("a", True, 2),
            EditorDecision("b", True, 1),
            EditorDecision("c", False, 0),
            EditorDecision("d", True, 1),
        ]
        ranked = [
            RankedItem("a", 0.9, 1),
            RankedItem("b", 0.8, 2),
            RankedItem("c", 0.7, 3),
        ]
        result = evaluate_variant("test", ranked, decisions, top_k=3)
        assert result.hit_rate == pytest.approx(2 / 3)  # a, b 命中
        assert result.false_positive_rate == pytest.approx(1 / 3)  # c 誤報
        assert result.miss_rate == pytest.approx(1 / 3)  # d 漏稿
        assert 0.0 <= result.ndcg_at_k <= 1.0
