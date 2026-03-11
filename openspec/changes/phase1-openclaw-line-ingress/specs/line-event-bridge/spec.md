## ADDED Requirements

### Requirement: Webhook 必須快速確認與非同步派工
系統 MUST 在收到 LINE webhook 後快速回應 200，並將任務非同步送入 OpenClaw。

#### Scenario: 正常事件
- **WHEN** 收到有效 LINE 事件
- **THEN** 服務在 2 秒內回應 200 並完成任務入列

#### Scenario: 簽章失敗
- **WHEN** LINE 簽章無效或缺失
- **THEN** 系統回應 401 並拒絕入列