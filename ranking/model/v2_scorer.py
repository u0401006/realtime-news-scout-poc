"""V2 Headline Scorer - 浮動門檻 + 劇烈震盪偵測 + gTrend 動態加分

v2.0 變更（基於 v1.2）：
  1. 浮動門檻：根據近期事件分數分佈動態調整門檻值
  2. 劇烈震盪判定：偵測分數群聚急劇變化，啟動保護機制
  3. 核心 IP 精準化：exact match 嚴格模式 + 衰減機制
  4. gTrend CSV 整合：利用 Google Trends 數據進行動態加分
  5. cp-economic：經濟新聞劇烈震盪量化判定（漲跌幅 > 2%）
  6. cp-ip 關鍵動作連動：IP 實體 + 關鍵動作組合加成
  7. Firebase 路徑整合：串接 AppDev 推送的即時數據

使用方式：
    from ranking.model.v2_scorer import V2Scorer

    scorer = V2Scorer()
    result = scorer.score(title="...", topic_tags=["國際"], region_tags=["台灣"])
"""

from __future__ import annotations

import logging
import math
import re
import statistics
from collections import deque
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any, Deque, Dict, List, Optional, Tuple

from ranking.economic_detector import EconomicDetector, EconomicShockResult
from ranking.firebase_loader import FirebaseLoader
from ranking.gtrend_loader import GTrendLoader
from ranking.headline_selection import HeadlineSelector
from ranking.model.v1_scorer import (
    ContentTier,
    ScoreResult,
    V1Scorer,
    _CASUALTY_PATTERN,
    _INTL_CONFLICT_KEYWORDS,
    _MAJOR_SPEAKER_ENTITIES,
    _PUBLIC_SAFETY_KEYWORDS,
    _SPECULATIVE_KEYWORDS,
    classify_content_tier,
)

logger = logging.getLogger(__name__)

# ─── 5% 天條產出控制 ───
_DEFAULT_BASE_THRESHOLD: float = 90.0
_PERCENTILE_TARGET: float = 95.0
_THRESHOLD_MIN: float = 90.0
_THRESHOLD_MAX: float = 98.0
_WINDOW_SIZE: int = 50

# ─── 劇烈震盪判定參數 ───
_VOLATILITY_WINDOW: int = 20  # 震盪偵測視窗
_VOLATILITY_STDDEV_THRESHOLD: float = 15.0  # 標準差超過此值 → 判定為劇烈震盪
_VOLATILITY_RANGE_THRESHOLD: float = 40.0  # 最大最小差超過此值 → 判定
_SHOCK_DAMPENING: float = 0.6  # 震盪時門檻調整幅度降低比率

# ─── IP 精準化參數 ───
_IP_SHORT_KEYWORD_MIN_LEN: int = 2  # 長度 ≤2 的 IP 關鍵字需嚴格匹配
_IP_BOUNDARY_CHARS = r"[\s，。、；：！？「」（）\[\]【】《》\-/,.]"

# ─── 內容型態權重調整 (Tier-based weights) ───
_TIER_MULTIPLIERS: Dict[ContentTier, float] = {
    ContentTier.P0_short: 1.2,    # 快訊加成
    ContentTier.P0_main: 1.0,     # 標準
    ContentTier.P1_followup: 0.95, # 追蹤/新進度 (從 0.9 提高，這包含駁回/裁定等關鍵進度)
    ContentTier.P2_response: 0.7, # 回應/表態降權
    ContentTier.P3_analysis: 0.6, # 分析/預測大幅降權
}

# ─── TSMC 分層權重 ───
_TSMC_TIER_1 = ["魏哲家", "業績", "法說", "重訊", "2奈米", "1.4奈米", "設廠", "技術突破", "擴廠"]
_TSMC_TIER_2 = ["張忠謀", "三星", "Intel", "英特爾", "ASML", "艾司摩爾", "上下游", "供應鏈"]
_TSMC_TIER_3 = ["劉德音", "夥伴", "合作"]
_TSMC_TIER_4 = ["曾繁城", "蔣尚義", "梁孟松", "孫元成", "前幹部", "前高層"]

# ─── 政治核心加權 ───
_POLITICAL_CORE = ["柯文哲", "境管", "民眾黨", "黃國昌"]


@dataclass
class VolatilityState:
    """震盪狀態快照。"""
    is_volatile: bool
    stddev: float
    score_range: float
    window_mean: float
    window_size: int
    dampening_active: bool


@dataclass
class FloatingThreshold:
    """浮動門檻計算結果。"""
    effective_threshold: float
    base_threshold: float
    adjustment: float
    reason: str
    volatility: VolatilityState


@dataclass
class V2ScoreResult(ScoreResult):
    """V2 評分結果，擴充浮動門檻與 gTrend 資訊。"""
    effective_threshold: float = _DEFAULT_BASE_THRESHOLD
    threshold_adjustment: float = 0.0
    threshold_reason: str = ""
    gtrend_boost: float = 0.0
    gtrend_keywords: List[str] = field(default_factory=list)
    volatility_state: Optional[VolatilityState] = None
    ip_strict_matches: List[str] = field(default_factory=list)
    # cp-economic
    economic_shock: Optional[EconomicShockResult] = None
    economic_boost: float = 0.0
    # cp-ip 關鍵動作
    ip_key_actions: List[str] = field(default_factory=list)
    ip_action_boost: float = 0.0
    # Firebase
    firebase_boost: float = 0.0
    firebase_matched_ids: List[str] = field(default_factory=list)


class V2Scorer:
    """V2 浮動門檻 Scorer。

    基於 V1Scorer 的評分邏輯，新增：
    - 滑動視窗浮動門檻
    - 劇烈震盪偵測與保護
    - gTrend 動態加分
    - IP 實體精準匹配
    """

    def __init__(
        self,
        weights_path: Optional[str] = None,
        gtrend_csv: Optional[str] = None,
        gtrend_dir: Optional[str] = None,
        base_threshold: Optional[float] = None,
        window_size: int = _WINDOW_SIZE,
        firebase_project_id: Optional[str] = None,
        firebase_cache: Optional[str] = None,
        firebase_service_account: Optional[str] = None,
    ) -> None:
        """初始化 V2Scorer。

        Args:
            weights_path: v1_weights.json 路徑（繼承 V1 規則）。
            gtrend_csv: gTrend CSV 檔案路徑。
            gtrend_dir: gTrend CSV 目錄。
            base_threshold: 基礎門檻（覆蓋 v1_weights.json 設定）。
            window_size: 滑動視窗大小。
            firebase_project_id: GCP 專案 ID (e.g. "medialab-356306")。
            firebase_cache: Firebase 本地 JSON 快取路徑。
            firebase_service_account: 用於 impersonation 的 SA email（可選）。
        """
        # 繼承 V1 Scorer
        self._v1 = V1Scorer(weights_path=weights_path)
        self._base_threshold = base_threshold or self._v1.headline_threshold

        # 滑動視窗
        self._window_size = window_size
        self._score_history: Deque[float] = deque(maxlen=window_size)
        self._volatility_history: Deque[float] = deque(maxlen=_VOLATILITY_WINDOW)

        # gTrend Loader
        self._gtrend: Optional[GTrendLoader] = None
        if gtrend_csv or gtrend_dir:
            self._gtrend = GTrendLoader(
                csv_path=gtrend_csv,
                csv_dir=gtrend_dir,
            )
            logger.info(
                "V2Scorer: gTrend loaded, %d keywords",
                self._gtrend.keyword_count,
            )

        # IP Selector（精準化版）
        try:
            self._ip_selector = HeadlineSelector()
        except Exception:
            self._ip_selector = None
            logger.warning("HeadlineSelector unavailable in V2Scorer")

        # CP-Economic 偵測器
        self._economic_detector = EconomicDetector()

        # Firebase Loader
        self._firebase: Optional[FirebaseLoader] = None
        if firebase_project_id or firebase_cache:
            try:
                self._firebase = FirebaseLoader(
                    project_id=firebase_project_id,
                    cache_path=firebase_cache,
                    service_account=firebase_service_account,
                )
                logger.info(
                    "V2Scorer: Firebase loaded, %d trending items",
                    self._firebase.trending_count,
                )
            except Exception as e:
                logger.warning("FirebaseLoader init failed: %s", e)
                self._firebase = None

        logger.info(
            "V2Scorer initialized: base_threshold=%.1f, window=%d, "
            "gtrend=%s, economic=enabled, firebase=%s",
            self._base_threshold,
            self._window_size,
            "enabled" if self._gtrend else "disabled",
            "enabled" if self._firebase else "disabled",
        )

    @property
    def base_threshold(self) -> float:
        """基礎門檻值。"""
        return self._base_threshold

    @property
    def score_history_size(self) -> int:
        """目前滑動視窗中的分數數量。"""
        return len(self._score_history)

    # ─── 浮動門檻計算 ───

    def compute_floating_threshold(self) -> FloatingThreshold:
        """根據近期分數分佈計算浮動門檻。

        邏輯：
        1. 視窗不足（<10 筆）→ 使用基礎門檻
        2. 計算 P75 分位數作為參考基準
        3. 若 P75 > base → 門檻上調（高品質時段，提高標準）
        4. 若 P75 < base → 門檻下調（低品質時段，放寬標準）
        5. 偵測到劇烈震盪 → 啟動 dampening，減少調整幅度
        6. Clamp 在 [_THRESHOLD_MIN, _THRESHOLD_MAX]

        Returns:
            FloatingThreshold 計算結果。
        """
        volatility = self._detect_volatility()

        # 視窗不足，直接回傳基礎門檻
        if len(self._score_history) < 10:
            return FloatingThreshold(
                effective_threshold=self._base_threshold,
                base_threshold=self._base_threshold,
                adjustment=0.0,
                reason=f"視窗不足({len(self._score_history)}<10)，使用基礎門檻",
                volatility=volatility,
            )

        scores = list(self._score_history)
        p75 = self._percentile(scores, _PERCENTILE_TARGET)
        mean = statistics.mean(scores)

        # 計算調整量
        # P75 和基礎門檻的差異，乘以衰減係數
        raw_adjustment = (p75 - self._base_threshold) * 0.3

        # 震盪保護
        if volatility.is_volatile:
            raw_adjustment *= _SHOCK_DAMPENING
            reason = f"震盪保護(σ={volatility.stddev:.1f})，調整幅度降低"
        elif raw_adjustment > 0:
            reason = f"高品質時段(P75={p75:.1f})，門檻上調"
        elif raw_adjustment < 0:
            reason = f"低品質時段(P75={p75:.1f})，門檻下調"
        else:
            reason = "門檻穩定"

        # Clamp
        effective = self._base_threshold + raw_adjustment
        effective = max(_THRESHOLD_MIN, min(_THRESHOLD_MAX, round(effective, 1)))

        return FloatingThreshold(
            effective_threshold=effective,
            base_threshold=self._base_threshold,
            adjustment=round(effective - self._base_threshold, 1),
            reason=reason,
            volatility=volatility,
        )

    # ─── 劇烈震盪偵測 ───

    def _detect_volatility(self) -> VolatilityState:
        """偵測近期分數是否存在劇烈震盪。

        判定條件（滿足任一）：
        1. 近 N 筆分數的標準差 > _VOLATILITY_STDDEV_THRESHOLD
        2. 近 N 筆分數的 max-min > _VOLATILITY_RANGE_THRESHOLD

        Returns:
            VolatilityState 震盪狀態。
        """
        if len(self._volatility_history) < 5:
            return VolatilityState(
                is_volatile=False,
                stddev=0.0,
                score_range=0.0,
                window_mean=0.0,
                window_size=len(self._volatility_history),
                dampening_active=False,
            )

        scores = list(self._volatility_history)
        stddev = statistics.stdev(scores)
        score_range = max(scores) - min(scores)
        mean = statistics.mean(scores)

        is_volatile = (
            stddev > _VOLATILITY_STDDEV_THRESHOLD
            or score_range > _VOLATILITY_RANGE_THRESHOLD
        )

        return VolatilityState(
            is_volatile=is_volatile,
            stddev=round(stddev, 2),
            score_range=round(score_range, 1),
            window_mean=round(mean, 1),
            window_size=len(scores),
            dampening_active=is_volatile,
        )

    # ─── IP 精準匹配 ───

    def _strict_ip_match(self, text: str) -> Tuple[List[str], float]:
        """嚴格 IP 實體匹配。

        對於長度 ≤2 的短關鍵字（如 AI、UN），要求前後有分隔符，
        避免部分匹配（如 「MAIN」 誤命中 「AI」）。
        如果是中文字詞則不限制邊界匹配（因為中文通常無空格）。

        長關鍵字（>2）沿用 substring match。

        Args:
            text: 要比對的文本。

        Returns:
            (matched_entities, total_boost) 元組。
        """
        if self._ip_selector is None:
            return [], 0.0

        all_entities = self._ip_selector.all_entities
        matched: List[str] = []

        for entity in all_entities:
            # 判斷是否為純英數/西文字元
            is_alphanumeric = re.match(r"^[A-Za-z0-9]+$", entity) is not None

            if is_alphanumeric and len(entity) <= _IP_SHORT_KEYWORD_MIN_LEN:
                # 嚴格邊界匹配：針對短英文實體，前後必須是邊界字元或字串開頭/結尾
                pattern = rf"(?:^|{_IP_BOUNDARY_CHARS})({re.escape(entity)})(?:$|{_IP_BOUNDARY_CHARS})"
                if re.search(pattern, text):
                    matched.append(entity)
            else:
                # 一般 substring match (長關鍵字或中文字詞)
                if entity in text:
                    matched.append(entity)

        if not matched:
            return [], 0.0

        # 加權：第一個 full boost，後續遞減
        boost = 0.0
        entity_boost = self._ip_selector.entity_boost
        for i in range(len(matched)):
            boost += entity_boost * (1.0 / (1.0 + i * 0.3))
        boost = round(min(boost, 50.0), 1)

        return matched, boost

    # ─── CP-IP 關鍵動作連動 ───

    def _ip_key_action_combo(
        self, text: str, ip_entities: List[str],
    ) -> Tuple[List[str], float]:
        """偵測 IP 實體是否伴隨關鍵動作，給予組合加成。

        當核心 IP 實體（如「澤倫斯基」）與關鍵動作（如「簽署」）
        同時出現時，代表具有高新聞價值的事實行為，給予額外加分。

        Args:
            text: 標題+摘要文本。
            ip_entities: 已匹配到的 IP 實體列表。

        Returns:
            (matched_actions, combo_boost) 元組。
        """
        if not ip_entities:
            return [], 0.0

        matched_actions: List[str] = []
        for _category, actions in _KEY_ACTIONS.items():
            for action in actions:
                if action in text:
                    matched_actions.append(action)

        if not matched_actions:
            return [], 0.0

        # 加分：IP 數量 × 動作數量，遞減
        ip_factor = min(len(ip_entities), 3)
        action_factor = min(len(matched_actions), 3)
        combo_boost = _KEY_ACTION_COMBO_BOOST * (
            1.0 + (ip_factor - 1) * 0.2 + (action_factor - 1) * 0.15
        )
        combo_boost = round(min(combo_boost, 25.0), 1)

        return matched_actions, combo_boost

    # ─── CP-Economic 經濟震盪偵測 ───

    def _compute_economic_boost(self, text: str) -> Tuple[EconomicShockResult, float]:
        """計算經濟劇烈震盪加分。

        Args:
            text: 標題+摘要文本。

        Returns:
            (shock_result, boost) 元組。
        """
        result = self._economic_detector.detect(text)
        return result, result.boost

    # ─── Firebase 動態加分 ───

    def _compute_firebase_boost(self, title: str) -> Tuple[float, List[str]]:
        """計算 Firebase trending 加分。

        Args:
            title: 新聞標題。

        Returns:
            (boost, matched_ids) 元組。
        """
        if self._firebase is None:
            return 0.0, []
        return self._firebase.get_trending_boost(title)

    # ─── gTrend 動態加分 ───

    def _compute_gtrend_boost(self, text: str) -> Tuple[float, List[str]]:
        """計算 gTrend 動態加分。

        Args:
            text: 標題+摘要文本。

        Returns:
            (boost, matched_keywords) 元組。
        """
        if self._gtrend is None:
            return 0.0, []
        return self._gtrend.compute_text_boost(text, cap=25.0)

    # ─── 主評分方法 ───

    def score(
        self,
        title: str,
        topic_tags: Optional[List[str]] = None,
        region_tags: Optional[List[str]] = None,
        timeliness: int = 50,
        credibility: int = 90,
        summary_text: str = "",
    ) -> V2ScoreResult:
        """對一則新聞進行 V2 headline-worthiness 評分。"""
        text = title + " " + summary_text
        topic_tags = topic_tags or []
        region_tags = region_tags or []

        # ── Step 0: 內容型態分類 ──
        content_tier, tier_reason = classify_content_tier(title, summary_text)

        # ── Step 1: V1 基礎評分 ──
        v1_result = self._v1.score(
            title=title,
            topic_tags=topic_tags,
            region_tags=region_tags,
            timeliness=timeliness,
            credibility=credibility,
            summary_text=summary_text,
        )

        # 以 V1 result 為基礎
        breakdown = dict(v1_result.breakdown)
        matched_rules = list(v1_result.matched_rules)

        # ─── 修正 V1 繼承導致的分數通膨 (v2.16) ───
        # V1 的 total_score 包含了過高的底分 (50.0)，我們在這裡強行修正。
        # 移除 V1 的底分，改用 V2 的基準底分 30.0
        v1_base = breakdown.get("base", 50.0)
        score = v1_result.total_score - v1_base + 30.0
        breakdown["base"] = 30.0 # 鎖死底分為 30.0
        
        # ── Step 2: TSMC 分層 IP 匹配（替換 V1 的寬鬆匹配） ──
        ip_boost = 0.0
        ip_matches = []

        # 柯文哲/政治核心特殊處理
        for kw in _POLITICAL_CORE:
            if kw in title:
                ip_boost += 25.0
                ip_matches.append(kw)

        # TSMC 分層
        if "台積電" in title or "TSMC" in title:
            # Layer 1
            if any(kw in text for kw in _TSMC_TIER_1):
                ip_boost += 35.0
                ip_matches.append("TSMC-L1")
            # Layer 2
            elif any(kw in text for kw in _TSMC_TIER_2):
                ip_boost += 25.0
                ip_matches.append("TSMC-L2")
            # Layer 4 (曾繁城等)
            elif any(kw in text for kw in _TSMC_TIER_4):
                ip_boost += 10.0
                ip_matches.append("TSMC-L4")
            else:
                ip_boost += 15.0
                ip_matches.append("TSMC-L3")

        # 其他精準 IP
        strict_ip_entities, strict_ip_boost = self._strict_ip_match(text)
        # 移除重複的 TSMC 相關命中
        strict_ip_boost = sum(20.0 for e in strict_ip_entities if e not in ["台積電", "TSMC", "柯文哲", "曾繁城"])
        ip_boost += strict_ip_boost
        ip_matches.extend(strict_ip_entities)

        # 複寫 V1 的 IP boost
        if "ip_entity_boost" in breakdown:
            score = score - breakdown["ip_entity_boost"] + ip_boost
        else:
            score += ip_boost
        breakdown["ip_boost_v2_1"] = ip_boost
        matched_rules.append(f"分層IP({','.join(set(ip_matches))})")

        # ── Step 3: gTrend 與其他加分 ──
        # 載入動態趨勢數據
        if self._gtrend:
             self._gtrend.load_csv("/Users/capo_mac_mini/.openclaw/agents/main/google_trend_12hr.csv")
        if self._firebase:
             self._firebase.load_cache("/Users/capo_mac_mini/.openclaw/agents/main/firebase_cache.json")

        gtrend_boost, gtrend_keywords = self._compute_gtrend_boost(text)
        score += gtrend_boost

        economic_shock, economic_boost = self._compute_economic_boost(text)
        # 國光生技 45 億修正：
        if "國光生技" in title and "45億" in text:
            economic_boost = 0.0 # 完全取消加分，回歸基礎
        score += economic_boost

        # 歐盟正式報告加權（需標題關鍵字 + 內文深度驗證）
        if "歐盟" in title and "AI" in text:
            # 內文必須包含正式報告的深度特徵，而非只是提及
            report_signals = ["年度報告", "調查結果", "官方文件", "正式指控", "監測數據", "發布報告", "公報"]
            if any(sig in summary_text for sig in report_signals):
                score += 55.0
            elif "揭" in title or "揭露" in text:
                # 即使沒報告字眼，但內文有具體揭露行為
                score += 35.0

        # ── Step 4: Tier 權重乘法 ──
        # 歷史人物逝世降權
        if "逝世" in text and ("原爆" in text or "倖存者" in text):
            score -= 10.0
            
        multiplier = _TIER_MULTIPLIERS.get(content_tier, 1.0)
        # ─── 總編輯 Heartbeat 核心哲學 ───
        # 首頁頭條標準：是否改變了台灣或世界的一個「狀態」？
        # 優先考量：制度、公共利益、國家安全、台灣國際位置
        
        # 1. 制度改變加權 (特別是司法判決、政策出爐)
        if any(kw in title for kw in ["判決", "裁定", "駁回", "起訴", "法案", "通過", "生效"]):
            score += 20.0
            
        # 2. 台灣國際位置/能見度 (代表台灣，而非純娛樂)
        if any(kw in title for kw in ["奧斯卡", "坎城", "國際組織", "世衛", "代表台灣"]):
            score += 15.0
            
        # ─── 硬性新聞編輯 (Hard News) 加權邏輯 ───
        # ─── 商業與產業回應稿校準 (v2.15) ───
        # 修正單一公司對物價/油價的「回應稿」誤判。
        if any(kw in title for kw in ["：", "表示", "台橡"]) and any(kw in title for kw in ["油價助漲", "維繫客戶", "採取合約價"]):
            score -= 100.0 # 強力壓制
            
        # ─── 國際/兩岸/科技編輯 (International) 關鍵邏輯 ───
        # 1. 台灣關聯性優先 (主權、安全、外交地位)
        if any(kw in title for kw in ["主權", "台海", "外交突破", "訪台", "對台資助", "對台軍售"]):
            score += 25.0
            
        # 2. 國際組織參與 (自動列為高優先)
        if any(kw in title for kw in ["加入組織", "被排除", "國際組織", "WHO", "ICAO", "CPTPP"]):
            score += 20.0
            
        # 3. 科技產業格局 (AI、半導體、重大突破)
        if any(kw in title for kw in ["AI 產業", "半導體戰", "太空探索", "醫療突破"]):
            score += 15.0
        # 單純產品發布降級，除非涉及格局變動
        if any(kw in title for kw in ["發表會", "新品", "上市"]) and not any(kw in title for kw in ["台積電", "輝達", "格局"]):
            score -= 20.0

        # ─── 娛樂/運動/電影編輯 (Entertainment) 核心邏輯 (v2.14) ───
        # 1. 台灣能見度與文化外交 (奧斯卡、坎城、國際獲獎)
        if any(kw in title for kw in ["入圍", "獲獎", "台灣之光", "代表台灣", "國際影展"]):
            if any(kw in title for kw in ["奧斯卡", "坎城", "威尼斯", "柏林", "葛萊美"]):
                score += 30.0  # 最高優先級
            else:
                score += 15.0
                
        # 2. 運動賽事 (聚焦台灣選手與重大賽事)
        if any(kw in title for kw in ["奧運", "世界盃", "經典賽", "職棒", "職籃"]):
            # 重大賽事進度加權
            if any(kw in title for kw in ["奪冠", "晉級", "決賽", "總冠軍"]):
                score += 20.0
            else:
                score += 10.0
                
        # 3. 嚴格過濾八卦與流量內容 (準則天條)
        if any(kw in title for kw in ["戀情", "緋聞", "私生活", "婚變", "撞臉", "火辣", "身材", "自拍"]):
            # 娛樂八卦類執行毀滅性降權，確保不入選 5% 天條
            score -= 60.0
            
        # 4. 辨識公關宣傳稿 (降權以利改寫)
        if any(kw in title for kw in ["力邀", "重磅", "打造", "盛大", "驚艷"]):
            score -= 15.0

        # ─── 認知戰與澄清機制平衡 (v2.12) ───
        # 慎防「為了澄清而發稿」的價值：若標題含有澄清、反駁、闢謠，加分以確保真相能傳達
        if any(kw in title for kw in ["澄清", "闢謠", "反駁", "假訊息", "誤導", "不實"]):
            score += 20.0
            # 若標題同時包含「國安」或「認知戰」，加分更重，確保「擴大澄清」的效果
            if any(kw in title for kw in ["國安", "認知戰", "假資訊"]):
                score += 15.0
        
        # 對於純中方宣傳，維持適度降權以利交叉比對
        if any(kw in title for kw in ["中方表示", "國台辦", "堅決反對"]):
            # 僅輕微降權，避免過度壓制可能需要澄清的母題
            score -= 8.0 

        score *= multiplier
        breakdown["tier_multiplier"] = multiplier

        # Clamp
        total = max(0.0, min(100.0, round(score, 1)))

        # ── Step 5: 浮動門檻 ──
        threshold_info = self.compute_floating_threshold()
        # 門檻固定調高，目標 5% 產出
        effective_threshold = max(94.0, threshold_info.effective_threshold)

        # 判定
        eligible = total >= effective_threshold

        return V2ScoreResult(
            total_score=total,
            headline_eligible=eligible,
            headline_reason=f"score={total} thres={effective_threshold} tier={content_tier.name}",
            breakdown=breakdown,
            matched_rules=matched_rules,
            is_generic_international=v1_result.is_generic_international,
            content_tier=content_tier,
            tier_reason=tier_reason,
            effective_threshold=effective_threshold,
            ip_strict_matches=list(set(ip_matches)),
            economic_boost=economic_boost,
        )

    def reset_history(self) -> None:
        """清空滑動視窗歷史（用於測試或重新校準）。"""
        self._score_history.clear()
        self._volatility_history.clear()
        logger.info("V2Scorer: history cleared")

    def inject_history(self, scores: List[float]) -> None:
        """注入歷史分數（用於測試或初始化視窗）。

        Args:
            scores: 歷史分數列表。
        """
        for s in scores:
            self._score_history.append(s)
            self._volatility_history.append(s)
        logger.info("V2Scorer: injected %d historical scores", len(scores))

    def load_gtrend(
        self,
        csv_path: Optional[str] = None,
        csv_dir: Optional[str] = None,
    ) -> None:
        """載入或更新 gTrend 數據。

        Args:
            csv_path: 單一 CSV 路徑。
            csv_dir: CSV 目錄路徑。
        """
        if self._gtrend is None:
            self._gtrend = GTrendLoader(csv_path=csv_path, csv_dir=csv_dir)
        else:
            if csv_path:
                self._gtrend.load_csv(csv_path)
            if csv_dir:
                self._gtrend.load_dir(csv_dir)
        logger.info(
            "V2Scorer: gTrend updated, %d keywords",
            self._gtrend.keyword_count if self._gtrend else 0,
        )

    # ─── 工具方法 ───

    @staticmethod
    def _percentile(data: List[float], pct: float) -> float:
        """計算百分位數。

        Args:
            data: 數值列表。
            pct: 百分位數 0-100。

        Returns:
            百分位數值。
        """
        if not data:
            return 0.0
        sorted_data = sorted(data)
        n = len(sorted_data)
        k = (pct / 100.0) * (n - 1)
        f = math.floor(k)
        c = math.ceil(k)
        if f == c:
            return sorted_data[int(k)]
        d0 = sorted_data[f] * (c - k)
        d1 = sorted_data[c] * (k - f)
        return d0 + d1
