## ADDED Requirements

### Requirement: PoC 必須有可追蹤的即時成效指標
系統 MUST 持續追蹤延遲、採用率、漏稿率與誤報率，並在超出門檻時告警。

#### Scenario: 正常指標追蹤
- **WHEN** 候選稿產生與編輯決策持續發生
- **THEN** 系統更新 event-to-candidate 延遲、Top N 採用率與漏稿統計

#### Scenario: 延遲超過門檻
- **WHEN** event-to-candidate p95 高於 120 秒
- **THEN** 系統觸發告警並附上受影響時段摘要

#### Scenario: 週期性驗收檢核
- **WHEN** 到達 PoC 檢核節點
- **THEN** 系統輸出對照報告（人工流程 vs 自動流程）作為 go/no-go 依據
