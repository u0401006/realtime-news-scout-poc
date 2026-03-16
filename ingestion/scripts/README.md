# ingestion/scripts

## send_smoke_result.py

將 smoke test 輸出的 JSONL 選稿結果，格式化為「入選/未入選 + 理由」清單，
支援 **dry-run**（預設）與實際 OpenClaw message 發送。

---

## 安裝需求

- Python 3.10+（使用 `list[dict]` 型態提示）
- 無額外 pip 套件（僅標準庫）
- 實際發送需安裝並設定好 `openclaw` CLI

---

## JSONL 輸入格式

每行一個 JSON 物件：

```jsonl
{
  "title": "新聞標題",
  "url": "https://...",
  "selected": true,
  "reason": "選稿理由說明",
  "score": 0.87,
  "source": "CNA",
  "publishedAt": "2026-03-16T10:00:00+08:00"
}
```

| 欄位 | 型別 | 必填 | 說明 |
|------|------|------|------|
| `title` | string | ✅ | 新聞標題 |
| `url` | string | — | 文章連結 |
| `selected` | bool | ✅ | `true` = 入選，`false` = 未入選 |
| `reason` | string | ✅ | 入選/未入選理由 |
| `score` | float | — | 評分（0.0～1.0） |
| `source` | string | — | 新聞來源 |
| `publishedAt` | string | — | 發布時間（ISO 8601） |

---

## 執行方式

### 1. Dry-run（預設，僅印出格式化訊息）

```bash
python3 send_smoke_result.py --input ../sample_data/smoke_output.jsonl
```

### 2. 加上視窗標籤

```bash
python3 send_smoke_result.py \
  --input ../sample_data/smoke_output.jsonl \
  --window "2026-03-16 10:00~11:00"
```

### 3. 限制顯示筆數

```bash
python3 send_smoke_result.py \
  --input ../sample_data/smoke_output.jsonl \
  --limit 5
```

### 4. 儲存格式化結果到檔案

```bash
python3 send_smoke_result.py \
  --input ../sample_data/smoke_output.jsonl \
  --output /tmp/smoke_preview.txt
```

### 5. 實際發送到 Telegram 頻道

```bash
python3 send_smoke_result.py \
  --input ../sample_data/smoke_output.jsonl \
  --send \
  --channel -1001234567890
```

---

## 參數說明

| 參數 | 短名 | 預設 | 說明 |
|------|------|------|------|
| `--input` | `-i` | （必填） | JSONL 輸入檔案路徑 |
| `--send` | — | `False` | 啟用實際發送（否則 dry-run） |
| `--channel` | `-c` | `""` | 目標頻道 ID（--send 時必填） |
| `--limit` | `-n` | `0`（全部） | 最多顯示 N 筆 |
| `--window` | `-w` | `""` | 視窗標籤字串 |
| `--output` | `-o` | `""` | 另存格式化訊息為純文字檔 |

---

## 輸出格式範例

```
📰 *Smoke Test 選稿結果*
時間：2026-03-16 10:30
視窗：2026-03-16 10:00~11:00
入選 4 篇 ／ 未入選 3 篇
──────────────────────────────

✅ **入選稿件**
✅ [1] 台積電宣布新一代 2nm 晶片量產時程提前至 Q3
  狀態：入選
  理由：高科技產業重大里程碑，具廣泛影響力，情感中性，報導時效高
  分數：0.92
  來源：CNA
  https://cna.com.tw/...

❌ **未入選稿件**
❌ [1] 某藝人分手傳聞引爆網路熱議
  狀態：未入選
  理由：娛樂八卦類，不符合硬新聞選稿標準，情感偏向標題黨
  分數：0.23
  來源：ETtoday
  https://example.com/...

──────────────────────────────
```

---

## 測試指引

### 快速驗證（使用範例資料）

```bash
cd /path/to/realtime-news-scout-poc
python3 ingestion/scripts/send_smoke_result.py \
  --input ingestion/sample_data/smoke_output.jsonl \
  --window "2026-03-16 09:00~11:00"
```

預期輸出：7 筆資料（4 入選、3 未入選），印出格式化清單，結尾顯示 `[DRY-RUN]` 提示。

### 驗證 JSON 錯誤處理

```bash
echo '{"title":"test","selected":true,"reason":"ok"}
invalid json line
{"title":"test2","selected":false,"reason":"no"}' > /tmp/test.jsonl

python3 ingestion/scripts/send_smoke_result.py --input /tmp/test.jsonl
```

預期：第 2 行顯示 `[WARN]` 警告，正常處理第 1、3 筆。

### 驗證缺少 --channel 時的錯誤提示

```bash
python3 ingestion/scripts/send_smoke_result.py \
  --input ingestion/sample_data/smoke_output.jsonl \
  --send
# 預期輸出：[ERROR] 使用 --send 時必須指定 --channel
```
