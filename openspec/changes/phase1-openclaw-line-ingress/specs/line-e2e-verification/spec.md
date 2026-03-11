## ADDED Requirements

### Requirement: 系統必須提供 LINE 端到端驗收
系統 MUST 驗證從 LINE 指令輸入到 OpenClaw 回覆輸出的完整流程。

#### Scenario: 指令成功回覆
- **WHEN** 使用者送出有效任務指令
- **THEN** 使用者在可接受時間內收到任務處理回覆

#### Scenario: 上游失敗回覆
- **WHEN** OpenClaw 執行失敗
- **THEN** 使用者收到可理解的失敗訊息與重試指引