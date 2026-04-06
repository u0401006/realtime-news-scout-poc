# Architecture Decision Records (ADR)

記錄 realtime-news-scout-poc 重要的設計決策。

---

## ADR-001: 事件鏈生命週期管理（Event Chain Lifecycle: Promotion & GC）

**日期**: 2026-04-06

**狀態**: accepted

**任務 ID**: BE-EVENT-LIFECYCLE-20260406

**背景**:

`event_state.json` 儲存所有動態事件鏈（chains）的 momentum 與狀態。
原始設計缺乏退場與收斂機制，導致：

1. **死鏈無限累積**：衰退至 momentum ≈ 0 的鏈仍佔用記憶體與 JSON 空間，隨時間增長無上限。
2. **熱鏈無長期記憶**：momentum 達到高峰（Peaking）的重要事件沒有自動升格機制，無法沉澱為 Wiki 長期知識。

**決策**:

實作「孵化 → 升級 → 退場」三階段生命週期：

### 階段定義

| Phase    | momentum 範圍    | 說明              |
|----------|-----------------|-------------------|
| Emerging | 0.2 ≤ m < 0.5   | 初期萌芽，待觀察    |
| Growing  | 0.5 ≤ m < 0.8   | 成長中，持續追蹤    |
| Peaking  | m ≥ 0.8         | 熱點，觸發 Wiki 升格 |
| Fading   | m < 0.2         | 衰退，接近退場      |

### A. Garbage Collection（死鏈物理退場）

- **觸發條件**: `momentum < 0.1`（GC 門檻）
- **執行時機**: `EventStateManager.save()` 呼叫時
- **行為**: 從 `self.state["chains"]` 物理移除，不保留任何記錄
- **實作位置**: `ranking/model/event_state_manager.py`，`_gc()` 方法

**理由**:
選擇在 `save()` 時執行 GC（而非即時移除），確保 GC 與持久化原子發生，
避免 in-memory 狀態與磁碟狀態不一致。

### B. Wiki Promotion（熱鏈自動升格）

- **觸發條件**: `momentum >= 0.8` 且 `promoted_to_wiki == False`
- **流程**:
  1. 呼叫 LLM（gpt-4o-mini）根據 `recent_titles` 生成事件摘要
  2. 寫入 `context/wiki/chains/<事件名>.md`
  3. 更新 `context/wiki/indices/chain_context_map.json` 索引
  4. 將 chain 標記為 `promoted_to_wiki = True`，寫回 `event_state.json`
- **實作位置**: `main-brain/scripts/wiki_compiler.py`，`promote_peaking_chains()` 函數
- **CLI 觸發**: `uv run python scripts/wiki_compiler.py --promote-chains`

**LLM 降級策略**:
若 `OPENAI_API_KEY` 未設定或 openai 套件不存在，改用規則型摘要（fallback）。
確保系統在無 API key 環境下仍可正常運作。

### C. 衰退機制（Decay）

- **觸發條件**: 每輪新聞掃描後，未被觀測到的 chain
- **衰退公式**: `momentum *= 0.85`（DECAY_FACTOR）
- **phase 同步**: 衰退後自動重新計算 phase
- **實作位置**: `EventStateManager.decay_unseen(seen_chain_ids)` 方法

**後果**:

| 面向     | 影響                                                    |
|----------|---------------------------------------------------------|
| 記憶體   | 死鏈自動清除，長期穩定；不再無限增長                       |
| 知識沉澱 | 熱鏈自動轉為 Wiki Markdown，供 UnifiedSnapshot 引用        |
| 可測試性 | GC 與 Promotion 邏輯均有單元測試覆蓋                      |
| 降級安全 | Promotion 在無 LLM 環境下仍可執行（fallback 摘要）         |

**受影響的檔案**:

- `realtime-news-scout-poc/ranking/model/event_state_manager.py`（新建）
- `realtime-news-scout-poc/tests/ranking/test_event_state_manager.py`（新建）
- `main-brain/scripts/wiki_compiler.py`（擴充 promotion 邏輯）
- `main-brain/tests/test_wiki_compiler.py`（擴充 promotion 測試）
- `realtime-news-scout-poc/context/DECISIONS.md`（本文件，新建）

**決策者**: Backend Agent（BE-EVENT-LIFECYCLE-20260406）
