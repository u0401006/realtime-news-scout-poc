## ADDED Requirements

### Requirement: Content Tier Classification
系統 MUST 為每則候選新聞輸出 `content_tier`，值為 `P0_short`, `P0_main`, `P1_followup`, `P2_response`, `P3_analysis` 之一。

#### Scenario: P0 短稿判定
- **WHEN** 內容篇幅短且具快訊/緊急/重大/時效信號
- **THEN** `content_tier` = `P0_short`

#### Scenario: P0 主稿判定
- **WHEN** 內容補足主事件核心事實與脈絡
- **THEN** `content_tier` = `P0_main`

#### Scenario: P1 後續判定
- **WHEN** 內容描述主事件後續發展
- **THEN** `content_tier` = `P1_followup`

#### Scenario: P2 回應稿判定
- **WHEN** 內容主要為相關人物/單位/團體的回覆、補充或批評
- **THEN** `content_tier` = `P2_response`

#### Scenario: P3 分析稿判定
- **WHEN** 內容主要為次級研調單位、專家學者的評論評價
- **THEN** `content_tier` = `P3_analysis`

### Requirement: Tier-aware Tie Break
系統 MUST 在分數相同時依 tier 優先序排序：`P0_short > P0_main > P1_followup > P2_response > P3_analysis`。

#### Scenario: 同分排序
- **WHEN** 兩則新聞 score 相同
- **THEN** tier 優先序較高者排名較前

### Requirement: Tier Explainability
系統 MUST 輸出 `tier_reason` 說明判定依據。

#### Scenario: 輸出可審核理由
- **WHEN** 產出候選清單
- **THEN** 每則含 `tier_reason` 供編輯人工審核