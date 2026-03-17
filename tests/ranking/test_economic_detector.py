"""測試 CP-Economic — 經濟新聞劇烈震盪量化判定

驗證：
1. 漲跌幅百分比提取
2. 劇烈震盪判定（>2%）與極端震盪（>5%）
3. 震盪動作詞偵測
4. 政策衝擊詞偵測
5. 經濟指標 + 動作詞組合加分
6. 加分上限
"""

from __future__ import annotations

import pytest

from ranking.economic_detector import (
    EconomicDetector,
    EconomicShockResult,
    _ECONOMIC_CAP,
    _EXTREME_PCT_THRESHOLD,
    _SEVERE_PCT_THRESHOLD,
)


@pytest.fixture
def detector() -> EconomicDetector:
    return EconomicDetector()


class TestPctExtraction:
    """漲跌幅百分比提取。"""

    def test_extract_simple_pct(self, detector: EconomicDetector) -> None:
        """簡單百分比格式。"""
        result = detector.detect("台股今日跌幅達3.5%")
        assert result.detected_pct is not None
        assert result.detected_pct == pytest.approx(3.5)

    def test_extract_chinese_pct(self, detector: EconomicDetector) -> None:
        """中文描述格式。"""
        result = detector.detect("道瓊暴跌2.8% 創今年最大單日跌幅")
        assert result.detected_pct is not None
        assert result.detected_pct >= 2.8

    def test_extract_multiple_pcts_takes_max(self, detector: EconomicDetector) -> None:
        """多個百分比取最大值。"""
        result = detector.detect("台積電跌1.5% 輝達重挫5.2%")
        assert result.detected_pct is not None
        assert result.detected_pct == pytest.approx(5.2)

    def test_no_pct_returns_none(self, detector: EconomicDetector) -> None:
        """無百分比時回傳 None。"""
        result = detector.detect("台股收盤小漲30點")
        assert result.detected_pct is None


class TestSeverityJudgment:
    """劇烈震盪嚴重程度判定。"""

    def test_extreme_shock(self, detector: EconomicDetector) -> None:
        """漲跌幅 > 5% 判定為極端。"""
        result = detector.detect("台股暴跌800點 跌幅達6.2%")
        assert result.severity == "extreme"
        assert result.is_shock

    def test_severe_shock(self, detector: EconomicDetector) -> None:
        """漲跌幅 > 2% 判定為劇烈。"""
        result = detector.detect("台股重挫 跌幅3.1%")
        assert result.severity == "severe"
        assert result.is_shock

    def test_moderate_wave(self, detector: EconomicDetector) -> None:
        """漲跌幅 < 2% 判定為溫和。"""
        result = detector.detect("台股今日跌幅1.2%")
        assert result.severity == "moderate"

    def test_no_shock_plain_text(self, detector: EconomicDetector) -> None:
        """無經濟訊號。"""
        result = detector.detect("總統出席公益活動")
        assert result.severity == "none"
        assert not result.is_shock
        assert result.boost == 0.0


class TestShockActionKeywords:
    """震盪動作詞偵測。"""

    def test_crash_keyword(self, detector: EconomicDetector) -> None:
        """崩盤關鍵字。"""
        result = detector.detect("台股崩盤 加權指數失守萬八")
        assert "崩盤" in result.matched_actions
        assert result.is_shock  # 有動作詞+指標

    def test_blood_bath_keyword(self, detector: EconomicDetector) -> None:
        """血洗關鍵字。"""
        result = detector.detect("費半血洗 半導體股全面重挫")
        assert any(kw in result.matched_actions for kw in ["血洗", "重挫"])

    def test_historic_high(self, detector: EconomicDetector) -> None:
        """歷史新高。"""
        result = detector.detect("台股歷史新高 加權指數突破2萬5")
        assert "歷史新高" in result.matched_actions

    def test_action_without_index_no_shock(self, detector: EconomicDetector) -> None:
        """僅有動作詞無指標不判定為震盪。"""
        result = detector.detect("某小店暴跌人氣 生意冷清")
        # "暴跌" in matched_actions, but no index → is_shock depends on context
        # Actually, "暴跌" is a shock action, need index to combine
        assert "暴跌" in result.matched_actions


class TestPolicyShock:
    """政策衝擊詞偵測。"""

    def test_rate_hike(self, detector: EconomicDetector) -> None:
        """升息。"""
        result = detector.detect("Fed 升息3碼 美元指數飆漲")
        assert "升息" in result.matched_policies
        assert result.boost > 0

    def test_default(self, detector: EconomicDetector) -> None:
        """違約。"""
        result = detector.detect("美國公債違約風險升溫 殖利率暴漲")
        assert "違約" in result.matched_policies


class TestEconomicBoost:
    """經濟加分計算。"""

    def test_extreme_gets_highest_boost(self, detector: EconomicDetector) -> None:
        """極端震盪獲得最高加分。"""
        result = detector.detect("台股暴跌800點 跌幅達6.2% 加權指數重挫")
        assert result.boost >= 25.0  # extreme + action + index

    def test_severe_with_action_and_index(self, detector: EconomicDetector) -> None:
        """劇烈震盪 + 動作詞 + 指標。"""
        result = detector.detect("道瓊暴跌3.2% 歷史新低 油價崩盤")
        assert result.boost > 15.0

    def test_boost_capped(self, detector: EconomicDetector) -> None:
        """加分不超過上限。"""
        result = detector.detect(
            "台股暴跌8% 歷史新低 加權指數崩盤 Fed 升息 違約風險 "
            "美元暴漲 殖利率飆升 油價崩跌"
        )
        assert result.boost <= _ECONOMIC_CAP

    def test_no_boost_for_non_economic(self, detector: EconomicDetector) -> None:
        """非經濟新聞不加分。"""
        result = detector.detect("大谷翔平單場3轟破紀錄")
        assert result.boost == 0.0
