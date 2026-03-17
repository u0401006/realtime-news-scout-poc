"""測試門檻提高至 83.0 的影響

驗證：
1. 門檻值確實為 83.0
2. 邊緣新聞（分數 80-82）被正確過濾
3. 高品質內容（IP/公安/重大事件）仍然出線
"""

from __future__ import annotations

import pytest

from ranking.model.v1_scorer import V1Scorer


@pytest.fixture
def scorer() -> V1Scorer:
    return V1Scorer()


class TestThreshold83:
    """門檻 83.0 驗證。"""

    def test_threshold_is_83(self, scorer: V1Scorer) -> None:
        """確認門檻已更新為 83.0。"""
        assert scorer.headline_threshold == 83.0, (
            f"門檻應為 83.0，實際 {scorer.headline_threshold}"
        )

    # ─── 高品質內容仍出線 ───

    def test_major_public_safety_passes(self, scorer: V1Scorer) -> None:
        """重大公安事件（IP+死傷）應輕鬆過 83。"""
        result = scorer.score(
            title="台中新光三越氨氣外洩1死 勒令停用",
            topic_tags=["社會", "地方"],
            region_tags=["台灣"],
        )
        assert result.headline_eligible, (
            f"重大公安應出線，分數={result.total_score}, reason={result.headline_reason}"
        )
        assert result.total_score >= 83.0

    def test_ip_entity_strong_story_passes(self, scorer: V1Scorer) -> None:
        """強 IP 實體（大谷翔平+破紀錄）應出線。"""
        result = scorer.score(
            title="大谷翔平單場3轟破紀錄 道奇大勝",
            topic_tags=["運動"],
        )
        assert result.headline_eligible, (
            f"強 IP 應出線，分數={result.total_score}"
        )

    def test_major_military_passes(self, scorer: V1Scorer) -> None:
        """重大軍事新聞應出線。"""
        result = scorer.score(
            title="第七艦隊通過台灣海峽 共軍軍艦跟監",
            topic_tags=["國際", "軍事"],
            region_tags=["台灣"],
        )
        assert result.headline_eligible, (
            f"軍事要聞應出線，分數={result.total_score}"
        )

    # ─── 邊緣新聞被過濾 ───

    def test_generic_intl_filtered(self, scorer: V1Scorer) -> None:
        """泛國際新聞不應出線。"""
        result = scorer.score(
            title="歐盟執委會討論2027年碳排放目標細節",
            topic_tags=["國際"],
        )
        assert not result.headline_eligible, (
            f"泛國際應被過濾，分數={result.total_score}"
        )

    def test_speculative_news_filtered(self, scorer: V1Scorer) -> None:
        """預測分析類不應出線。"""
        result = scorer.score(
            title="富邦金分析師：台股恐回測萬五",
            topic_tags=["財經"],
        )
        assert not result.headline_eligible, (
            f"預測分析應被過濾，分數={result.total_score}"
        )

    def test_minor_local_news_filtered(self, scorer: V1Scorer) -> None:
        """一般地方小新聞不應出線。"""
        result = scorer.score(
            title="台北市違規停車開罰3萬件",
            topic_tags=["社會"],
        )
        assert not result.headline_eligible, (
            f"地方小新聞應被過濾，分數={result.total_score}"
        )
