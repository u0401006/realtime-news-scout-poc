## Why

需求已確定改為「OpenClaw 編排 + 執行 + LINE 作入口」，不再依賴 Claude Code。需要一個獨立新專案承接 Phase 1，建立可併發、可擴充、可監控的雲端任務處理鏈路。

## What Changes

- 建立 OpenClaw 雲端 runtime（Cloud Run）與 skills 掛載基線。
- 建立 linebot ingress 到 OpenClaw 的事件橋接 endpoint（快速 ACK + 非同步派工）。
- 建立 LINE 端到端驗證流程（指令→派工→回覆）。
- 建立佇列與 session 對應策略（不同使用者隔離 session、併發控制、重試）。

## Capabilities

### New Capabilities
- `openclaw-cloud-runtime`: OpenClaw 服務化部署、環境設定、技能掛載。
- `line-event-bridge`: LINE webhook 事件橋接到 OpenClaw 任務流。
- `session-queue-governance`: 多使用者 session 隔離、併發限制、重試與 dead-letter 策略。
- `line-e2e-verification`: LINE 指令到回覆的端到端驗收流程。

### Modified Capabilities
- 無

## Impact

- 主要影響：`deploy/cloudrun/*`, `bridge/*`, `skills/*`, `tests/e2e/*`, `.github/workflows/*`。
- 依賴：Google Cloud Run、LINE Messaging API、OpenClaw runtime。
- 風險：尖峰流量下任務排隊與延遲、Webhook 重送、session 汙染。