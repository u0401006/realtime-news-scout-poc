## MAV-Watchtower Guard Tasks

- [ ] [P0] **MAV-Watchtower 基座建立**
  - [ ] 撰寫 `ranking/mav_verifier.py`
  - [ ] 實作三模型 (GPT-4o, Gemini, Llama) 的非同步呼叫。
  - [ ] 定義 `MAV_Decision` 模型：`is_eligible: bool`, `reason: str`, `confidence: float`。

- [ ] [P0] **Scorer 鉤子整合**
  - [ ] 修改 `v2_scorer.py` 引入 `MAVVerifier`。
  - [ ] 在 `score()` 結尾處針對高分新聞觸發 `verify()`。
  - [ ] 更新 `V2ScoreResult` 包含 `mav_decision` 欄位。

- [ ] [P1] **語言天條防火牆**
  - [ ] 整合 MAV 進行摘要輸出前的「台灣用語」二度校閱。
  - [ ] 建立自動「簡轉繁/用語替換」後置處理 (Post-processor)。

- [ ] [P1] **煙霧測試與校準**
  - [ ] 使用今日 2026-03-17 的樣本進行 MAV 聯測。
  - [ ] 驗證「台橡公關稿」是否被 MAV 成功 Veto。
