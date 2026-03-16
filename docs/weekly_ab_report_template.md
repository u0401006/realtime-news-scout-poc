# SkillEvo 每週 A/B 評估報告

**週次：** W{XX}-{YYYY}
**評估期間：** {START_DATE} ~ {END_DATE}
**評估者：** QA 自動產生 (`tests/eval/ab_eval.py`)
**Baseline 版本：** v{BASELINE_VERSION}
**Candidate 版本：** v{CANDIDATE_VERSION}
**Top-k：** {K}

---

## 一、指標對比

| 指標 | Baseline | Candidate | 差量 | 判定 |
|------|----------|-----------|------|------|
| 命中率 (Hit Rate) | {B_HIT}% | {C_HIT}% | {D_HIT}% | {ICON_HIT} |
| NDCG@{K} | {B_NDCG}% | {C_NDCG}% | {D_NDCG}% | {ICON_NDCG} |
| 誤報率 (FP Rate) | {B_FP}% | {C_FP}% | {D_FP}% | {ICON_FP} |
| 漏稿率 (Miss Rate) | {B_MISS}% | {C_MISS}% | {D_MISS}% | {ICON_MISS} |

> 判定圖示：✅ 改善 | 🔴 惡化 | ➖ 持平

## 二、樣本統計

| 項目 | Baseline | Candidate |
|------|----------|-----------|
| 模型推薦數 | {B_REC} | {C_REC} |
| 編輯採用數 | {B_ADOPT} | {C_ADOPT} |
| Ground Truth 總決策數 | {GT_TOTAL} | {GT_TOTAL} |
| 評估期間天數 | {DAYS} | {DAYS} |

## 三、門檻判定

### 判定結果：**{ACTION}**

{REASON_LIST}

### 門檻參數

#### 升級條件（全部滿足）

| 參數 | 門檻值 | 實際值 | 滿足 |
|------|--------|--------|------|
| 命中率提升 ≥ 2% | 2.00% | {D_HIT}% | {Y/N} |
| NDCG 提升 ≥ 1% | 1.00% | {D_NDCG}% | {Y/N} |
| 誤報率 ≤ 30% | 30.00% | {C_FP}% | {Y/N} |
| 漏稿率 ≤ 20% | 20.00% | {C_MISS}% | {Y/N} |

#### 回滾條件（任一觸發）

| 參數 | 門檻值 | 實際值 | 觸發 |
|------|--------|--------|------|
| 命中率下降 ≥ 5% | -5.00% | {D_HIT}% | {Y/N} |
| NDCG 下降 ≥ 3% | -3.00% | {D_NDCG}% | {Y/N} |
| 誤報率 ≥ 50% | 50.00% | {C_FP}% | {Y/N} |

## 四、分類別分析

> ⚠️ Phase 2 啟用。目前為彙總指標，後續將按新聞類別分層分析。

| 類別 | Baseline Hit Rate | Candidate Hit Rate | Δ |
|------|-------------------|--------------------|---|
| 政治 | - | - | - |
| 經濟 | - | - | - |
| 國際 | - | - | - |
| 社會 | - | - | - |
| 其他 | - | - | - |

## 五、典型案例分析

### 正面案例（Candidate 勝出）

| # | Article ID | 標題摘要 | Baseline 排名 | Candidate 排名 | 編輯決策 |
|---|-----------|---------|--------------|----------------|---------|
| 1 | - | - | - | - | - |

### 負面案例（Candidate 退步）

| # | Article ID | 標題摘要 | Baseline 排名 | Candidate 排名 | 編輯決策 |
|---|-----------|---------|--------------|----------------|---------|
| 1 | - | - | - | - | - |

## 六、行動建議

{ACTION_ITEMS}

## 七、風險與備註

- [ ] Ground Truth 資料量是否充足（≥ 50 筆）
- [ ] A/B 期間是否有重大突發事件影響基線
- [ ] Check-point 權重上週是否有調整
- [ ] 是否需要人工 override 自動判定

---

**附件：**
- `ranking/snapshots/{BASELINE_VERSION}.jsonl` — Baseline 快照
- `ranking/snapshots/{CANDIDATE_VERSION}.jsonl` — Candidate 快照
- `editor-console/decisions.jsonl` — 編輯決策原始資料

---

*本報告由 `python -m tests.eval.ab_eval` 自動產生。*
*門檻設定參見 `docs/gate_thresholds.yaml`。*
