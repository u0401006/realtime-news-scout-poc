## Why

Trend Scout 已具備 CNA Agent skill 的新聞挑選能力，但目前每日摘要流程偏批次，無法即時支援編輯台選稿節奏。為驗證「以即時事件流 + 版面編排判斷」取代人工初選稿的可行性，需要啟動 PoC 專案並定義可量測的驗收門檻。

## What Changes

- 建立即時進件流程：以 webhook 為主、必要時以 bridge 補齊非 webhook 來源。
- 建立選稿評分引擎 v1：新聞價值分 + 版面適配分 + 去重/衝突規則。
- 建立編輯審核介面最小閉環：候選稿入選/淘汰理由可視化，並回寫人工決策。
- 建立即時監控與 SLO：事件延遲、候選命中率、採用率、漏稿率。

## Capabilities

### New Capabilities
- `realtime-ingestion-webhook`: 來源事件即時收流、驗簽、標準化與入列。
- `layout-aware-story-ranking`: 依新聞價值與版面需求進行候選排序。
- `editor-review-feedback`: 編輯採用/拒絕決策回寫與規則調整輸入。
- `realtime-monitoring-slo`: PoC 指標追蹤與告警。

### Modified Capabilities
- 無

## Impact

- 主要影響：`ingestion/*`, `ranking/*`, `editor-console/*`, `analytics/*`, `tests/e2e/*`。
- 依賴：新聞來源 webhook、Trend Scout 既有 skill、版面配置資料源。
- 風險：來源事件品質不一、同題重複事件、高峰時段延遲與誤排序。

## Success Criteria (PoC Gate)

- event-to-candidate p95 < 120 秒
- Top 10 候選稿編輯採用率 >= 40%
- 初選人工工時降低 >= 50%
- 重大漏稿率低於現行人工流程基線
