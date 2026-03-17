# AutoResearch 規劃草案 (v1.0)
## 目標
建立一套自動化的「反饋 -> 學習 -> 演進」閉環，將 Capo 的手動仲裁自動化轉化為 Scorer 的邏輯優化建議，減少手動修改代碼的負擔，並確保權重調整具備數據支撐。

## 1. 數據落實 (Ground Truth Collection)
*   **路徑**：`training_data/samples/capo_feedback.jsonl`
*   **機制**：每次 Capo 在 Telegram 對新聞進行「👍/👎」或「這則要過/不該過」的評價時，由 PM Agent 自動擷取並寫入該文件。
*   **欄位**：`timestamp`, `title`, `current_score`, `label` (1:PASS, 0:FAIL), `reason` (如果有)。

## 2. 自動化研究路徑 (AutoResearch Workflow)
*   **觸發機制**：每日凌晨 (Cron) 或當累積 feedback 達 50 筆時觸發。
*   **步驟**：
    1.  **分佈分析**：統計近期入選 5% 的新聞特徵（關鍵字雲、來源分佈）。
    2.  **誤差分析 (Error Analysis)**：找出「門檻外但 Capo 說要過 (False Negative)」與「門檻內但 Capo 說不該過 (False Positive)」的樣本。
    3.  **歸因研究**：利用 LLM 交叉比對這兩類樣本的標籤特徵。
    4.  **建議生成**：產出 `evo_report_<date>.md`，列出權重調整建議（例如：增加「憲法法庭」加權 +10.0）。

## 3. 技能演進 (Skill Evolution)
*   **回測驗證**：在正式修改 `v2_scorer.py` 前，自動跑 `smoke_test` 比對新舊權重對歷史數據的影響。
*   **演進報告**：向 Capo 提交報告，含：
    *   目前 P95 門檻變化趨勢。
    *   建議新增/修改的權重項。
    *   預估修改後對「入選品質」的提升。

## 4. 防止幻想 (Anti-Hallucination)
*   **引用檢查**：所有權重建議必須引用 `training_data` 中的具體樣本 ID。
*   **數據鎖定**：如果某個關鍵字在 `sample_negative.jsonl` 中出現頻率極高，系統自動阻斷其被提升權重的可能性。

## 下一步行動
1. [ ] 啟動 `backfill_from_smoke.py`：將今日 2026-03-17 的所有煙霧測試結果正式入庫。
2. [ ] 建立 `capo_feedback_collector`：對接 Telegram 反饋至 `training_data`。
3. [ ] 撰寫第一份 `autoresearch_v1.py` 原型腳本。
