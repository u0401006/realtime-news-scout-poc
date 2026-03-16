#!/usr/bin/env python3
"""SkillEvo v1 — 排序模型訓練腳本（Logistic Regression baseline）。

v1 使用手工特徵 + Logistic Regression 作為 baseline，
輸出每筆候選稿的 score（0.0–1.0）和可解釋 reason。

特徵工程：
  - f_breaking:     是否命中突發關鍵字（binary）
  - f_political:    是否命中政治關鍵字（binary）
  - f_economic:     是否命中經濟關鍵字（binary）
  - f_international: 是否命中國際關鍵字（binary）
  - f_low_category: 類別是否全為低優先（binary，負向）
  - f_body_length:  內文長度（log-normalized）
  - f_keyword_count: sitemap keywords 數量

訓練：
  - 只使用 label=positive / label=negative 的資料
  - unlabeled 排除不參與訓練

輸出：
  - model artifact: ranking/model/v1_weights.json
  - 預測結果: ranking/model/v1_predictions.jsonl（每行含 pid, score, reason）

用法：
    cd realtime-news-scout-poc
    python -m ranking.model.v1_train \
        --data training_data/samples/seed.jsonl \
        [--output-weights ranking/model/v1_weights.json] \
        [--output-predictions ranking/model/v1_predictions.jsonl]
"""

from __future__ import annotations

import argparse
import json
import math
import sys
from dataclasses import dataclass, field
from pathlib import Path
from typing import Optional

sys.path.insert(0, str(Path(__file__).resolve().parent.parent.parent))

from training_data.schema import Label, TrainingSample  # noqa: E402

# ---------------------------------------------------------------------------
# 特徵抽取（與 headline_selection check-points 對齊）
# ---------------------------------------------------------------------------

BREAKING_KW = {
    "爆炸", "傷亡", "地震", "颱風", "海嘯", "墜機",
    "槍擊", "恐攻", "核災", "疫情", "封城", "戒嚴",
    "火警", "衝突", "落石", "骨折", "開槍", "追捕",
    "事故", "罹難", "搜救", "直升機",
}
POLITICAL_KW = {
    "總統", "行政院", "立法院", "監察院", "司法院",
    "選舉", "罷免", "彈劾", "修憲", "公投",
    "國防", "外交", "兩岸", "內政部", "財政部",
    "卓榮泰", "管碧玲", "國土安全",
}
ECONOMIC_KW = {
    "半導體", "台積電", "AI", "人工智慧",
    "股市", "央行", "利率", "通膨", "GDP",
    "貿易", "關稅", "制裁", "薪資", "營收",
    "增資", "量產",
}
INTERNATIONAL_KW = {
    "以色列", "真主黨", "伊朗", "烏克蘭", "俄羅斯",
    "北約", "聯合國", "G7", "G20", "APEC",
}

# 新增：IP / 知名品牌 / 高知名度企業
IP_KW = {
    "台積電", "鴻海", "中華電", "聯發科", "NVIDIA",
    "Apple", "Google", "Tesla", "TSMC",
    "騰輝", "永豐餘", "寶可夢",
    "GTC", "人形機器人", "AI伺服器",
}

# 新增：新奇性關鍵字
NOVELTY_KW = {
    "首次", "首度", "創紀錄", "突破", "新種", "命名",
    "史上", "最高", "最大", "翻倍", "里程碑",
}

# 新增：高關注賽事
SPORTS_KW = {
    "奧運", "世足", "WBC", "MLB", "NBA", "世界盃",
    "亞運", "大聯盟", "冬奧", "國訓中心",
}

# 泛國際 — 這些關鍵字單獨命中但無其他正向特徵時應被過濾
GENERIC_INTL_KW = {
    "中國", "美國", "日本", "歐盟",
}

LOW_PRIORITY_CAT = {"entertainment", "sport", "lifestyle"}

FEATURE_NAMES = [
    "f_breaking",
    "f_political",
    "f_economic",
    "f_international",
    "f_ip",
    "f_novelty",
    "f_sports",
    "f_generic_intl_only",
    "f_low_category",
    "f_body_length",
    "f_keyword_count",
]


def _has_keyword_hit(title: str, keywords: set[str]) -> bool:
    """標題是否命中任一關鍵字。"""
    return any(kw in title for kw in keywords)


def extract_features(sample: TrainingSample) -> list[float]:
    """抽取特徵向量（v1.1 — 含新奇、IP、賽事、泛國際負向）。"""
    title = sample.title
    cats = {k.lower() for k in sample.keywords}

    hit_breaking = _has_keyword_hit(title, BREAKING_KW)
    hit_political = _has_keyword_hit(title, POLITICAL_KW)
    hit_economic = _has_keyword_hit(title, ECONOMIC_KW)
    hit_international = _has_keyword_hit(title, INTERNATIONAL_KW)
    hit_ip = _has_keyword_hit(title, IP_KW)
    hit_novelty = _has_keyword_hit(title, NOVELTY_KW)
    hit_sports = _has_keyword_hit(title, SPORTS_KW)

    # 泛國際：命中 GENERIC_INTL_KW 但未命中任何正向特徵
    hit_generic_intl = _has_keyword_hit(title, GENERIC_INTL_KW)
    any_positive = any([
        hit_breaking, hit_political, hit_economic,
        hit_international, hit_ip, hit_novelty, hit_sports,
    ])
    generic_intl_only = hit_generic_intl and not any_positive

    return [
        1.0 if hit_breaking else 0.0,
        1.0 if hit_political else 0.0,
        1.0 if hit_economic else 0.0,
        1.0 if hit_international else 0.0,
        1.0 if hit_ip else 0.0,
        1.0 if hit_novelty else 0.0,
        1.0 if hit_sports else 0.0,
        1.0 if generic_intl_only else 0.0,   # 負向特徵
        1.0 if (cats and cats.issubset(LOW_PRIORITY_CAT)) else 0.0,
        math.log1p(sample.body_length),  # log-normalized
        float(len(sample.keywords)),
    ]


# ---------------------------------------------------------------------------
# Logistic Regression（純 Python，無外部 ML 套件依賴）
# ---------------------------------------------------------------------------

def _sigmoid(z: float) -> float:
    """Numerically stable sigmoid."""
    if z >= 0:
        return 1.0 / (1.0 + math.exp(-z))
    ez = math.exp(z)
    return ez / (1.0 + ez)


@dataclass
class LogisticModel:
    """簡易 Logistic Regression（SGD 訓練）。"""

    weights: list[float] = field(default_factory=list)
    bias: float = 0.0
    feature_names: list[str] = field(default_factory=lambda: list(FEATURE_NAMES))

    def predict(self, features: list[float]) -> float:
        """回傳 0.0–1.0 的 score。"""
        z = self.bias + sum(w * x for w, x in zip(self.weights, features))
        return _sigmoid(z)

    def explain(self, features: list[float]) -> str:
        """產生可解釋 reason：列出貢獻最大的特徵。"""
        contributions = [
            (self.feature_names[i], self.weights[i] * features[i])
            for i in range(len(features))
        ]
        # 按絕對貢獻排序，取前 3
        contributions.sort(key=lambda x: abs(x[1]), reverse=True)
        parts: list[str] = []
        for name, contrib in contributions[:3]:
            if abs(contrib) < 0.01:
                continue
            sign = "+" if contrib >= 0 else ""
            parts.append(f"{name}={sign}{contrib:.2f}")
        if not parts:
            return "無顯著特徵貢獻"
        return "top features: " + ", ".join(parts)

    def to_dict(self) -> dict[str, object]:
        """序列化為 dict。"""
        return {
            "version": "v1",
            "feature_names": self.feature_names,
            "weights": self.weights,
            "bias": self.bias,
        }

    @classmethod
    def from_dict(cls, data: dict[str, object]) -> LogisticModel:
        """從 dict 還原。"""
        return cls(
            weights=list(data["weights"]),  # type: ignore[arg-type]
            bias=float(data["bias"]),  # type: ignore[arg-type]
            feature_names=list(data.get("feature_names", FEATURE_NAMES)),  # type: ignore[arg-type]
        )


def train(
    samples: list[TrainingSample],
    *,
    lr: float = 0.1,
    epochs: int = 100,
) -> LogisticModel:
    """以 SGD 訓練 Logistic Regression。

    只使用 label=positive / negative 的樣本。

    Args:
        samples: 訓練樣本列表。
        lr: 學習率。
        epochs: 訓練 epoch 數。

    Returns:
        訓練完成的 LogisticModel。
    """
    # 篩選有標註的資料
    labeled = [
        s for s in samples
        if s.label in (Label.POSITIVE, Label.NEGATIVE)
    ]

    if not labeled:
        print("⚠️ 無有效標註資料，回傳零權重模型")
        return LogisticModel(weights=[0.0] * len(FEATURE_NAMES))

    n_features = len(FEATURE_NAMES)
    model = LogisticModel(weights=[0.0] * n_features)

    # 準備資料
    data: list[tuple[list[float], float]] = []
    for s in labeled:
        feat = extract_features(s)
        y = 1.0 if s.label == Label.POSITIVE else 0.0
        data.append((feat, y))

    print(f"📊 訓練資料: {len(data)} 筆 "
          f"(positive={sum(1 for _, y in data if y > 0.5)}, "
          f"negative={sum(1 for _, y in data if y <= 0.5)})")

    # SGD 訓練
    for epoch in range(epochs):
        total_loss = 0.0
        for feat, y in data:
            pred = model.predict(feat)
            error = pred - y
            total_loss += -y * math.log(max(pred, 1e-15)) - (1 - y) * math.log(max(1 - pred, 1e-15))

            # 梯度更新
            for i in range(n_features):
                model.weights[i] -= lr * error * feat[i]
            model.bias -= lr * error

        if (epoch + 1) % 20 == 0:
            avg_loss = total_loss / len(data)
            print(f"  epoch {epoch + 1:>4d}/{epochs}: loss={avg_loss:.4f}")

    return model


# ---------------------------------------------------------------------------
# 預測 & 輸出
# ---------------------------------------------------------------------------

@dataclass
class Prediction:
    """單筆預測結果。"""

    pid: str
    title: str
    score: float
    reason: str
    original_label: str


def predict_all(
    model: LogisticModel,
    samples: list[TrainingSample],
) -> list[Prediction]:
    """對所有樣本（含 unlabeled）預測。"""
    results: list[Prediction] = []
    for s in samples:
        feat = extract_features(s)
        score = model.predict(feat)
        reason = model.explain(feat)
        results.append(Prediction(
            pid=s.pid,
            title=s.title,
            score=round(score, 4),
            reason=reason,
            original_label=s.label.value,
        ))
    results.sort(key=lambda r: r.score, reverse=True)
    return results


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

def load_samples(path: Path) -> list[TrainingSample]:
    """從 JSONL 載入 TrainingSample。"""
    samples: list[TrainingSample] = []
    with path.open("r", encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            samples.append(TrainingSample.model_validate_json(line))
    return samples


def main() -> None:
    """CLI 入口。"""
    parser = argparse.ArgumentParser(description="SkillEvo v1 排序模型訓練")
    parser.add_argument(
        "--data", "-d",
        type=Path,
        default=Path("training_data/samples/seed.jsonl"),
        help="訓練資料 JSONL 路徑",
    )
    parser.add_argument(
        "--output-weights", "-w",
        type=Path,
        default=Path("ranking/model/v1_weights.json"),
        help="模型權重輸出路徑",
    )
    parser.add_argument(
        "--output-predictions", "-p",
        type=Path,
        default=Path("ranking/model/v1_predictions.jsonl"),
        help="預測結果輸出路徑",
    )
    parser.add_argument("--lr", type=float, default=0.1, help="學習率")
    parser.add_argument("--epochs", type=int, default=100, help="訓練 epochs")
    args = parser.parse_args()

    # 載入資料
    print(f"📥 載入訓練資料: {args.data}")
    samples = load_samples(args.data)
    print(f"   共 {len(samples)} 筆")

    # 訓練
    print("🏋️ 開始訓練...")
    model = train(samples, lr=args.lr, epochs=args.epochs)

    # 儲存權重
    args.output_weights.parent.mkdir(parents=True, exist_ok=True)
    with args.output_weights.open("w", encoding="utf-8") as f:
        json.dump(model.to_dict(), f, ensure_ascii=False, indent=2)
    print(f"💾 權重已儲存: {args.output_weights}")

    # 預測
    preds = predict_all(model, samples)
    args.output_predictions.parent.mkdir(parents=True, exist_ok=True)
    with args.output_predictions.open("w", encoding="utf-8") as f:
        for p in preds:
            f.write(json.dumps({
                "pid": p.pid,
                "title": p.title,
                "score": p.score,
                "reason": p.reason,
                "original_label": p.original_label,
            }, ensure_ascii=False) + "\n")
    print(f"📤 預測結果已儲存: {args.output_predictions}")

    # 摘要
    print("\n📊 預測摘要（前 5 名）:")
    for p in preds[:5]:
        print(f"  {p.score:.4f} | [{p.original_label:>9s}] {p.title[:40]}... | {p.reason}")

    # Accuracy on labeled data
    labeled_preds = [p for p in preds if p.original_label != "unlabeled"]
    if labeled_preds:
        correct = sum(
            1 for p in labeled_preds
            if (p.score >= 0.5 and p.original_label == "positive")
            or (p.score < 0.5 and p.original_label == "negative")
        )
        acc = correct / len(labeled_preds)
        print(f"\n🎯 標註資料 accuracy: {correct}/{len(labeled_preds)} = {acc:.1%}")


if __name__ == "__main__":
    main()
