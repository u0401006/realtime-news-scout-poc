## 1. Runtime and Infra

- [ ] 1.1 建立 Cloud Run OpenClaw runtime 映像與部署腳本
- [ ] 1.2 建立 skills 掛載與啟動檢查
- [ ] 1.3 建立基本監控與錯誤告警

## 2. LINE Bridge

- [ ] 2.1 實作 `/webhook/line` 驗簽與快速 ACK
- [ ] 2.2 實作事件入列與非同步派工到 OpenClaw
- [ ] 2.3 實作回覆路徑（成功/失敗/排隊中）

## 3. Session and Queue Governance

- [ ] 3.1 實作使用者 ID 到 session 映射策略
- [ ] 3.2 實作每使用者與全域併發上限
- [ ] 3.3 實作重試與 dead-letter 流程

## 4. QA and E2E

- [ ] 4.1 建立 LINE 指令端到端測試案例
- [ ] 4.2 建立壓力與併發測試（多使用者）
- [ ] 4.3 建立驗收報告與回滾流程