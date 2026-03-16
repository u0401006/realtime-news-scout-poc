# Training Data Samples

此目錄存放 JSONL 格式的標註樣本檔。

## 檔案格式

每行一筆 JSON，符合 `training_data.schema.TrainingSample`。

## 檔案命名慣例

- `seed_YYYYMMDD.jsonl` — 由 smoke test 自動轉換的種子資料
- `labeled_YYYYMMDD.jsonl` — 經人工/編輯標註的資料
- `unlabeled_YYYYMMDD.jsonl` — 尚未標註的候選資料
