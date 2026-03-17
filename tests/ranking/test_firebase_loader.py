"""測試 FirebaseLoader — 串接 AppDev 提供的 Firebase 路徑數據

驗證：
1. 快取載入與解析
2. Trending Items 提取與排序
3. Trending Boost 計算
4. 關鍵字重疊比對邏輯
5. 設定讀取
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from ranking.firebase_loader import FirebaseLoader, FirebaseTrendingItem


@pytest.fixture
def firebase_cache(tmp_path: Path) -> str:
    """建立測試用 Firebase 快取 JSON。"""
    cache_file = tmp_path / "firebase_test.json"
    data = {
        "trending/headlines": {
            "id_1": {
                "title": "台積電法說會",
                "boost": 15.0,
                "category": "科技"
            },
            "id_2": {
                "title": "大谷翔平50轟50盜",
                "boost": 20.0,
                "category": "運動"
            },
            "id_3": {
                "title": "花蓮強震",
                "boost": 25.0,
                "category": "社會"
            }
        },
        "config/scorer": {
            "threshold_override": 85.0,
            "min_samples": 20
        }
    }
    cache_file.write_text(json.dumps(data, ensure_ascii=False), encoding="utf-8")
    return str(cache_file)


class TestFirebaseLoaderBasic:
    """基本載入與解析驗證。"""

    def test_load_cache(self, firebase_cache: str) -> None:
        """驗證快取載入。"""
        loader = FirebaseLoader(cache_path=firebase_cache)
        assert loader.trending_count == 3
        items = loader.trending_items
        # 應依 boost 降序排序
        assert items[0].title == "花蓮強震"
        assert items[0].boost == 25.0
        assert items[1].title == "大谷翔平50轟50盜"
        assert items[2].title == "台積電法說會"

    def test_get_config(self, firebase_cache: str) -> None:
        """驗證設定讀取。"""
        loader = FirebaseLoader(cache_path=firebase_cache)
        assert loader.get_config("threshold_override") == 85.0
        assert loader.get_config("min_samples") == 20
        assert loader.get_config("non_existent", "default") == "default"


class TestTrendingBoost:
    """Trending Boost 計算驗證。"""

    def test_exact_match_boost(self, firebase_cache: str) -> None:
        """完全匹配標題關鍵字。"""
        loader = FirebaseLoader(cache_path=firebase_cache)
        boost, ids = loader.get_trending_boost("台積電法說會：3奈米貢獻營收顯著")
        assert boost > 0
        assert "id_1" in ids

    def test_partial_match_boost(self, firebase_cache: str) -> None:
        """部分匹配標題。"""
        loader = FirebaseLoader(cache_path=firebase_cache)
        # 標題包含 trending title
        boost, ids = loader.get_trending_boost("震驚！花蓮強震造成多處房屋倒塌")
        assert "id_3" in ids
        assert boost == 25.0

    def test_keyword_overlap_boost(self, firebase_cache: str) -> None:
        """關鍵字重疊匹配。"""
        loader = FirebaseLoader(cache_path=firebase_cache)
        # "大谷翔平" + "50轟" 重疊
        boost, ids = loader.get_trending_boost("大谷翔平刷新紀錄 50轟達成")
        assert "id_2" in ids

    def test_multiple_matches_diminishing(self, firebase_cache: str) -> None:
        """多重匹配遞減加成。"""
        loader = FirebaseLoader(cache_path=firebase_cache)
        # 同時命中 台積電(15) 與 大谷翔平(20)
        boost, ids = loader.get_trending_boost("台積電法說會巧遇大谷翔平50轟")
        assert len(ids) == 2
        # 計算：20 * 1.0 + 15 * (1/1.5) = 20 + 10 = 30
        assert boost == pytest.approx(30.0)

    def test_no_match_returns_zero(self, firebase_cache: str) -> None:
        """未命中時回傳 0。"""
        loader = FirebaseLoader(cache_path=firebase_cache)
        boost, ids = loader.get_trending_boost("今日天氣晴朗適合郊遊")
        assert boost == 0.0
        assert ids == []


class TestKeywordOverlap:
    """關鍵字重疊邏輯測試。"""

    def test_overlap_true(self) -> None:
        loader = FirebaseLoader()
        assert loader._keyword_overlap("台積電法說會", "台積電營收創新高")  # 重疊 "台積電"

    def test_overlap_false(self) -> None:
        loader = FirebaseLoader()
        assert not loader._keyword_overlap("大谷翔平", "達比修有")  # 無足夠重疊
