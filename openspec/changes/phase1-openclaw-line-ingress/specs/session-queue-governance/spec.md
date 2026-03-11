## ADDED Requirements

### Requirement: 多使用者必須 session 隔離
系統 MUST 以使用者 ID 映射 session，避免跨使用者上下文污染。

#### Scenario: 同時多使用者請求
- **WHEN** 多位使用者同時發送指令
- **THEN** 系統分派到不同 session 並各自追蹤結果

### Requirement: 系統必須提供併發與重試治理
系統 MUST 支援每使用者與全域併發上限，並對失敗任務進行有限次重試。

#### Scenario: 併發超限
- **WHEN** 任務超過併發上限
- **THEN** 系統將任務排入佇列並回報排隊狀態

#### Scenario: 任務重試失敗
- **WHEN** 任務達到最大重試次數仍失敗
- **THEN** 系統將任務送入 dead-letter 並記錄告警