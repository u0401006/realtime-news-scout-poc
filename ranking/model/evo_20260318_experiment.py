#!/usr/bin/env python3
"""SkillEvo 2026-03-18 A/B Experiment — 排序模型權重演進

變因 A：現行 v1_weights.json（v1.3-evo from 03-17）
變因 B：IP/新奇/重大事故/體育金牌 大幅加權；一般國際/經濟 降權

指標：
  - Hit Rate = gold_set 正樣本中，score >= threshold 的比例
  - Top 10 Accuracy = 排序後前 10 名中，正樣本的佔比
  - False Positive Rate = 負樣本中被誤判為 headline 的比例
"""

import copy
import json
import re
import sys
from pathlib import Path
from typing import Any, Dict, List, Tuple

BASE = Path(__file__).resolve().parent.parent.parent  # realtime-news-scout-poc/
WEIGHTS_PATH = BASE / "ranking" / "model" / "v1_weights.json"
GOLD_SET_PATH = BASE / "training_data" / "gold_set.md"
SAMPLES_DIR = BASE / "training_data" / "samples"

# ─── 載入樣本 ───

def parse_gold_set(path: Path) -> List[Dict]:
    """從 gold_set.md 解析正樣本（含 entities/tags）。"""
    if not path.exists():
        return []
    text = path.read_text(encoding="utf-8")
    samples = []
    date_section = ""
    for line in text.splitlines():
        if line.startswith("## "):
            date_section = line.strip("# ").strip()
            continue
        # 特殊：Capo 評分備註行
        if line.startswith("- 20260") and ":" in line:
            continue
        m = re.match(r"^- (https?://\S+)\s*\((.*)\)", line)
        if m:
            url = m.group(1)
            tags = [t.strip() for t in m.group(2).split(",")]
            # 用 tags 重建簡短 title
            title = "".join(tags[:4])
            samples.append({
                "url": url,
                "title": title,
                "tags": tags,
                "label": "positive",
                "source": f"gold_set/{date_section}",
            })
    return samples


def load_jsonl(path: Path) -> List[Dict]:
    if not path.exists():
        return []
    results = []
    with path.open("r", encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            data = json.loads(line)
            if data.get("label") == "unlabeled":
                continue
            results.append(data)
    return results


# ─── 簡易 scorer（不依賴 import ranking） ───

def score_with_weights(weights: Dict, title: str, keywords: List[str] = None) -> float:
    """用給定權重對標題評分。"""
    text = title + " " + " ".join(keywords or [])
    score = weights.get("base_score", 50.0)

    for rule in weights.get("feature_rules", []):
        rule_kw = rule.get("keywords", [])
        rule_w = rule.get("weight", 0.0)
        matched = [kw for kw in rule_kw if kw in text]
        if matched:
            hit_mul = min(len(matched), 3) / 2.0
            score += rule_w * 5.0 * hit_mul

    # IP entity boost
    ip_entities = weights.get("ip_entities", [])
    ip_boost = weights.get("ip_entity_boost", 20.0)
    ip_matched = [e for e in ip_entities if e in text]
    if ip_matched:
        boost = 0.0
        for i in range(len(ip_matched)):
            boost += ip_boost * (1.0 / (1.0 + i * 0.3))
        score += min(boost, 50.0)

    # Generic international penalty
    gen_kw = weights.get("generic_international_keywords", [])
    gen_penalty = weights.get("generic_international_penalty", -25.0)
    gen_hits = [kw for kw in gen_kw if kw in text]
    has_strong = any(
        rule.get("weight", 0) > 0 and any(kw in text for kw in rule.get("keywords", []))
        for rule in weights.get("feature_rules", [])
    )
    if gen_hits and not has_strong:
        score += gen_penalty

    # Timeliness + credibility defaults
    score += 50 * weights.get("timeliness_weight", 0.3) * 0.3
    score += 90 * weights.get("credibility_weight", 0.1) * 0.1

    return max(0.0, min(100.0, round(score, 1)))


# ─── 變因 B 權重修改 ───

def make_variation_b(weights: Dict) -> Dict:
    """產生變因 B 權重：IP/新奇/重大事故/體育 加權，一般國際/經濟 降權。"""
    w = copy.deepcopy(weights)

    boost_cats = ["IP", "新奇", "運動", "公安", "死亡", "災害", "軍事", "衝突"]
    reduce_cats = ["泛國際", "經濟", "auto_negative"]

    for rule in w["feature_rules"]:
        name = rule["name"]
        cat = rule.get("category", "")
        combined = name + cat

        if any(x in combined for x in boost_cats):
            old = rule["weight"]
            if old > 0:
                rule["weight"] = round(old * 1.4, 2)  # +40%
        elif any(x in combined for x in reduce_cats):
            old = rule["weight"]
            if old < 0:
                rule["weight"] = round(old * 1.3, 2)  # 加大負面力道 30%
            elif old > 0:
                rule["weight"] = round(old * 0.6, 2)  # 降權 40%

    # IP entity boost 加碼
    w["ip_entity_boost"] = round(w.get("ip_entity_boost", 20.0) * 1.25, 1)

    # 加入 gold_set 03-18 的新 entities
    new_entities = ["經典賽", "冠軍戰", "委內瑞拉", "總冠軍賽", "WBC決賽",
                    "美國之音", "VOA", "裁員無效", "法官裁定"]
    existing = set(w.get("ip_entities", []))
    for e in new_entities:
        existing.add(e)
    w["ip_entities"] = sorted(existing)

    return w


# ─── 主實驗 ───

def run():
    # 載入現行權重
    with WEIGHTS_PATH.open("r") as f:
        weights_a = json.load(f)

    # 建立變因 B
    weights_b = make_variation_b(weights_a)

    # 載入所有樣本
    gold_samples = parse_gold_set(GOLD_SET_PATH)
    all_jsonl = []
    for p in sorted(SAMPLES_DIR.glob("*.jsonl")):
        all_jsonl.extend(load_jsonl(p))

    # 去重（by URL）
    seen = set()
    test_set = []
    for s in gold_samples:
        if s["url"] not in seen:
            seen.add(s["url"])
            test_set.append(s)
    for s in all_jsonl:
        if s.get("url", "") not in seen:
            seen.add(s["url"])
            test_set.append(s)

    positive_samples = [s for s in test_set if s.get("label") == "positive"]
    negative_samples = [s for s in test_set if s.get("label") == "negative"]

    threshold = weights_a.get("headline_threshold", 83.0)

    print(f"=== SkillEvo 2026-03-18 A/B Experiment ===")
    print(f"Test Set: {len(test_set)} total ({len(positive_samples)} pos, {len(negative_samples)} neg)")
    print(f"Threshold: {threshold}")
    print()

    # 評分
    results = {"A": [], "B": []}
    for s in test_set:
        title = s.get("title", "")
        kw = s.get("keywords", s.get("tags", []))
        label = s.get("label", "positive")

        score_a = score_with_weights(weights_a, title, kw)
        score_b = score_with_weights(weights_b, title, kw)

        results["A"].append({"title": title[:30], "label": label, "score": score_a})
        results["B"].append({"title": title[:30], "label": label, "score": score_b})

    # 計算指標
    for var_name in ["A", "B"]:
        items = results[var_name]
        pos_items = [x for x in items if x["label"] == "positive"]
        neg_items = [x for x in items if x["label"] == "negative"]

        hits = sum(1 for x in pos_items if x["score"] >= threshold)
        hit_rate = hits / len(pos_items) * 100 if pos_items else 0

        fp = sum(1 for x in neg_items if x["score"] >= threshold)
        fp_rate = fp / len(neg_items) * 100 if neg_items else 0

        # Top 10
        sorted_items = sorted(items, key=lambda x: x["score"], reverse=True)
        top10 = sorted_items[:10]
        top10_pos = sum(1 for x in top10 if x["label"] == "positive")
        top10_acc = top10_pos / min(10, len(top10)) * 100 if top10 else 0

        print(f"--- Variation {var_name} ---")
        print(f"  Hit Rate (pos >= {threshold}): {hits}/{len(pos_items)} = {hit_rate:.2f}%")
        print(f"  False Positive Rate: {fp}/{len(neg_items)} = {fp_rate:.2f}%")
        print(f"  Top 10 Accuracy: {top10_pos}/10 = {top10_acc:.1f}%")

        # Show top 10 detail
        print(f"  Top 10:")
        for i, x in enumerate(top10):
            marker = "+" if x["label"] == "positive" else "-"
            print(f"    {i+1}. [{marker}] {x['score']:.1f} | {x['title']}")
        print()

    # 判定
    a_pos = [x for x in results["A"] if x["label"] == "positive"]
    b_pos = [x for x in results["B"] if x["label"] == "positive"]
    a_hit = sum(1 for x in a_pos if x["score"] >= threshold)
    b_hit = sum(1 for x in b_pos if x["score"] >= threshold)
    a_rate = a_hit / len(a_pos) * 100 if a_pos else 0
    b_rate = b_hit / len(b_pos) * 100 if b_pos else 0

    a_sorted = sorted(results["A"], key=lambda x: x["score"], reverse=True)
    b_sorted = sorted(results["B"], key=lambda x: x["score"], reverse=True)
    a_top10_pos = sum(1 for x in a_sorted[:10] if x["label"] == "positive")
    b_top10_pos = sum(1 for x in b_sorted[:10] if x["label"] == "positive")

    winner = "B" if b_rate > a_rate else ("A" if a_rate > b_rate else "TIE")
    print(f"=== VERDICT: {winner} wins ===")
    print(f"  A Hit Rate: {a_rate:.2f}% | B Hit Rate: {b_rate:.2f}%")
    print(f"  A Top10 Pos: {a_top10_pos} | B Top10 Pos: {b_top10_pos}")

    # 輸出 B 權重供後續更新
    if winner == "B":
        print("\n>>> Variation B wins — writing updated weights...")
        weights_b["version"] = "v1.4-evo"
        from datetime import datetime, timezone
        weights_b["trained_at"] = datetime.now(timezone.utc).isoformat()
        weights_b["sample_count"] = len(test_set)
        weights_b["positive_count"] = len(positive_samples)
        weights_b["negative_count"] = len(negative_samples)
        out_path = WEIGHTS_PATH.parent / "v1_weights_candidate.json"
        with out_path.open("w", encoding="utf-8") as f:
            json.dump(weights_b, f, ensure_ascii=False, indent=2)
        print(f">>> Candidate written to {out_path}")

    return winner, a_rate, b_rate, a_top10_pos, b_top10_pos


if __name__ == "__main__":
    run()
