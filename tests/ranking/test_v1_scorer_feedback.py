"""測試 v1_scorer.py feedback_loop 修正 — v1.2

驗證 Capo 反饋中的三大修正邏輯：
1. 預測/分析類降權
2. 公安/死傷升權
3. 國際衝突 signal 不觸發泛國際降權
"""

from __future__ import annotations

import pytest

from ranking.model.v1_scorer import V1Scorer


@pytest.fixture
def scorer() -> V1Scorer:
    """使用預設權重檔初始化 scorer。"""
    return V1Scorer()


# ─── 1. 預測/分析類降權 ───

class TestSpeculativePenalty:
    """預測/分析類標題應降權。"""

    def test_speculative_keyword_lowers_score(self, scorer: V1Scorer) -> None:
        """含「恐」「分析」的標題應被降權。"""
        result = scorer.score(
            title="富邦金分析師：台股恐回測萬五",
            topic_tags=["財經"],
        )
        assert result.total_score <= 55.0, (
            f"預測分析類應 ≤55，實際 {result.total_score}"
        )
        assert "預測分析降權" in result.matched_rules

    def test_speculative_with_survey(self, scorer: V1Scorer) -> None:
        """含「調查」「可能」的標題應被降權。"""
        result = scorer.score(
            title="調查：逾6成民眾可能支持核電重啟",
            topic_tags=["社會"],
        )
        assert result.total_score <= 55.0, (
            f"調查類應 ≤55，實際 {result.total_score}"
        )

    def test_major_speaker_bypasses_penalty(self, scorer: V1Scorer) -> None:
        """重大實體（如央行、川普）發言的預測不降權。"""
        result = scorer.score(
            title="川普：若中國不配合恐加碼制裁",
            topic_tags=["國際"],
        )
        assert "預測分析降權" not in result.matched_rules


# ─── 2. 公安/死傷升權 ───

class TestPublicSafetyBoost:
    """公安/死傷突發事件應大幅加權。"""

    def test_ammonia_leak_fatality(self, scorer: V1Scorer) -> None:
        """「氨氣外洩1死 勒令停用」應 ≥85。"""
        result = scorer.score(
            title="台中新光三越氨氣外洩1死 勒令停用",
            topic_tags=["社會", "地方"],
            region_tags=["台灣"],
        )
        assert result.total_score >= 85.0, (
            f"公安死傷應 ≥85，實際 {result.total_score}\n"
            f"breakdown: {result.breakdown}"
        )
        assert any("公安死傷" in r for r in result.matched_rules)

    def test_chemical_leak_multi_casualty(self, scorer: V1Scorer) -> None:
        """化工廠氨氣外洩 多人傷亡 應 ≥85。"""
        result = scorer.score(
            title="桃園化工廠氨氣外洩 3人送醫1死 相驗確認中毒",
            topic_tags=["社會"],
            region_tags=["台灣"],
        )
        assert result.total_score >= 85.0, (
            f"化工廠公安應 ≥85，實際 {result.total_score}\n"
            f"breakdown: {result.breakdown}"
        )

    def test_generic_accident_no_boost(self, scorer: V1Scorer) -> None:
        """一般交通違規不觸發公安加權。"""
        result = scorer.score(
            title="台北市違規停車開罰3萬件",
            topic_tags=["社會"],
        )
        assert not any("公安死傷" in r for r in result.matched_rules)


# ─── 3. 國際衝突 signal 不觸發泛國際降權 ───

class TestIntlConflictSignal:
    """含衝突性關鍵字不應觸發泛國際降權。"""

    def test_seizure_retaliation(self, scorer: V1Scorer) -> None:
        """「扣押」「報復」→ 高價值，不應被泛國際抑制。"""
        result = scorer.score(
            title="中國扣押台商報復美國海纜制裁",
            topic_tags=["國際"],
        )
        assert not result.is_generic_international, "國際衝突不應標記為泛國際"
        assert result.total_score >= 75.0, (
            f"國際衝突應 ≥75，實際 {result.total_score}"
        )
        assert "國際衝突signal" in result.matched_rules

    def test_belt_road_sanction(self, scorer: V1Scorer) -> None:
        """一帶一路制裁 → 高價值 signal。"""
        result = scorer.score(
            title="一帶一路沿線港口遭美歐聯手制裁 北京強烈反彈",
            topic_tags=["國際"],
        )
        assert not result.is_generic_international
        assert result.total_score >= 75.0, (
            f"一帶一路制裁應 ≥75，實際 {result.total_score}"
        )

    def test_generic_intl_still_penalized(self, scorer: V1Scorer) -> None:
        """無衝突 signal 的泛國際新聞仍應被降權。"""
        result = scorer.score(
            title="歐盟執委會討論2027年碳排放目標細節",
            topic_tags=["國際"],
        )
        # 這類新聞不應得高分
        assert result.total_score < 70.0, (
            f"泛國際應 <70，實際 {result.total_score}"
        )


# ─── 4. IP 實體驗證 ───

class TestIPEntityUpdate:
    """驗證新增的 IP 實體。"""

    def test_taichung_shinkong_in_entities(self, scorer: V1Scorer) -> None:
        """台中新光三越應在 IP 實體清單中。"""
        from ranking.headline_selection import HeadlineSelector
        selector = HeadlineSelector()
        assert "台中新光三越" in selector.all_entities
