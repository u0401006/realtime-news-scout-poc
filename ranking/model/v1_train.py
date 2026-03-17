"""V1 Headline Ranking Model — 訓練腳本

讀取 training_data/gold_set.md，解析正/負樣本及特徵關鍵字，
產出 keyword-based scoring 模型參數存於 ranking/model/v1_weights.json。

使用方式：
    python -m ranking.model.v1_train
    python -m ranking.model.v1_train --gold-set training_data/gold_set.md
"""

from __future__ import annotations

import json
import logging
import re
import sys
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

logger = logging.getLogger(__name__)

# ─────────────────────────────────────────────
# 資料結構
# ─────────────────────────────────────────────

@dataclass
class TrainingSample:
    """解析自 gold_set.md 的單一訓練樣本。"""
    pid: str
    title: str
    tags: List[str]
    weight: int  # +3, +2, +1, -1
    category: str = ""  # IP/新奇, 軍事, 運動, 災害, 泛國際
    entities: List[str] = field(default_factory=list)  # 括號內的實體關鍵字


@dataclass
class FeatureRule:
    """單一特徵規則。"""
    name: str
    keywords: List[str]
    weight: float
    category: str


@dataclass
class ModelWeights:
    """V1 模型權重 — keyword-based scoring。"""
    version: str = "v1.0"
    trained_at: str = ""
    sample_count: int = 0
    positive_count: int = 0
    negative_count: int = 0

    # 特徵規則
    feature_rules: List[Dict[str, Any]] = field(default_factory=list)

    # 全域參數
    base_score: float = 50.0
    positive_keyword_boost: float = 15.0
    negative_keyword_penalty: float = -20.0
    timeliness_weight: float = 0.3
    credibility_weight: float = 0.1

    # 門檻
    headline_threshold: float = 72.0
    strong_reject_threshold: float = 45.0

    # 泛國際抑制
    generic_international_penalty: float = -25.0
    generic_international_keywords: List[str] = field(default_factory=list)

    # IP 實體清單 — 從正樣本 entities 提取的「強實體」
    ip_entities: List[str] = field(default_factory=list)
    ip_entity_boost: float = 20.0  # 強實體命中時的額外加分


# ─────────────────────────────────────────────
# 解析 gold_set.md
# ─────────────────────────────────────────────

_SAMPLE_RE = re.compile(
    r"^- `(\d{12})`\s*\|\s*(.+?)\s*\|\s*tags:\s*(.+?)(?:\s*\|\s*entities:\s*(.*))?$"
)
_ENTITY_RE = re.compile(r"\(([^)]+)\)")
_WEIGHT_RE = re.compile(r"###\s*\[([+-]?\d+)\]")
_FEATURE_RE = re.compile(
    r"^\|\s*(.+?)\s*\|\s*(.+?)\s*\|\s*([+-]?\d+\.?\d*)\s*\|$"
)


def parse_gold_set(gold_set_path: str) -> Tuple[List[TrainingSample], List[FeatureRule]]:
    """解析 gold_set.md，回傳 (樣本列表, 特徵規則列表)。

    Args:
        gold_set_path: gold_set.md 檔案路徑。

    Returns:
        Tuple of (samples, feature_rules).
    """
    path = Path(gold_set_path)
    if not path.exists():
        raise FileNotFoundError(f"Gold set not found: {gold_set_path}")

    text = path.read_text(encoding="utf-8")
    lines = text.splitlines()

    samples: List[TrainingSample] = []
    feature_rules: List[FeatureRule] = []
    current_weight: int = 0
    current_category: str = ""
    in_feature_table = False

    for line in lines:
        line = line.strip()

        # 偵測 weight header: ### [+3] IP/IP 新奇 ...
        wm = _WEIGHT_RE.match(line)
        if wm:
            current_weight = int(wm.group(1))
            # 提取 category 描述
            rest = line[wm.end():].strip().lstrip("—").lstrip("-").strip()
            current_category = rest.split("—")[0].strip() if rest else ""
            in_feature_table = False
            continue

        # 偵測特徵摘要表格
        if "特徵類型" in line and "關鍵字模式" in line:
            in_feature_table = True
            continue

        if in_feature_table:
            fm = _FEATURE_RE.match(line)
            if fm:
                feat_name = fm.group(1).strip()
                keywords_str = fm.group(2).strip()
                feat_weight = float(fm.group(3).strip())
                keywords = [k.strip() for k in keywords_str.split(",") if k.strip()]
                feature_rules.append(FeatureRule(
                    name=feat_name,
                    keywords=keywords,
                    weight=feat_weight,
                    category=feat_name,
                ))
            elif line.startswith("|") and "---" in line:
                continue  # table separator
            elif not line.startswith("|"):
                in_feature_table = False
            continue

        # 偵測樣本行
        sm = _SAMPLE_RE.match(line)
        if sm:
            pid = sm.group(1)
            title = sm.group(2).strip()
            tags = [t.strip() for t in sm.group(3).split(",")]
            # 解析 entities 括號標籤
            entities_raw = sm.group(4) or ""
            entities = _ENTITY_RE.findall(entities_raw)
            samples.append(TrainingSample(
                pid=pid,
                title=title,
                tags=tags,
                weight=current_weight,
                category=current_category,
                entities=entities,
            ))

    logger.info(
        "Parsed %d samples (%d positive, %d negative) and %d feature rules",
        len(samples),
        sum(1 for s in samples if s.weight > 0),
        sum(1 for s in samples if s.weight < 0),
        len(feature_rules),
    )
    return samples, feature_rules


# ─────────────────────────────────────────────
# 訓練（特徵工程 + 規則生成）
# ─────────────────────────────────────────────

def _extract_keyword_patterns(samples: List[TrainingSample]) -> Dict[str, List[str]]:
    """從正樣本標題中提取高頻關鍵詞模式。"""
    positive_titles = [s.title for s in samples if s.weight > 0]
    negative_titles = [s.title for s in samples if s.weight < 0]

    # 以 2-4 字組合做 n-gram 抽取
    def ngrams(text: str, ns: List[int] = [2, 3, 4]) -> List[str]:
        result: List[str] = []
        for n in ns:
            for i in range(len(text) - n + 1):
                gram = text[i:i+n]
                if not re.match(r"^[\u4e00-\u9fff]+$", gram):
                    continue
                result.append(gram)
        return result

    pos_ngrams: Dict[str, int] = {}
    for title in positive_titles:
        for gram in ngrams(title):
            pos_ngrams[gram] = pos_ngrams.get(gram, 0) + 1

    neg_ngrams: Dict[str, int] = {}
    for title in negative_titles:
        for gram in ngrams(title):
            neg_ngrams[gram] = neg_ngrams.get(gram, 0) + 1

    # 正向關鍵字：出現 >=2 次且不在負樣本中
    positive_keywords = [
        k for k, v in pos_ngrams.items()
        if v >= 2 and k not in neg_ngrams
    ]

    # 負向關鍵字：只出現在負樣本中
    negative_keywords = [
        k for k, v in neg_ngrams.items()
        if v >= 2 and k not in pos_ngrams
    ]

    return {
        "positive_auto": sorted(positive_keywords),
        "negative_auto": sorted(negative_keywords),
    }


def train(
    gold_set_path: str = "training_data/gold_set.md",
    output_path: str = "ranking/model/v1_weights.json",
) -> ModelWeights:
    """執行 V1 訓練流程。

    Steps:
        1. 解析 gold_set.md
        2. 從正/負樣本提取特徵關鍵字
        3. 結合手動標記的特徵規則
        4. 計算最佳門檻值
        5. 輸出 v1_weights.json

    Args:
        gold_set_path: gold_set.md 路徑。
        output_path: 輸出權重檔路徑。

    Returns:
        訓練完成的 ModelWeights。
    """
    logger.info("=== V1 Training Started ===")
    logger.info("Gold set: %s", gold_set_path)

    # Step 1: 解析
    samples, feature_rules = parse_gold_set(gold_set_path)
    if not samples:
        raise ValueError("No training samples found in gold set")

    positive_samples = [s for s in samples if s.weight > 0]
    negative_samples = [s for s in samples if s.weight < 0]

    # Step 1.5: 提取正樣本 IP 實體
    ip_entities: List[str] = []
    for s in positive_samples:
        for ent in s.entities:
            if ent not in ip_entities:
                ip_entities.append(ent)
    logger.info("Extracted %d IP entities from positive samples: %s", len(ip_entities), ip_entities)

    # Step 2: 自動提取關鍵字
    auto_keywords = _extract_keyword_patterns(samples)
    logger.info(
        "Auto-extracted: %d positive keywords, %d negative keywords",
        len(auto_keywords["positive_auto"]),
        len(auto_keywords["negative_auto"]),
    )

    # Step 3: 建構特徵規則
    rules: List[Dict[str, Any]] = []
    for fr in feature_rules:
        rules.append({
            "name": fr.name,
            "keywords": fr.keywords,
            "weight": fr.weight,
            "category": fr.category,
        })

    # 加入 IP 實體特徵規則（強實體 — 最高加權）
    if ip_entities:
        rules.append({
            "name": "ip_entities",
            "keywords": ip_entities,
            "weight": 4.0,  # 比一般正向規則更高
            "category": "IP實體(自動提取)",
        })

    # 加入自動提取的關鍵字
    if auto_keywords["positive_auto"]:
        rules.append({
            "name": "auto_positive",
            "keywords": auto_keywords["positive_auto"],
            "weight": 2.0,
            "category": "自動提取正向",
        })
    if auto_keywords["negative_auto"]:
        rules.append({
            "name": "auto_negative",
            "keywords": auto_keywords["negative_auto"],
            "weight": -1.5,
            "category": "自動提取負向",
        })

    # Step 4: 計算門檻值 — 模擬 scoring 後取最佳分界
    # 泛國際負面關鍵字
    generic_intl_keywords: List[str] = []
    for fr in feature_rules:
        if fr.weight < 0:
            generic_intl_keywords.extend(fr.keywords)
    generic_intl_keywords.extend(auto_keywords.get("negative_auto", []))

    # 模擬 scoring
    def simulate_score(sample: TrainingSample) -> float:
        score = 50.0  # base
        title = sample.title
        for rule in rules:
            matched = any(kw in title for kw in rule["keywords"])
            if matched:
                score += rule["weight"] * 5.0  # amplify for scoring

        # 泛國際抑制
        if any(kw in title for kw in generic_intl_keywords):
            score -= 25.0

        return max(0.0, min(100.0, score))

    pos_scores = [(s.pid, simulate_score(s)) for s in positive_samples]
    neg_scores = [(s.pid, simulate_score(s)) for s in negative_samples]

    logger.info("--- Positive sample scores ---")
    for pid, score in pos_scores:
        logger.info("  [+] %s: %.1f", pid, score)

    logger.info("--- Negative sample scores ---")
    for pid, score in neg_scores:
        logger.info("  [-] %s: %.1f", pid, score)

    # 找最佳門檻：max(正樣本最低分, 負樣本最高分 + margin)
    min_pos_score = min(s for _, s in pos_scores) if pos_scores else 70.0
    max_neg_score = max(s for _, s in neg_scores) if neg_scores else 40.0
    margin = 5.0
    optimal_threshold = max(max_neg_score + margin, min_pos_score - margin)
    optimal_threshold = round(optimal_threshold, 1)

    logger.info("Min positive score: %.1f", min_pos_score)
    logger.info("Max negative score: %.1f", max_neg_score)
    logger.info("Optimal threshold: %.1f (margin=%.1f)", optimal_threshold, margin)

    # Step 5: 建立 ModelWeights
    now_iso = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    weights = ModelWeights(
        version="v1.1",
        trained_at=now_iso,
        sample_count=len(samples),
        positive_count=len(positive_samples),
        negative_count=len(negative_samples),
        feature_rules=rules,
        base_score=50.0,
        positive_keyword_boost=15.0,
        negative_keyword_penalty=-20.0,
        timeliness_weight=0.3,
        credibility_weight=0.1,
        headline_threshold=optimal_threshold,
        strong_reject_threshold=max(max_neg_score - 5.0, 30.0),
        generic_international_penalty=-25.0,
        generic_international_keywords=list(set(generic_intl_keywords)),
        ip_entities=ip_entities,
        ip_entity_boost=20.0,
    )

    # 輸出
    output = Path(output_path)
    output.parent.mkdir(parents=True, exist_ok=True)

    weights_dict = {
        "version": weights.version,
        "trained_at": weights.trained_at,
        "sample_count": weights.sample_count,
        "positive_count": weights.positive_count,
        "negative_count": weights.negative_count,
        "feature_rules": weights.feature_rules,
        "base_score": weights.base_score,
        "positive_keyword_boost": weights.positive_keyword_boost,
        "negative_keyword_penalty": weights.negative_keyword_penalty,
        "timeliness_weight": weights.timeliness_weight,
        "credibility_weight": weights.credibility_weight,
        "headline_threshold": weights.headline_threshold,
        "strong_reject_threshold": weights.strong_reject_threshold,
        "generic_international_penalty": weights.generic_international_penalty,
        "generic_international_keywords": weights.generic_international_keywords,
        "ip_entities": weights.ip_entities,
        "ip_entity_boost": weights.ip_entity_boost,
    }

    output.write_text(
        json.dumps(weights_dict, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    logger.info("Weights saved to %s", output_path)

    # Step 6: 同步 IP 實體到 headline_selection.py 的 cp-ip 清單
    if ip_entities:
        from ranking.headline_selection import sync_ip_entities
        sync_ip_entities(ip_entities)
        logger.info("Synced %d IP entities to headline_selection cp-ip list", len(ip_entities))

    # 訓練報告
    logger.info("=== V1 Training Complete ===")
    logger.info("  Samples: %d (+%d / -%d)", len(samples), len(positive_samples), len(negative_samples))
    logger.info("  Feature rules: %d", len(rules))
    logger.info("  Headline threshold: %.1f", weights.headline_threshold)
    logger.info("  Strong reject threshold: %.1f", weights.strong_reject_threshold)
    logger.info("  Generic intl keywords: %d", len(weights.generic_international_keywords))

    return weights


# ─────────────────────────────────────────────
# CLI
# ─────────────────────────────────────────────

if __name__ == "__main__":
    import argparse

    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)s %(name)s %(message)s",
    )

    parser = argparse.ArgumentParser(description="V1 Headline Ranking Model Training")
    parser.add_argument(
        "--gold-set",
        default="training_data/gold_set.md",
        help="Path to gold_set.md",
    )
    parser.add_argument(
        "--output",
        default="ranking/model/v1_weights.json",
        help="Output weights JSON path",
    )
    args = parser.parse_args()

    weights = train(gold_set_path=args.gold_set, output_path=args.output)
    print(f"\n✅ Training complete. Threshold: {weights.headline_threshold}")
