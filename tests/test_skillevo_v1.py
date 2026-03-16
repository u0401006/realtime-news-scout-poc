"""SkillEvo v1 單元測試。"""

from __future__ import annotations

import json
import math
import tempfile
from datetime import datetime, timezone
from pathlib import Path

import pytest

from training_data.schema import Label, TrainingSample
from training_data.backfill_from_smoke import smoke_line_to_sample, backfill
from ranking.model.v1_train import (
    LogisticModel,
    extract_features,
    predict_all,
    train,
)


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

NOW = datetime(2026, 3, 16, 18, 0, 0, tzinfo=timezone.utc)

SMOKE_POS = {
    "pid": "202603160001",
    "url": "https://www.cna.com.tw/news/afe/202603160001.aspx",
    "title": "台積電宣布 2nm 量產提前",
    "published_at": "2026-03-16T09:15:00+08:00",
    "keywords": ["business", "economy", "stock", "taiwan"],
    "body_length": 820,
    "score": 70,
    "reason": "[cp-economic +20]",
    "selected": True,
    "has_body": True,
}

SMOKE_NEG = {
    "pid": "202603160099",
    "url": "https://www.cna.com.tw/news/ahel/202603160099.aspx",
    "title": "某藝人出席活動",
    "published_at": "2026-03-16T10:00:00+08:00",
    "keywords": ["lifestyle"],
    "body_length": 50,
    "score": 20,
    "reason": "[cp-category -25]",
    "selected": False,
    "has_body": True,
}

SMOKE_MID = {
    "pid": "202603160050",
    "url": "https://www.cna.com.tw/news/ahel/202603160050.aspx",
    "title": "地方活動報導",
    "published_at": "2026-03-16T11:00:00+08:00",
    "keywords": ["lifestyle", "taiwan"],
    "body_length": 300,
    "score": 50,
    "reason": "[fallback]",
    "selected": True,
    "has_body": True,
}


# ---------------------------------------------------------------------------
# Schema 測試
# ---------------------------------------------------------------------------

class TestTrainingSample:
    """TrainingSample schema 驗證。"""

    def test_create_positive(self) -> None:
        s = TrainingSample(
            pid="001", url="https://x", title="T",
            published_at=NOW, score=70,
            label=Label.POSITIVE,
        )
        assert s.label == Label.POSITIVE
        assert s.score == 70

    def test_score_bounds(self) -> None:
        with pytest.raises(Exception):
            TrainingSample(
                pid="001", url="https://x", title="T",
                published_at=NOW, score=101,
            )

    def test_json_roundtrip(self) -> None:
        s = TrainingSample(
            pid="001", url="https://x", title="T",
            published_at=NOW, score=50,
        )
        raw = s.model_dump_json()
        s2 = TrainingSample.model_validate_json(raw)
        assert s2.pid == s.pid
        assert s2.label == Label.UNLABELED


# ---------------------------------------------------------------------------
# Backfill 測試
# ---------------------------------------------------------------------------

class TestBackfill:
    """smoke → training 回填測試。"""

    def test_high_score_positive(self) -> None:
        s = smoke_line_to_sample(SMOKE_POS, now=NOW)
        assert s.label == Label.POSITIVE
        assert s.label_source == "smoke_auto"

    def test_low_score_negative(self) -> None:
        s = smoke_line_to_sample(SMOKE_NEG, now=NOW)
        assert s.label == Label.NEGATIVE

    def test_mid_score_unlabeled(self) -> None:
        s = smoke_line_to_sample(SMOKE_MID, now=NOW)
        assert s.label == Label.UNLABELED

    def test_backfill_file(self, tmp_path: Path) -> None:
        infile = tmp_path / "smoke.jsonl"
        outfile = tmp_path / "out.jsonl"
        with infile.open("w") as f:
            f.write(json.dumps(SMOKE_POS) + "\n")
            f.write(json.dumps(SMOKE_NEG) + "\n")
            f.write(json.dumps(SMOKE_MID) + "\n")

        stats = backfill(infile, outfile)
        assert stats["total"] == 3
        assert stats["positive"] == 1
        assert stats["negative"] == 1
        assert stats["unlabeled"] == 1

        # 驗證 output 可解析
        with outfile.open() as f:
            for line in f:
                TrainingSample.model_validate_json(line.strip())


# ---------------------------------------------------------------------------
# Feature 測試
# ---------------------------------------------------------------------------

class TestFeatures:
    """特徵抽取測試。"""

    def test_economic_keyword(self) -> None:
        s = TrainingSample(
            pid="1", url="", title="台積電 2nm 量產",
            published_at=NOW, score=70, keywords=["business"],
            body_length=500,
        )
        feat = extract_features(s)
        assert feat[2] == 1.0  # f_economic (台積電)

    def test_low_category(self) -> None:
        s = TrainingSample(
            pid="2", url="", title="演唱會",
            published_at=NOW, score=30,
            keywords=["entertainment"],
            body_length=200,
        )
        feat = extract_features(s)
        assert feat[8] == 1.0  # f_low_category (index 8 in v1.1)

    def test_body_length_log(self) -> None:
        s = TrainingSample(
            pid="3", url="", title="X",
            published_at=NOW, score=50,
            body_length=1000,
        )
        feat = extract_features(s)
        assert abs(feat[9] - math.log1p(1000)) < 0.001  # f_body_length (index 9 in v1.1)


# ---------------------------------------------------------------------------
# 模型訓練測試
# ---------------------------------------------------------------------------

class TestV1Model:
    """v1 模型訓練 / 預測測試。"""

    def _make_samples(self) -> list[TrainingSample]:
        pos = TrainingSample(
            pid="p1", url="", title="台積電重大宣布",
            published_at=NOW, score=80,
            keywords=["business", "economy"],
            body_length=800, label=Label.POSITIVE,
        )
        neg = TrainingSample(
            pid="n1", url="", title="藝人八卦",
            published_at=NOW, score=20,
            keywords=["entertainment"],
            body_length=100, label=Label.NEGATIVE,
        )
        return [pos, neg]

    def test_train_runs(self) -> None:
        samples = self._make_samples()
        model = train(samples, epochs=50)
        assert len(model.weights) == 11  # v1.1: 11 features

    def test_predict_positive_higher(self) -> None:
        samples = self._make_samples()
        model = train(samples, epochs=100)
        preds = predict_all(model, samples)
        scores = {p.pid: p.score for p in preds}
        assert scores["p1"] > scores["n1"]

    def test_model_serialization(self) -> None:
        model = LogisticModel(weights=[0.1] * 11, bias=0.5)
        d = model.to_dict()
        m2 = LogisticModel.from_dict(d)
        assert m2.weights == model.weights
        assert m2.bias == model.bias

    def test_explain_has_content(self) -> None:
        model = LogisticModel(weights=[1.0, 0.5, 0.3, 0.1, -0.5, 0.2, 0.1], bias=0.0)
        feat = [1.0, 0.0, 1.0, 0.0, 0.0, 5.0, 3.0]
        reason = model.explain(feat)
        assert "f_" in reason

    def test_empty_training_data(self) -> None:
        model = train([], epochs=10)
        assert all(w == 0.0 for w in model.weights)
