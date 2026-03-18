# QA Outbox

## Task Completed: smoke_cna_window.py + send_smoke_result.py V2 格式升級

- **Status:** DONE
- **Date:** 2026-03-18T08:33+08:00
- **Priority:** P0
- **Reviewer:** QA

### 審查對象
1. `ingestion/scripts/smoke_cna_window.py` — V2 Scorer 驅動升級
2. `ingestion/scripts/send_smoke_result.py` — V2 格式顯示升級

---

### smoke_cna_window.py 變更摘要

| 項目 | V1（舊） | V2（新） |
|------|---------|---------|
| 評分引擎 | `evaluate()` + 額外呼叫 `classify_content_tier()` | 純粹使用 `evaluate()` 回傳的 V2 欄位 |
| Content Tier | 從 `v1_scorer.classify_content_tier` 獨立分類 | 直接取 `verdict.content_tier`（V2Scorer 內建） |
| 門檻資訊 | 無 | `effectiveThreshold`（浮動門檻） |
| Boost 資訊 | 無 | `gtrendBoost`, `economicBoost`, `ipMatches` |
| JSONL 輸出 | `scoring` 僅含 newsValue/selected/reason | `scoring` 新增 effectiveThreshold/gtrendBoost/economicBoost/ipMatches |
| Summary 報告 | 基礎統計 | 新增門檻統計、Boost 分佈、score/threshold 對比 |
| Import 清潔度 | 引入 `classify_content_tier` | 移除，僅保留 `ContentTier` enum（用於排序） |

**關鍵修改：**
- 移除 `from ranking.model.v1_scorer import classify_content_tier`
- `build_v2_record()` 新增參數：`effective_threshold`, `gtrend_boost`, `economic_boost`, `ip_matches`
- `score_events()` 改從 `verdict.content_tier` / `verdict.effective_threshold` 等取值
- `_print_summary()` 新增門檻平均值、Boost 統計、score/threshold 對比輸出
- 排序中的 `ContentTier` enum 查詢加入 try/except 防禦（防止未知 tier 崩潰）

### send_smoke_result.py 變更摘要

| 項目 | V1（舊） | V2（新） |
|------|---------|---------|
| 格式偵測 | 手動 `"content" in rec` | `_is_v2()` 輔助函式 |
| 欄位存取 | 混合邏輯 | `_get_field()` 統一抽象層（V1/V2 自動適配） |
| 分數顯示 | 僅顯示分數 | 顯示 `分數 / 門檻（±差距）` |
| Tier 顯示 | 僅名稱 | 帶彩色 emoji 的 `TIER_LABELS` 映射 |
| Boost 顯示 | 無 | 顯示 gTrend / 經濟震盪 / IP 匹配 |
| 統計區塊 | 僅入選/未入選數 | 新增平均分數/門檻、Tier 分佈、Boost 統計 |
| 版本標示 | 固定 "(V2)" | 自動偵測 V1/V2 |
| V1 向下相容 | 部分 | 完整（`_get_field` 自動回退 V1 格式） |

---

### QA 檢查結果

| 檢查項目 | 結果 | 說明 |
|----------|------|------|
| AST 語法檢查 | ✅ PASS | 兩個檔案皆通過 `ast.parse` |
| V2 欄位完整性 | ✅ PASS | effectiveThreshold/gtrendBoost/economicBoost/ipMatches/contentTier 皆有引用 |
| 向下相容 | ✅ PASS | `evaluate()` 介面不變；`send_smoke_result.py` 保留 V1 格式支援 |
| classify_content_tier 移除 | ✅ PASS | 不再獨立呼叫，改用 `verdict.content_tier` |
| Import 一致性 | ✅ PASS | `headline_selection.evaluate` 為唯一評分入口 |
| 排序邏輯 | ✅ PASS | score 降序 → tier 升序，加入 KeyError 防禦 |
| Summary 報告 | ✅ PASS | 含門檻統計、Boost 分佈、score/threshold 對比 |

### 未測試（環境限制）

- ⚠️ 無法執行 runtime import 測試（缺 `httpx` 依賴）
- ⚠️ 未測試實際 CNA sitemap 抓取（需網路 + 完整依賴）
- ⚠️ `smoke_v2_scorer.py` 未在此任務範圍內修改（仍直接使用 `V2Scorer`，無需升級）

### 建議

1. 在完整環境中執行 `python -m pytest` 確認整合測試
2. 可考慮為 `send_smoke_result.py` 加入 unit test（V1/V2 格式各一筆的 fixture）
3. `smoke_v2_scorer.py` 的輸出格式與 `smoke_cna_window.py` 不一致（前者 flat、後者 nested），未來可統一

### Blockers
- 無

### Questions
- 無
