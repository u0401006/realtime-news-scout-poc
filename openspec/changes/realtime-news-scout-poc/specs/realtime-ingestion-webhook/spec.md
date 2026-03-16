## ADDED Requirements

### Requirement: 來源事件必須即時收流並標準化
系統 MUST 支援 webhook 事件即時接收，並在快速 ACK 後將事件標準化入列。

#### Scenario: 正常 webhook 事件
- **WHEN** 收到有效簽章且結構完整的來源事件
- **THEN** 服務在 2 秒內回應 200，並將標準化事件送入候選處理佇列

#### Scenario: 重複事件
- **WHEN** 在冪等視窗內收到相同事件識別碼
- **THEN** 系統不得重複入列，僅記錄重複命中

#### Scenario: 簽章或格式無效
- **WHEN** 簽章驗證失敗或必要欄位缺失
- **THEN** 系統回應 4xx，並記錄拒收原因
