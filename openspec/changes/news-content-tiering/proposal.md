## Why
目前 score 能反映重要性，但尚未顯式區分「快訊短稿、主稿、後續、回應、分析」等內容型態。編輯決策需要內容層級辨識，避免分析/回應稿與主事件稿混排。

## What Changes
- 新增內容型態分層 `content_tier`：`P0_short`, `P0_main`, `P1_followup`, `P2_response`, `P3_analysis`。
- 新增 `tier_reason`，可解釋判定依據。
- 同分排序增加 tier 優先序：`P0_short > P0_main > P1_followup > P2_response > P3_analysis`。
- 以既有 score + tier 雙軸輸出供編輯台判斷。

## Impact
- 影響 ranking scorer 與 smoke 輸出欄位。
- 需新增測試樣本與回歸測試，避免把 P2/P3 誤判成 P0。