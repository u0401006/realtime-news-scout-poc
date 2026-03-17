"""CP-Economic — 經濟新聞「劇烈震盪」量化判定模組

偵測新聞標題/摘要中是否包含經濟指標劇烈震盪的訊號，
當漲跌幅 > 2% 或出現特定崩盤/暴漲關鍵字時，給予額外加權。

CP = Content Priority

使用方式：
    from ranking.economic_detector import EconomicDetector

    detector = EconomicDetector()
    result = detector.detect("台股暴跌800點 跌幅達3.5%")
"""

from __future__ import annotations

import logging
import re
from dataclasses import dataclass
from typing import List, Optional, Tuple

logger = logging.getLogger(__name__)

# ─── 漲跌幅正規表達式 ───
# 匹配如：跌3.5%、漲2.1%、-5%、+3%、跌幅達3.5%、漲幅2%
_PCT_CHANGE_PATTERN = re.compile(
    r"(?:漲幅?|跌幅?|漲跌幅?|上漲|下跌|重挫|暴跌|暴漲|大漲|大跌|飆漲|崩跌)"
    r"[達逾約近超]?"
    r"\s*(\d+(?:\.\d+)?)\s*%"
    r"|"
    r"[+-]?\s*(\d+(?:\.\d+)?)\s*%"
)

# ─── 劇烈震盪門檻 ───
_SEVERE_PCT_THRESHOLD: float = 2.0  # 漲跌幅 > 2% 判定為劇烈震盪
_EXTREME_PCT_THRESHOLD: float = 5.0  # 漲跌幅 > 5% 為極端震盪

# ─── 經濟指標關鍵字 ───
_MARKET_INDEX_KEYWORDS: List[str] = [
    "台股", "加權指數", "道瓊", "S&P", "S&P500", "納斯達克", "費半",
    "恒生", "日經", "上證", "深證", "KOSPI", "DAX",
    "美元", "日圓", "歐元", "人民幣", "匯率",
    "油價", "金價", "銀價", "原油", "布蘭特", "WTI",
    "比特幣", "以太幣", "加密貨幣",
    "殖利率", "公債", "國債",
]

# ─── 劇烈震盪動作詞 ───
_SHOCK_ACTION_KEYWORDS: List[str] = [
    "暴跌", "暴漲", "崩盤", "崩跌", "重挫", "閃崩", "熔斷",
    "飆漲", "狂瀉", "血洗", "跳水", "大屠殺",
    "歷史新低", "歷史新高", "創新低", "創新高",
    "腰斬", "蒸發",
]

# ─── 央行/政策衝擊關鍵字 ───
_POLICY_SHOCK_KEYWORDS: List[str] = [
    "升息", "降息", "量化寬鬆", "QE", "緊縮", "印鈔",
    "違約", "債務危機", "信用評等", "降評",
    "資本管制", "外匯管制", "拋售",
]

# ─── 加分配置 ───
_BASE_ECONOMIC_BOOST: float = 10.0  # 偵測到經濟震盪基礎加分
_SEVERE_PCT_BOOST: float = 15.0     # 漲跌幅 > 2%
_EXTREME_PCT_BOOST: float = 25.0    # 漲跌幅 > 5%
_SHOCK_KEYWORD_BOOST: float = 10.0  # 震盪動作詞加分
_POLICY_SHOCK_BOOST: float = 8.0    # 政策衝擊加分
_ECONOMIC_CAP: float = 40.0         # 經濟加分上限


@dataclass
class EconomicShockResult:
    """經濟震盪偵測結果。"""
    is_shock: bool
    severity: str  # "none" | "moderate" | "severe" | "extreme"
    detected_pct: Optional[float]  # 偵測到的百分比數值
    matched_indices: List[str]     # 命中的經濟指標
    matched_actions: List[str]     # 命中的震盪動作詞
    matched_policies: List[str]    # 命中的政策衝擊詞
    boost: float                   # 計算後的加分值
    reason: str                    # 判定原因


class EconomicDetector:
    """CP-Economic 經濟劇烈震盪偵測器。

    偵測邏輯：
    1. 搜尋漲跌幅百分比 → 判定嚴重程度
    2. 搜尋市場指標關鍵字 → 確認為經濟新聞
    3. 搜尋震盪動作詞 → 額外加權
    4. 搜尋政策衝擊詞 → 額外加權
    5. 綜合計算加分（cap 在上限）
    """

    def __init__(
        self,
        severe_pct: float = _SEVERE_PCT_THRESHOLD,
        extreme_pct: float = _EXTREME_PCT_THRESHOLD,
        cap: float = _ECONOMIC_CAP,
    ) -> None:
        """初始化偵測器。

        Args:
            severe_pct: 劇烈震盪百分比門檻。
            extreme_pct: 極端震盪百分比門檻。
            cap: 加分上限。
        """
        self._severe_pct = severe_pct
        self._extreme_pct = extreme_pct
        self._cap = cap

    def detect(self, text: str) -> EconomicShockResult:
        """偵測文本中的經濟劇烈震盪訊號。

        Args:
            text: 標題 + 摘要文本。

        Returns:
            EconomicShockResult 偵測結果。
        """
        # Step 1: 搜尋漲跌幅百分比
        detected_pct = self._extract_max_pct(text)

        # Step 2: 搜尋經濟指標
        matched_indices = [kw for kw in _MARKET_INDEX_KEYWORDS if kw in text]

        # Step 3: 搜尋震盪動作詞
        matched_actions = [kw for kw in _SHOCK_ACTION_KEYWORDS if kw in text]

        # Step 4: 搜尋政策衝擊詞
        matched_policies = [kw for kw in _POLICY_SHOCK_KEYWORDS if kw in text]

        # Step 5: 判定
        boost = 0.0
        reasons: List[str] = []

        # 百分比判定
        if detected_pct is not None and detected_pct >= self._extreme_pct:
            boost += _EXTREME_PCT_BOOST
            severity = "extreme"
            reasons.append(f"極端震盪({detected_pct:.1f}%>{self._extreme_pct}%)")
        elif detected_pct is not None and detected_pct >= self._severe_pct:
            boost += _SEVERE_PCT_BOOST
            severity = "severe"
            reasons.append(f"劇烈震盪({detected_pct:.1f}%>{self._severe_pct}%)")
        elif detected_pct is not None:
            severity = "moderate"
            reasons.append(f"溫和波動({detected_pct:.1f}%)")
        else:
            severity = "none"

        # 震盪動作詞加分
        if matched_actions:
            boost += _SHOCK_KEYWORD_BOOST
            reasons.append(f"震盪動作({','.join(matched_actions[:3])})")

        # 政策衝擊加分
        if matched_policies:
            boost += _POLICY_SHOCK_BOOST
            reasons.append(f"政策衝擊({','.join(matched_policies[:2])})")

        # 經濟指標確認 → 基礎加分
        if matched_indices and (matched_actions or detected_pct is not None):
            boost += _BASE_ECONOMIC_BOOST
            reasons.append(f"經濟指標({','.join(matched_indices[:2])})")

        # 判定是否為震盪
        is_shock = severity in ("severe", "extreme") or (
            matched_actions and matched_indices
        )

        # 若無任何經濟訊號，不加分
        if not is_shock and severity == "none" and not matched_actions:
            boost = 0.0

        # Cap
        boost = min(boost, self._cap)

        reason = "；".join(reasons) if reasons else "無經濟震盪訊號"

        return EconomicShockResult(
            is_shock=is_shock,
            severity=severity,
            detected_pct=detected_pct,
            matched_indices=matched_indices,
            matched_actions=matched_actions,
            matched_policies=matched_policies,
            boost=round(boost, 1),
            reason=reason,
        )

    @staticmethod
    def _extract_max_pct(text: str) -> Optional[float]:
        """從文本中提取最大漲跌幅百分比。

        Args:
            text: 要搜尋的文本。

        Returns:
            最大百分比數值，或 None。
        """
        matches = _PCT_CHANGE_PATTERN.findall(text)
        if not matches:
            return None

        max_pct = 0.0
        for groups in matches:
            for g in groups:
                if g:
                    try:
                        val = float(g)
                        if val > max_pct:
                            max_pct = val
                    except ValueError:
                        continue

        return max_pct if max_pct > 0 else None
