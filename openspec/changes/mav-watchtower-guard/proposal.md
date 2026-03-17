# OpenSpec Proposal: MAV-Watchtower Guard

## Why
Scorer v2.16 雖然具備量化評分能力，但對於複雜的「內容農場標題黨」與「單一公司公關稿」仍可能因關鍵字命中而產生 False Positive。為達成 Capo 的 P95 天條，需要引入 MAV (Multi-Agent Verification) 進行二審仲裁。

## What Changes
1. **Scorer Hook**: 在 `v2_scorer.py` 評分結束後，針對分數 > 90.0 的新聞觸發非同步 MAV 驗證。
2. **MAV-Council Integration**: 使用 MAV-Council 進行三模型投票：
   - **Model A (Context)**: 判斷是否為「狀態改變」的硬新聞。
   - **Model B (Bias/Clickbait)**: 偵測是否具備「標題黨、公關稿、內容農場」特徵。
   - **Model C (Compliance)**: 檢查是否符合台灣用語規範與政治核心 IP 加權。
3. **Veto Logic**: 只要 MAV 多數決 (2/3) 判定為不適格，則無論 Scorer 分數多高，一律攔截 (Eligible = False)。

## Acceptance Criteria
- 成功攔截帶有「油價」關鍵字但純屬單一公司回應的新聞。
- 成功攔截具備農場氣息的標題。
- 確保所有入選新聞之摘要完全不含簡體字或中國用語。
