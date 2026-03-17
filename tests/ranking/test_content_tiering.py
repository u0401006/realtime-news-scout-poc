"""測試 content_tier 內容型態辨識 — news-content-tiering

驗證：
1. P0_short / P0_main / P1_followup / P2_response / P3_analysis 各類判定
2. ScoreResult 包含 content_tier + tier_reason
3. 同分排序以 tier 優先序為準
"""

from __future__ import annotations

import pytest

from ranking.model.v1_scorer import (
    ContentTier,
    ScoreResult,
    V1Scorer,
    classify_content_tier,
)


@pytest.fixture
def scorer() -> V1Scorer:
    """使用預設權重檔初始化 scorer。"""
    return V1Scorer()


# ─── 1. classify_content_tier 單元測試 ───


class TestClassifyContentTier:
    """直接測試 classify_content_tier 函式。"""

    def test_p0_short_breaking_keyword(self) -> None:
        """含「快訊」應判定 P0_short。"""
        tier, reason = classify_content_tier("快訊：花蓮發生規模5.2地震")
        assert tier == ContentTier.P0_short
        assert "快訊" in reason

    def test_p0_short_speed_keyword(self) -> None:
        """含「速報」應判定 P0_short。"""
        tier, reason = classify_content_tier("速報｜台積電法說會營收超預期")
        assert tier == ContentTier.P0_short
        assert "速報" in reason

    def test_p0_short_very_short_title(self) -> None:
        """≤15 字極短標題應判定 P0_short。"""
        short_title = "台股收盤漲200點"  # 8 字
        tier, reason = classify_content_tier(short_title)
        assert tier == ContentTier.P0_short
        assert "極短標題" in reason

    def test_p0_main_default(self) -> None:
        """無特殊標記的一般報導應判定 P0_main。"""
        tier, reason = classify_content_tier(
            "美國第七艦隊通過台灣海峽 中國軍方嚴正警告 國防部嚴密監控中"
        )
        assert tier == ContentTier.P0_main
        assert "主體報導" in reason

    def test_p1_followup_keyword(self) -> None:
        """含「最新」「追蹤」應判定 P1_followup。"""
        tier, reason = classify_content_tier(
            "花蓮地震最新：搜救隊尋獲最後一名失聯者遺體 確認罹難"
        )
        assert tier == ContentTier.P1_followup
        assert "最新" in reason

    def test_p1_followup_update(self) -> None:
        """含「更新」應判定 P1_followup。"""
        tier, reason = classify_content_tier("更新：確診數據上修為5000例")
        assert tier == ContentTier.P1_followup
        assert "更新" in reason

    def test_p2_response_keyword(self) -> None:
        """含「回應」「聲明」應判定 P2_response。"""
        tier, reason = classify_content_tier(
            "外交部回應中國軍演：嚴正關切 呼籲對岸自制克制挑釁行為"
        )
        assert tier == ContentTier.P2_response
        assert "回應" in reason

    def test_p2_response_condemn(self) -> None:
        """含「譴責」應判定 P2_response。"""
        tier, reason = classify_content_tier(
            "聯合國安理會譴責北韓再次試射洲際飛彈 要求立即停止挑釁"
        )
        assert tier == ContentTier.P2_response
        assert "譴責" in reason

    def test_p3_analysis_keyword(self) -> None:
        """含「分析」「觀察」應判定 P3_analysis。"""
        tier, reason = classify_content_tier(
            "分析：美中科技戰下的台灣半導體產業何去何從 多面向深入解讀"
        )
        assert tier == ContentTier.P3_analysis
        assert "分析" in reason

    def test_p3_analysis_forecast(self) -> None:
        """含「預測」應判定 P3_analysis。"""
        tier, reason = classify_content_tier(
            "IMF預測2027年全球經濟成長率下修至2.8% 新興市場受衝擊最大"
        )
        assert tier == ContentTier.P3_analysis
        assert "預測" in reason

    def test_p3_analysis_editorial(self) -> None:
        """含「社論」「專欄」應判定 P3_analysis。"""
        tier, reason = classify_content_tier(
            "社論：從能源轉型看台灣永續發展的挑戰與機遇 需要全民共識推動"
        )
        assert tier == ContentTier.P3_analysis
        assert "社論" in reason


# ─── 2. ScoreResult 包含 content_tier + tier_reason ───


class TestScoreResultTier:
    """V1Scorer.score() 回傳結果應包含 tier 資訊。"""

    def test_score_result_has_tier(self, scorer: V1Scorer) -> None:
        """ScoreResult 應包含 content_tier 欄位。"""
        result = scorer.score(title="快訊：花蓮強震規模6.8")
        assert isinstance(result.content_tier, ContentTier)
        assert result.content_tier == ContentTier.P0_short
        assert result.tier_reason != ""

    def test_main_story_tier(self, scorer: V1Scorer) -> None:
        """一般主體報導應回傳 P0_main。"""
        result = scorer.score(
            title="台積電3奈米良率突破95% 全球半導體業震撼 輝達追加百億美元訂單搶產能",
            topic_tags=["科技"],
        )
        assert result.content_tier == ContentTier.P0_main

    def test_analysis_tier(self, scorer: V1Scorer) -> None:
        """分析類文章應回傳 P3_analysis。"""
        result = scorer.score(
            title="分析：美中科技戰下的台灣半導體產業何去何從 多面向深入解讀趨勢",
            topic_tags=["科技"],
        )
        assert result.content_tier == ContentTier.P3_analysis

    def test_response_tier(self, scorer: V1Scorer) -> None:
        """回應類應回傳 P2_response。"""
        result = scorer.score(
            title="國防部回應共軍環台軍演：國軍全程掌握 民眾無須恐慌 將持續嚴密監控",
            topic_tags=["軍事"],
        )
        assert result.content_tier == ContentTier.P2_response


# ─── 3. 同分排序 tier 優先序 ───


class TestTierPrioritySort:
    """同分時 tier 優先序：P0_short > P0_main > P1 > P2 > P3。"""

    def test_tier_enum_order(self) -> None:
        """ContentTier.value 越小優先序越高。"""
        assert ContentTier.P0_short.value < ContentTier.P0_main.value
        assert ContentTier.P0_main.value < ContentTier.P1_followup.value
        assert ContentTier.P1_followup.value < ContentTier.P2_response.value
        assert ContentTier.P2_response.value < ContentTier.P3_analysis.value

    def test_same_score_sorted_by_tier(self) -> None:
        """同分的 ScoreResult 按 tier 優先序排列。"""
        results = [
            ScoreResult(
                total_score=80.0,
                headline_eligible=True,
                headline_reason="",
                breakdown={},
                matched_rules=[],
                is_generic_international=False,
                content_tier=ContentTier.P3_analysis,
                tier_reason="分析",
            ),
            ScoreResult(
                total_score=80.0,
                headline_eligible=True,
                headline_reason="",
                breakdown={},
                matched_rules=[],
                is_generic_international=False,
                content_tier=ContentTier.P0_short,
                tier_reason="快訊",
            ),
            ScoreResult(
                total_score=80.0,
                headline_eligible=True,
                headline_reason="",
                breakdown={},
                matched_rules=[],
                is_generic_international=False,
                content_tier=ContentTier.P1_followup,
                tier_reason="追蹤",
            ),
            ScoreResult(
                total_score=80.0,
                headline_eligible=True,
                headline_reason="",
                breakdown={},
                matched_rules=[],
                is_generic_international=False,
                content_tier=ContentTier.P0_main,
                tier_reason="主體",
            ),
            ScoreResult(
                total_score=80.0,
                headline_eligible=True,
                headline_reason="",
                breakdown={},
                matched_rules=[],
                is_generic_international=False,
                content_tier=ContentTier.P2_response,
                tier_reason="回應",
            ),
        ]

        # 排序：分數降序 → tier 升序（value 越小越優先）
        sorted_results = sorted(
            results,
            key=lambda r: (-r.total_score, r.content_tier.value),
        )

        expected_order = [
            ContentTier.P0_short,
            ContentTier.P0_main,
            ContentTier.P1_followup,
            ContentTier.P2_response,
            ContentTier.P3_analysis,
        ]
        actual_order = [r.content_tier for r in sorted_results]
        assert actual_order == expected_order, (
            f"排序錯誤：期望 {expected_order}，實際 {actual_order}"
        )

    def test_different_scores_override_tier(self) -> None:
        """不同分數時，分數高的排前面，不管 tier。"""
        high_analysis = ScoreResult(
            total_score=95.0,
            headline_eligible=True,
            headline_reason="",
            breakdown={},
            matched_rules=[],
            is_generic_international=False,
            content_tier=ContentTier.P3_analysis,
            tier_reason="分析",
        )
        low_short = ScoreResult(
            total_score=70.0,
            headline_eligible=False,
            headline_reason="",
            breakdown={},
            matched_rules=[],
            is_generic_international=False,
            content_tier=ContentTier.P0_short,
            tier_reason="快訊",
        )

        sorted_results = sorted(
            [low_short, high_analysis],
            key=lambda r: (-r.total_score, r.content_tier.value),
        )
        assert sorted_results[0].total_score == 95.0
        assert sorted_results[1].total_score == 70.0


# ─── 4. SmokeCnaWindow 整合 tier 輸出 ───


class TestSmokeTierIntegration:
    """SmokeCnaWindow 輸出應包含 tier 欄位。"""

    def test_smoke_events_have_tier_fields(self) -> None:
        """score_events 結果每筆應有 contentTier + tierReason。"""
        from ingestion.scripts.smoke_cna_window import SmokeCnaWindow

        window = SmokeCnaWindow()
        events = window._generate_dry_run_events()
        scored = window.score_events(events)

        for event in scored:
            cls = event["classification"]
            assert "contentTier" in cls, f"缺少 contentTier: {event['content']['title']}"
            assert "tierReason" in cls, f"缺少 tierReason: {event['content']['title']}"
            # contentTier 應為合法值
            assert cls["contentTier"] in [t.name for t in ContentTier], (
                f"非法 contentTier: {cls['contentTier']}"
            )

    def test_smoke_tier_sort_order(self) -> None:
        """同分事件應按 tier 排序。"""
        from ingestion.scripts.smoke_cna_window import SmokeCnaWindow

        window = SmokeCnaWindow()
        events = window._generate_dry_run_events()
        scored = window.score_events(events)

        # 驗證排序正確：分數降序 → tier 升序
        for i in range(len(scored) - 1):
            s1 = scored[i]["scoring"]["newsValue"]
            s2 = scored[i + 1]["scoring"]["newsValue"]
            if s1 == s2:
                t1 = ContentTier[scored[i]["classification"]["contentTier"]].value
                t2 = ContentTier[scored[i + 1]["classification"]["contentTier"]].value
                assert t1 <= t2, (
                    f"同分排序錯誤：{scored[i]['content']['title']} "
                    f"(tier={scored[i]['classification']['contentTier']}) 應在 "
                    f"{scored[i+1]['content']['title']} "
                    f"(tier={scored[i+1]['classification']['contentTier']}) 前面"
                )
