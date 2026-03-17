"""V1 Headline Scorer — 使用 v1_weights.json 對事件評分

讀取 v1_train.py 產出的權重檔，對 StandardEvent 或任意標題+metadata
進行 headline-worthiness 評分。

v1.2 變更：
  - 降權「預測/分析類」（若、恐、可能、分析、調查）
  - 升權「公安/死傷」（氨氣、外洩、勒令停用、相驗、1死 etc.）
  - 國際衝突高價值 signal（扣押、報復、海纜、一帶一路）不觸發泛國際降權

使用方式：
    from ranking.model.v1_scorer import V1Scorer

    scorer = V1Scorer()  # 自動載入 v1_weights.json
    result = scorer.score(title="...", topic_tags=["國際"], region_tags=["台灣"])
"""

from __future__ import annotations

import enum
import json
import logging
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Dict, List, Optional

logger = logging.getLogger(__name__)

_DEFAULT_WEIGHTS_PATH = Path(__file__).parent / "v1_weights.json"

# ─── 預測/分析類降權關鍵字 ───
_SPECULATIVE_KEYWORDS: List[str] = [
    "若", "恐", "可能", "分析", "調查", "預測", "預估", "推測",
    "觀察", "展望", "研判", "或將",
]

# ─── 重大實體發言人（命中時不降權預測類） ───
_MAJOR_SPEAKER_ENTITIES: List[str] = [
    "川普", "拜登", "習近平", "普丁", "澤倫斯基", "賴清德",
    "WHO", "NATO", "央行", "Fed", "聯準會",
]

# ─── 公安/死傷突發事件加權關鍵字 ───
_PUBLIC_SAFETY_KEYWORDS: List[str] = [
    "氨氣", "外洩", "勒令停用", "相驗", "中毒", "氣爆",
    "瓦斯", "工安", "爆炸", "塌陷", "崩塌", "倒塌",
]
_CASUALTY_PATTERN = re.compile(r"\d+死|\d+傷|死亡|罹難|喪生|身亡|殉職")

# ─── 國際衝突高價值 signal（不應觸發泛國際降權） ───
_INTL_CONFLICT_KEYWORDS: List[str] = [
    "扣押", "報復", "海纜", "一帶一路", "制裁", "封鎖",
    "斷交", "驅逐", "間諜", "滲透", "人質", "挾持",
    "禁運", "凍結資產", "脫鉤", "貿易戰", "關稅戰",
]


# ─── 內容型態分類 (Content Tier) ───

class ContentTier(enum.Enum):
    """內容型態層級，優先序由高到低。"""
    P0_short = 0      # 快訊/速報（≤30 字標題、含「快訊」「速報」「Breaking」）
    P0_main = 1       # 主體報導（首發事實報導）
    P1_followup = 2   # 後續追蹤（含「最新」「更新」「追蹤」等）
    P2_response = 3   # 回應/反應（含「回應」「表態」「聲明」「譴責」等）
    P3_analysis = 4   # 分析/評論（含「分析」「觀察」「展望」「預測」等）


# tier 判定關鍵字
_P0_SHORT_KEYWORDS: List[str] = [
    "快訊", "速報", "Breaking", "BREAKING", "最新快訊",
]
_P0_SHORT_MAX_TITLE_LEN: int = 15

_P1_FOLLOWUP_KEYWORDS: List[str] = [
    "最新", "更新", "追蹤", "後續", "續報", "再傳", "又見",
    "持續", "滾動", "進展",
]

_P2_RESPONSE_KEYWORDS: List[str] = [
    "回應", "表態", "聲明", "譴責", "抗議", "駁斥", "反擊",
    "反駁", "澄清", "否認", "遺憾", "慰問", "致哀",
]

_P3_ANALYSIS_KEYWORDS: List[str] = [
    "分析", "觀察", "展望", "預測", "預估", "推測",
    "研判", "評論", "社論", "專欄", "解讀", "深度",
    "調查報導", "盤點", "懶人包",
]


def classify_content_tier(title: str, summary_text: str = "") -> tuple[ContentTier, str]:
    """判定新聞內容型態層級。

    判定優先序：
    1. P0_short 快訊關鍵字（最優先）
    2. P3_analysis 分析/評論關鍵字
    3. P2_response 回應/反應關鍵字
    4. P1_followup 後續追蹤關鍵字
    5. P0_short 極短標題 fallback（≤15 字，僅限無其他標記時）
    6. P0_main 主體報導（預設）

    Args:
        title: 新聞標題。
        summary_text: 摘要文字。

    Returns:
        (ContentTier, tier_reason) 元組。
    """
    text = title + " " + summary_text
    title_len = len(title.strip())

    # P0_short：快訊關鍵字（最優先）
    for kw in _P0_SHORT_KEYWORDS:
        if kw in text:
            return ContentTier.P0_short, f"快訊關鍵字「{kw}」"

    # P3_analysis：分析/評論（先判斷，避免被 P1/P2 搶走）
    for kw in _P3_ANALYSIS_KEYWORDS:
        if kw in text:
            return ContentTier.P3_analysis, f"分析評論關鍵字「{kw}」"

    # P2_response：回應/反應
    for kw in _P2_RESPONSE_KEYWORDS:
        if kw in text:
            return ContentTier.P2_response, f"回應反應關鍵字「{kw}」"

    # P1_followup：後續追蹤
    for kw in _P1_FOLLOWUP_KEYWORDS:
        if kw in text:
            return ContentTier.P1_followup, f"後續追蹤關鍵字「{kw}」"

    # P0_short fallback：極短標題（≤15 字，CJK 標題較短即為速報）
    if 0 < title_len <= _P0_SHORT_MAX_TITLE_LEN:
        return ContentTier.P0_short, f"極短標題（{title_len}字≤{_P0_SHORT_MAX_TITLE_LEN}）"

    # 預設 P0_main：主體報導
    return ContentTier.P0_main, "主體報導（無特殊標記）"


@dataclass
class ScoreResult:
    """評分結果。"""
    total_score: float
    headline_eligible: bool
    headline_reason: str
    breakdown: Dict[str, float]
    matched_rules: List[str]
    is_generic_international: bool
    content_tier: ContentTier = ContentTier.P0_main
    tier_reason: str = ""


class V1Scorer:
    """基於 v1_weights.json 的 keyword-based scorer。"""

    def __init__(self, weights_path: Optional[str] = None) -> None:
        path = Path(weights_path) if weights_path else _DEFAULT_WEIGHTS_PATH
        if not path.exists():
            raise FileNotFoundError(
                f"Weights file not found: {path}. Run v1_train.py first."
            )
        with open(path, encoding="utf-8") as f:
            self._w: Dict[str, Any] = json.load(f)

        self._feature_rules: List[Dict[str, Any]] = self._w.get("feature_rules", [])
        self._base_score: float = self._w.get("base_score", 50.0)
        self._headline_threshold: float = self._w.get("headline_threshold", 72.0)
        self._strong_reject: float = self._w.get("strong_reject_threshold", 40.0)
        self._generic_intl_penalty: float = self._w.get("generic_international_penalty", -25.0)
        self._generic_intl_kw: List[str] = self._w.get("generic_international_keywords", [])
        self._timeliness_weight: float = self._w.get("timeliness_weight", 0.3)
        self._credibility_weight: float = self._w.get("credibility_weight", 0.1)
        self._ip_entities: List[str] = self._w.get("ip_entities", [])
        self._ip_entity_boost: float = self._w.get("ip_entity_boost", 20.0)

        # 初始化 HeadlineSelector（CP-IP 引擎）
        try:
            from ranking.headline_selection import HeadlineSelector
            self._ip_selector = HeadlineSelector()
        except Exception:
            self._ip_selector = None
            logger.warning("HeadlineSelector unavailable, IP entity boost disabled")

        logger.info(
            "V1Scorer loaded: threshold=%.1f, rules=%d, generic_intl_kw=%d, ip_entities=%d",
            self._headline_threshold,
            len(self._feature_rules),
            len(self._generic_intl_kw),
            len(self._ip_entities),
        )

    @property
    def headline_threshold(self) -> float:
        return self._headline_threshold

    # ─── 預測/分析類降權 ───

    @staticmethod
    def _is_speculative(text: str) -> bool:
        """判斷文本是否為預測/分析類內容。"""
        return any(kw in text for kw in _SPECULATIVE_KEYWORDS)

    @staticmethod
    def _has_major_speaker(text: str) -> bool:
        """判斷文本是否包含重大實體發言人（命中時不降權）。"""
        return any(speaker in text for speaker in _MAJOR_SPEAKER_ENTITIES)

    # ─── 公安/死傷加權 ───

    @staticmethod
    def _public_safety_boost(text: str) -> float:
        """計算公安/死傷突發事件加權分數。

        Returns:
            加權分數（0 表示不匹配）。
        """
        safety_hits = [kw for kw in _PUBLIC_SAFETY_KEYWORDS if kw in text]
        casualty_match = _CASUALTY_PATTERN.search(text)

        if not safety_hits and not casualty_match:
            return 0.0

        boost = 0.0
        # 公安關鍵字命中
        if safety_hits:
            boost += 15.0 + min(len(safety_hits) - 1, 3) * 5.0
        # 死傷數字命中
        if casualty_match:
            boost += 15.0
        # 雙重命中（公安 + 死傷）→ 額外加成
        if safety_hits and casualty_match:
            boost += 10.0

        return min(boost, 45.0)

    # ─── 國際衝突 signal ───

    @staticmethod
    def _has_intl_conflict_signal(text: str) -> bool:
        """判斷文本是否包含國際衝突高價值 signal。"""
        return any(kw in text for kw in _INTL_CONFLICT_KEYWORDS)

    def score(
        self,
        title: str,
        topic_tags: Optional[List[str]] = None,
        region_tags: Optional[List[str]] = None,
        timeliness: int = 50,
        credibility: int = 90,
        summary_text: str = "",
    ) -> ScoreResult:
        """對一則新聞進行 headline-worthiness 評分。

        Args:
            title: 新聞標題。
            topic_tags: 主題標籤。
            region_tags: 地區標籤。
            timeliness: 時效分數 0-100。
            credibility: 可信度分數 0-100。
            summary_text: 摘要文字（輔助比對）。

        Returns:
            ScoreResult 包含總分與評分明細。
        """
        text = title + " " + summary_text
        topic_tags = topic_tags or []
        region_tags = region_tags or []

        score = self._base_score
        breakdown: Dict[str, float] = {"base": self._base_score}
        matched_rules: List[str] = []

        # ── 特徵規則比對 ──
        for rule in self._feature_rules:
            rule_keywords: List[str] = rule.get("keywords", [])
            rule_weight: float = rule.get("weight", 0.0)
            rule_name: str = rule.get("name", "unknown")

            matched_kw = [kw for kw in rule_keywords if kw in text]
            if matched_kw:
                # 多重命中加成（最多 x2）
                hit_multiplier = min(len(matched_kw), 3) / 2.0
                contribution = rule_weight * 5.0 * hit_multiplier
                score += contribution
                breakdown[rule_name] = contribution
                matched_rules.append(f"{rule_name}({','.join(matched_kw[:3])})")

        # ── IP 實體加成（CP-IP 引擎） ──
        if self._ip_selector is not None:
            ip_result = self._ip_selector.match(title=title, summary=summary_text)
            if ip_result.matched:
                score += ip_result.boost_score
                breakdown["ip_entity_boost"] = ip_result.boost_score
                matched_rules.append(
                    f"IP實體({','.join(ip_result.matched_entities[:3])})"
                )

        # ── [v1.2] 預測/分析類降權 ──
        is_speculative = self._is_speculative(text)
        if is_speculative and not self._has_major_speaker(text):
            spec_penalty = -20.0
            score += spec_penalty
            breakdown["speculative_penalty"] = spec_penalty
            matched_rules.append("預測分析降權")

        # ── [v1.2] 公安/死傷突發加權 ──
        safety_boost = self._public_safety_boost(text)
        if safety_boost > 0:
            score += safety_boost
            breakdown["public_safety_boost"] = safety_boost
            matched_rules.append(f"公安死傷加權(+{safety_boost:.0f})")

        # ── [v1.2] 國際衝突 signal ──
        has_conflict_signal = self._has_intl_conflict_signal(text)
        if has_conflict_signal:
            conflict_boost = 15.0
            score += conflict_boost
            breakdown["intl_conflict_boost"] = conflict_boost
            matched_rules.append("國際衝突signal")

        # ── 泛國際抑制 ──
        is_generic_intl = False
        generic_hits = [kw for kw in self._generic_intl_kw if kw in text]
        has_strong_positive = any(
            rule.get("weight", 0) > 0 and
            any(kw in text for kw in rule.get("keywords", []))
            for rule in self._feature_rules
        )

        if generic_hits and not has_strong_positive:
            # [v1.2] 國際衝突 signal 命中時，跳過泛國際降權
            if has_conflict_signal:
                matched_rules.append("泛國際抑制-跳過(衝突signal)")
            elif "國際" in topic_tags and len(topic_tags) <= 2:
                is_generic_intl = True
                penalty = self._generic_intl_penalty
                score += penalty
                breakdown["generic_intl_penalty"] = penalty
                matched_rules.append(f"泛國際抑制({','.join(generic_hits[:3])})")

        # ── 時效加成 ──
        timeliness_bonus = timeliness * self._timeliness_weight * 0.3
        score += timeliness_bonus
        breakdown["timeliness"] = timeliness_bonus

        # ── 可信度加成 ──
        cred_bonus = credibility * self._credibility_weight * 0.1
        score += cred_bonus
        breakdown["credibility"] = cred_bonus

        # ── 台灣相關加成 ──
        if "台灣" in region_tags or "台北" in region_tags:
            taiwan_bonus = 5.0
            score += taiwan_bonus
            breakdown["taiwan_boost"] = taiwan_bonus

        # Clamp
        total = max(0.0, min(100.0, round(score, 1)))

        # 判定
        eligible = total >= self._headline_threshold
        if eligible:
            reason = f"score={total:.1f} >= threshold={self._headline_threshold}"
        elif is_generic_intl:
            reason = f"泛國際抑制: score={total:.1f}, hits={generic_hits[:3]}"
        elif is_speculative:
            reason = f"預測分析降權: score={total:.1f}"
        else:
            reason = f"score={total:.1f} < threshold={self._headline_threshold}"

        # ── 內容型態分類 ──
        content_tier, tier_reason = classify_content_tier(title, summary_text)

        return ScoreResult(
            total_score=total,
            headline_eligible=eligible,
            headline_reason=reason,
            breakdown=breakdown,
            matched_rules=matched_rules,
            is_generic_international=is_generic_intl,
            content_tier=content_tier,
            tier_reason=tier_reason,
        )
