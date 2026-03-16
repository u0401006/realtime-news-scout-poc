# SkillEvo v1 — 排序模型骨架

## 概覽

SkillEvo v1 建立了「smoke → 標註 → 訓練 → 預測」的完整 pipeline，
對應 openspec tasks 5.1 + 5.2。

## 架構

```
smoke_result.jsonl (ingestion output)
        │
        ▼
training_data/backfill_from_smoke.py   ← 自動標註回填
        │
        ▼
training_data/samples/seed_*.jsonl     ← JSONL 標註資料
        │
        ▼
ranking/model/v1_train.py              ← 訓練 + 預測
        │
        ├─► ranking/model/v1_weights.json       ← 模型權重
        └─► ranking/model/v1_predictions.jsonl  ← 預測結果 (score + reason)
```

## Training Data Schema

定義於 `training_data/schema.py`：

| 欄位 | 型別 | 說明 |
|------|------|------|
| pid | str | CNA 稿件 ID |
| url | str | 原始 URL |
| title | str | 標題 |
| published_at | datetime | 發佈時間 |
| keywords | list[str] | sitemap 分類 |
| body_length | int | 內文字數 |
| score | int (0–100) | rule-based 分數 |
| reason | str | 選稿理由 |
| label | "positive" / "negative" / "unlabeled" | 標註 |
| label_source | str | 標註來源 |
| labeled_at | datetime? | 標註時間 |
| editor_note | str? | 編輯備註 |

## 標註回填流程

`backfill_from_smoke.py` 自動根據 score 門檻標註：
- score >= 65 → positive
- score <= 35 → negative
- 其餘 → unlabeled（待人工確認）

```bash
cd realtime-news-scout-poc
.venv/bin/python -m training_data.backfill_from_smoke \
    --input output/smoke_result.jsonl \
    --output training_data/samples/seed_20260316.jsonl
```

## v1 排序模型

手工特徵 + Logistic Regression baseline：

| 特徵 | 說明 |
|------|------|
| f_breaking | 突發關鍵字命中 |
| f_political | 政治關鍵字命中 |
| f_economic | 經濟關鍵字命中 |
| f_international | 國際關鍵字命中 |
| f_low_category | 低優先類別 |
| f_body_length | 內文長度（log） |
| f_keyword_count | keywords 數量 |

```bash
.venv/bin/python -m ranking.model.v1_train \
    --data training_data/samples/seed_20260316.jsonl
```

輸出：
- `ranking/model/v1_weights.json` — 可序列化的模型權重
- `ranking/model/v1_predictions.jsonl` — 每行 `{pid, title, score, reason, original_label}`

## 測試

```bash
.venv/bin/python -m pytest tests/test_skillevo_v1.py -v
```

15 個測試覆蓋 schema 驗證、回填流程、特徵抽取、模型訓練/預測/序列化。
