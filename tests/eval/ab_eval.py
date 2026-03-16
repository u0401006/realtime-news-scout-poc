"""SkillEvo A/B 評估腳本框架。

對照 tasks.md §5.3：每週 A/B（舊版 vs 新版）與自動回滾門檻。

指標體系
========
1. 命中率 (Hit Rate)      — 模型推薦 top-k 中，編輯實際採用的比例
2. NDCG@k                 — 排序品質，衡量高相關稿件是否排在前面
3. 誤報率 (False Positive) — 模型推薦但編輯拒絕的比例
4. 漏稿率 (Miss Rate)      — 編輯採用但模型未推薦的比例（輔助指標）

使用方式
========
    python -m tests.eval.ab_eval \\
        --baseline ranking/snapshots/v1.jsonl \\
        --candidate ranking/snapshots/v2.jsonl \\
        --ground-truth editor-console/decisions.jsonl \\
        --top-k 10 \\
        --output docs/weekly_ab_report.md
"""

from __future__ import annotations

import argparse
import json
import math
import sys
from dataclasses import dataclass, field
from pathlib import Path
from typing import Optional


# ---------------------------------------------------------------------------
# 資料結構
# ---------------------------------------------------------------------------

@dataclass
class RankedItem:
    """單一推薦稿件。"""
    article_id: str
    score: float
    rank: int


@dataclass
class EditorDecision:
    """編輯決策 ground truth。"""
    article_id: str
    adopted: bool            # 是否被編輯採用
    relevance: int = 1       # 相關度等級（0=不相關, 1=相關, 2=高度相關）
    reason: str = ""


@dataclass
class ABResult:
    """A/B 評估結果。"""
    variant: str             # "baseline" 或 "candidate"
    hit_rate: float          # 0.0 ~ 1.0
    ndcg_at_k: float         # 0.0 ~ 1.0
    false_positive_rate: float  # 0.0 ~ 1.0
    miss_rate: float         # 0.0 ~ 1.0
    total_recommended: int
    total_adopted: int
    total_ground_truth: int
    top_k: int


@dataclass
class GateVerdict:
    """升級/回滾門檻判定。"""
    action: str              # "UPGRADE" | "ROLLBACK" | "HOLD"
    reasons: list[str] = field(default_factory=list)
    baseline: Optional[ABResult] = None
    candidate: Optional[ABResult] = None


# ---------------------------------------------------------------------------
# 指標計算
# ---------------------------------------------------------------------------

def _dcg(relevances: list[int], k: int) -> float:
    """Discounted Cumulative Gain @ k。"""
    dcg = 0.0
    for i, rel in enumerate(relevances[:k]):
        dcg += (2 ** rel - 1) / math.log2(i + 2)  # i+2 因 log2(1)=0
    return dcg


def compute_ndcg(
    ranked_ids: list[str],
    ground_truth: dict[str, int],
    k: int,
) -> float:
    """計算 NDCG@k。

    Args:
        ranked_ids: 模型排序後的 article_id 列表（由高到低）
        ground_truth: {article_id: relevance} 映射
        k: 截斷位置
    """
    # 實際 DCG
    rels = [ground_truth.get(aid, 0) for aid in ranked_ids[:k]]
    actual_dcg = _dcg(rels, k)

    # 理想 DCG（所有 ground truth 相關度降序排列）
    ideal_rels = sorted(ground_truth.values(), reverse=True)
    ideal_dcg = _dcg(ideal_rels, k)

    if ideal_dcg == 0:
        return 0.0
    return actual_dcg / ideal_dcg


def compute_hit_rate(
    recommended_ids: set[str],
    adopted_ids: set[str],
) -> float:
    """命中率 = |推薦 ∩ 採用| / |推薦|。"""
    if not recommended_ids:
        return 0.0
    return len(recommended_ids & adopted_ids) / len(recommended_ids)


def compute_false_positive_rate(
    recommended_ids: set[str],
    adopted_ids: set[str],
) -> float:
    """誤報率 = |推薦 − 採用| / |推薦|。"""
    if not recommended_ids:
        return 0.0
    return len(recommended_ids - adopted_ids) / len(recommended_ids)


def compute_miss_rate(
    recommended_ids: set[str],
    adopted_ids: set[str],
) -> float:
    """漏稿率 = |採用 − 推薦| / |採用|。"""
    if not adopted_ids:
        return 0.0
    return len(adopted_ids - recommended_ids) / len(adopted_ids)


# ---------------------------------------------------------------------------
# 資料載入
# ---------------------------------------------------------------------------

def load_ranked_items(path: Path) -> list[RankedItem]:
    """從 JSONL 載入模型推薦列表。

    每行格式：{"article_id": "...", "score": 0.85, "rank": 1}
    """
    items = []
    with open(path, encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            obj = json.loads(line)
            items.append(RankedItem(
                article_id=obj["article_id"],
                score=float(obj.get("score", 0.0)),
                rank=int(obj.get("rank", len(items) + 1)),
            ))
    items.sort(key=lambda x: x.rank)
    return items


def load_ground_truth(path: Path) -> list[EditorDecision]:
    """從 JSONL 載入編輯決策。

    每行格式：{"article_id": "...", "adopted": true, "relevance": 2, "reason": "..."}
    """
    decisions = []
    with open(path, encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            obj = json.loads(line)
            decisions.append(EditorDecision(
                article_id=obj["article_id"],
                adopted=bool(obj.get("adopted", False)),
                relevance=int(obj.get("relevance", 1 if obj.get("adopted") else 0)),
                reason=obj.get("reason", ""),
            ))
    return decisions


# ---------------------------------------------------------------------------
# 評估流程
# ---------------------------------------------------------------------------

def evaluate_variant(
    variant_name: str,
    ranked_items: list[RankedItem],
    decisions: list[EditorDecision],
    top_k: int,
) -> ABResult:
    """對單一 variant 計算所有指標。"""
    # 建立 ground truth 映射
    gt_relevance = {d.article_id: d.relevance for d in decisions}
    adopted_ids = {d.article_id for d in decisions if d.adopted}

    # top-k 推薦清單
    top_k_items = ranked_items[:top_k]
    recommended_ids = {item.article_id for item in top_k_items}
    ranked_ids = [item.article_id for item in ranked_items]

    return ABResult(
        variant=variant_name,
        hit_rate=compute_hit_rate(recommended_ids, adopted_ids),
        ndcg_at_k=compute_ndcg(ranked_ids, gt_relevance, top_k),
        false_positive_rate=compute_false_positive_rate(recommended_ids, adopted_ids),
        miss_rate=compute_miss_rate(recommended_ids, adopted_ids),
        total_recommended=len(recommended_ids),
        total_adopted=len(adopted_ids),
        total_ground_truth=len(decisions),
        top_k=top_k,
    )


# ---------------------------------------------------------------------------
# 門檻判定（對應 gate_thresholds.yaml）
# ---------------------------------------------------------------------------

# 預設門檻常數（可由外部 YAML 覆蓋）
DEFAULT_THRESHOLDS = {
    "upgrade": {
        "hit_rate_delta_min": 0.02,          # candidate 命中率需比 baseline 高 ≥ 2%
        "ndcg_delta_min": 0.01,              # NDCG 需提升 ≥ 1%
        "false_positive_rate_max": 0.30,     # 誤報率不得超過 30%
        "miss_rate_max": 0.20,               # 漏稿率不得超過 20%
    },
    "rollback": {
        "hit_rate_delta_drop": -0.05,        # 命中率下降超過 5% 觸發回滾
        "ndcg_delta_drop": -0.03,            # NDCG 下降超過 3% 觸發回滾
        "false_positive_rate_critical": 0.50, # 誤報率超過 50% 立即回滾
    },
}


def judge_gate(
    baseline: ABResult,
    candidate: ABResult,
    thresholds: Optional[dict] = None,
) -> GateVerdict:
    """根據門檻判定升級、回滾或維持。

    判定邏輯優先順序：
    1. 先檢查 ROLLBACK 條件（任一觸發即回滾）
    2. 再檢查 UPGRADE 條件（全部滿足才升級）
    3. 其餘 → HOLD（維持現狀，繼續觀察）
    """
    t = thresholds or DEFAULT_THRESHOLDS
    reasons: list[str] = []

    # --- 差量計算 ---
    hit_delta = candidate.hit_rate - baseline.hit_rate
    ndcg_delta = candidate.ndcg_at_k - baseline.ndcg_at_k

    # === ROLLBACK 檢查 ===
    rollback_triggered = False

    if hit_delta <= t["rollback"]["hit_rate_delta_drop"]:
        reasons.append(
            f"🔴 命中率大幅下降：{hit_delta:+.2%}"
            f"（門檻 {t['rollback']['hit_rate_delta_drop']:+.2%}）"
        )
        rollback_triggered = True

    if ndcg_delta <= t["rollback"]["ndcg_delta_drop"]:
        reasons.append(
            f"🔴 NDCG 大幅下降：{ndcg_delta:+.2%}"
            f"（門檻 {t['rollback']['ndcg_delta_drop']:+.2%}）"
        )
        rollback_triggered = True

    if candidate.false_positive_rate >= t["rollback"]["false_positive_rate_critical"]:
        reasons.append(
            f"🔴 誤報率過高：{candidate.false_positive_rate:.2%}"
            f"（臨界值 {t['rollback']['false_positive_rate_critical']:.2%}）"
        )
        rollback_triggered = True

    if rollback_triggered:
        return GateVerdict(
            action="ROLLBACK",
            reasons=reasons,
            baseline=baseline,
            candidate=candidate,
        )

    # === UPGRADE 檢查 ===
    upgrade_ok = True

    if hit_delta >= t["upgrade"]["hit_rate_delta_min"]:
        reasons.append(f"✅ 命中率提升：{hit_delta:+.2%}")
    else:
        reasons.append(f"⚠️ 命中率提升不足：{hit_delta:+.2%}")
        upgrade_ok = False

    if ndcg_delta >= t["upgrade"]["ndcg_delta_min"]:
        reasons.append(f"✅ NDCG 提升：{ndcg_delta:+.2%}")
    else:
        reasons.append(f"⚠️ NDCG 提升不足：{ndcg_delta:+.2%}")
        upgrade_ok = False

    if candidate.false_positive_rate <= t["upgrade"]["false_positive_rate_max"]:
        reasons.append(f"✅ 誤報率可控：{candidate.false_positive_rate:.2%}")
    else:
        reasons.append(
            f"⚠️ 誤報率偏高：{candidate.false_positive_rate:.2%}"
            f"（需 ≤ {t['upgrade']['false_positive_rate_max']:.2%}）"
        )
        upgrade_ok = False

    if candidate.miss_rate <= t["upgrade"]["miss_rate_max"]:
        reasons.append(f"✅ 漏稿率可控：{candidate.miss_rate:.2%}")
    else:
        reasons.append(
            f"⚠️ 漏稿率偏高：{candidate.miss_rate:.2%}"
            f"（需 ≤ {t['upgrade']['miss_rate_max']:.2%}）"
        )
        upgrade_ok = False

    if upgrade_ok:
        return GateVerdict(
            action="UPGRADE",
            reasons=reasons,
            baseline=baseline,
            candidate=candidate,
        )

    return GateVerdict(
        action="HOLD",
        reasons=reasons,
        baseline=baseline,
        candidate=candidate,
    )


# ---------------------------------------------------------------------------
# 報告輸出
# ---------------------------------------------------------------------------

def format_report(
    baseline: ABResult,
    candidate: ABResult,
    verdict: GateVerdict,
    week_label: str = "",
) -> str:
    """產生 Markdown 格式的每週 A/B 評估報告。"""
    lines = [
        f"# SkillEvo 每週 A/B 評估報告",
        f"",
        f"**週次：** {week_label or 'N/A'}",
        f"**評估日期：** 自動產生",
        f"**Top-k：** {baseline.top_k}",
        f"",
        f"---",
        f"",
        f"## 一、指標對比",
        f"",
        f"| 指標 | Baseline | Candidate | 差量 | 判定 |",
        f"|------|----------|-----------|------|------|",
    ]

    def _delta_cell(b: float, c: float, higher_better: bool = True) -> tuple[str, str]:
        d = c - b
        sign = "+" if d > 0 else ""
        if higher_better:
            icon = "✅" if d > 0 else ("🔴" if d < -0.03 else "➖")
        else:
            icon = "✅" if d < 0 else ("🔴" if d > 0.05 else "➖")
        return f"{sign}{d:.2%}", icon

    for label, bv, cv, hb in [
        ("命中率 (Hit Rate)", baseline.hit_rate, candidate.hit_rate, True),
        ("NDCG@k", baseline.ndcg_at_k, candidate.ndcg_at_k, True),
        ("誤報率 (FP Rate)", baseline.false_positive_rate, candidate.false_positive_rate, False),
        ("漏稿率 (Miss Rate)", baseline.miss_rate, candidate.miss_rate, False),
    ]:
        delta_str, icon = _delta_cell(bv, cv, hb)
        lines.append(
            f"| {label} | {bv:.2%} | {cv:.2%} | {delta_str} | {icon} |"
        )

    lines += [
        f"",
        f"## 二、樣本統計",
        f"",
        f"| 項目 | Baseline | Candidate |",
        f"|------|----------|-----------|",
        f"| 推薦數 | {baseline.total_recommended} | {candidate.total_recommended} |",
        f"| 編輯採用數 | {baseline.total_adopted} | {candidate.total_adopted} |",
        f"| Ground Truth 總數 | {baseline.total_ground_truth} | {candidate.total_ground_truth} |",
        f"",
        f"## 三、門檻判定",
        f"",
        f"### 判定結果：**{verdict.action}**",
        f"",
    ]

    for r in verdict.reasons:
        lines.append(f"- {r}")

    lines += [
        f"",
        f"## 四、門檻參數（當前設定）",
        f"",
        f"### 升級條件（全部滿足）",
        f"",
        f"| 參數 | 門檻值 |",
        f"|------|--------|",
        f"| 命中率提升 ≥ | {DEFAULT_THRESHOLDS['upgrade']['hit_rate_delta_min']:.2%} |",
        f"| NDCG 提升 ≥ | {DEFAULT_THRESHOLDS['upgrade']['ndcg_delta_min']:.2%} |",
        f"| 誤報率 ≤ | {DEFAULT_THRESHOLDS['upgrade']['false_positive_rate_max']:.2%} |",
        f"| 漏稿率 ≤ | {DEFAULT_THRESHOLDS['upgrade']['miss_rate_max']:.2%} |",
        f"",
        f"### 回滾條件（任一觸發）",
        f"",
        f"| 參數 | 門檻值 |",
        f"|------|--------|",
        f"| 命中率下降 ≥ | {abs(DEFAULT_THRESHOLDS['rollback']['hit_rate_delta_drop']):.2%} |",
        f"| NDCG 下降 ≥ | {abs(DEFAULT_THRESHOLDS['rollback']['ndcg_delta_drop']):.2%} |",
        f"| 誤報率 ≥ | {DEFAULT_THRESHOLDS['rollback']['false_positive_rate_critical']:.2%} |",
        f"",
        f"---",
        f"",
        f"## 五、行動建議",
        f"",
    ]

    if verdict.action == "UPGRADE":
        lines.append("1. 將 candidate 模型版本升級為 production baseline")
        lines.append("2. 存檔本次 baseline snapshot 至 `ranking/snapshots/archive/`")
        lines.append("3. 更新 `ranking/config.yaml` 中的 `active_model_version`")
    elif verdict.action == "ROLLBACK":
        lines.append("1. ⚠️ **立即回滾**：將 production 恢復至 baseline 版本")
        lines.append("2. 保留 candidate snapshot 供 post-mortem 分析")
        lines.append("3. 在 `docs/postmortem/` 建立回滾事件記錄")
    else:
        lines.append("1. 維持現有 baseline 不變")
        lines.append("2. 繼續收集下一週資料後重新評估")
        lines.append("3. 考慮調整 candidate 模型參數後重跑")

    lines += [
        f"",
        f"---",
        f"*本報告由 `tests/eval/ab_eval.py` 自動產生*",
    ]

    return "\n".join(lines)


# ---------------------------------------------------------------------------
# CLI 入口
# ---------------------------------------------------------------------------

def main(argv: list[str] | None = None) -> None:
    parser = argparse.ArgumentParser(
        description="SkillEvo A/B 評估：比較 baseline vs candidate 排序品質",
    )
    parser.add_argument("--baseline", required=True, type=Path,
                        help="Baseline 排序結果 JSONL")
    parser.add_argument("--candidate", required=True, type=Path,
                        help="Candidate 排序結果 JSONL")
    parser.add_argument("--ground-truth", required=True, type=Path,
                        help="編輯決策 ground truth JSONL")
    parser.add_argument("--top-k", type=int, default=10,
                        help="Top-k 截斷（預設 10）")
    parser.add_argument("--week", type=str, default="",
                        help="週次標記（如 W12-2026）")
    parser.add_argument("--output", type=Path, default=None,
                        help="輸出報告路徑（預設 stdout）")
    args = parser.parse_args(argv)

    # 載入資料
    baseline_items = load_ranked_items(args.baseline)
    candidate_items = load_ranked_items(args.candidate)
    decisions = load_ground_truth(args.ground_truth)

    # 評估
    baseline_result = evaluate_variant("baseline", baseline_items, decisions, args.top_k)
    candidate_result = evaluate_variant("candidate", candidate_items, decisions, args.top_k)

    # 門檻判定
    verdict = judge_gate(baseline_result, candidate_result)

    # 產生報告
    report = format_report(baseline_result, candidate_result, verdict, args.week)

    if args.output:
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(report, encoding="utf-8")
        print(f"✅ 報告已寫入 {args.output}")
    else:
        print(report)

    # 回傳碼：ROLLBACK=2, HOLD=1, UPGRADE=0
    if verdict.action == "ROLLBACK":
        sys.exit(2)
    elif verdict.action == "HOLD":
        sys.exit(1)
    else:
        sys.exit(0)


if __name__ == "__main__":
    main()
