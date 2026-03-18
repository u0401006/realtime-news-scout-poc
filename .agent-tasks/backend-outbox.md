# Backend Outbox

## Task Completed: headline_selection.py 升級為 V2 Scorer 驅動模式

- **Status:** DONE
- **Date:** 2026-03-18T08:27+08:00
- **Priority:** P0

### Files changed
- `ingestion/adapters/headline_selection.py` — 完整重寫

### 變更摘要

1. **`evaluate()` 主判斷切換至 V2Scorer**
   - 內部 lazy-init `V2Scorer` 單例，呼叫 `scorer.score()` 取得 `V2ScoreResult`
   - 映射為 `HeadlineVerdict`（保留 `selected`, `reason`, `score`, `check_points`, `is_fallback` 所有欄位）
   - 新增擴充欄位：`effective_threshold`, `content_tier`, `ip_matches`, `gtrend_boost`, `economic_boost`

2. **V2 Scorer 功能完整串接**
   - 浮動門檻（滑動視窗 P75 + 震盪保護，預設 ≥ 94.0）
   - TSMC 分層 IP 匹配（L1–L4）
   - gTrend 動態加分
   - 經濟劇烈震盪偵測
   - Firebase trending 加分
   - 內容型態分類（P0_short / P0_main / P1_followup 等）× tier 乘數

3. **向下相容保證**
   - `evaluate(entry, body) → HeadlineVerdict` 介面不變
   - `smoke_cna_window.py` 的 `from ingestion.adapters.headline_selection import evaluate` 無需修改
   - `HeadlineVerdict` 和 `CheckPointResult` dataclass 結構保留

4. **V1 Legacy 保留**
   - 舊版 check-point 邏輯移至 `evaluate_v1(entry, body) → HeadlineVerdict`
   - 所有 V1 關鍵字清單、權重常數保留（加底線前綴 `_V1_`）
   - 可用於回退或 A/B 測試

5. **Scorer 管理 API**
   - `configure_scorer(...)`: 自訂參數初始化（weights、gtrend、firebase 等）
   - `reset_scorer()`: 重設單例（測試用）

### 測試結果
- ✅ V1 Legacy：`台積電2奈米量產` → selected=True, score=100
- ✅ V2 Scorer：`台積電2奈米量產` → selected=True, score=95, tier=P0_main, threshold=94.0
- ✅ V1 低分：`天氣晴朗` → selected=False, score=25
- ✅ V2 低分：`天氣晴朗` → selected=False, score=42, threshold=94.0
- ✅ Import 相容性：`smoke_cna_window.py` / `smoke_v2_scorer.py` 正常

### Migration
- N/A（無資料庫異動）

### Blockers
- 無

### Questions
- 無
