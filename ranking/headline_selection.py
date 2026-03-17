"""Headline Selection — CP-IP 高優先權匹配引擎

依據「強實體 (IP entities)」進行 headline 優先匹配。
IP 實體由 v1_train.py 從 gold_set.md 正樣本的 entities 欄位自動提取並注入。

CP-IP = Content Priority — Intellectual Property / Important Personalities
這些關鍵字在標題中出現時，直接給予高權重加成，確保模型對「強實體」敏感。

使用方式：
    from ranking.headline_selection import HeadlineSelector

    selector = HeadlineSelector()
    result = selector.match(title="大谷翔平單場3轟破紀錄")
"""

from __future__ import annotations

import json
import logging
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Dict, List, Optional, Set

logger = logging.getLogger(__name__)

_IP_ENTITIES_PATH = Path(__file__).parent / "model" / "ip_entities.json"

# ─────────────────────────────────────────────
# 基礎 CP-IP 清單（手動維護 + 自動注入）
# ─────────────────────────────────────────────

# 手動維護的核心 IP 清單 — 這些永遠不會被自動移除
_BASE_CP_IP: List[str] = [
    # 國際人物/組織
    "川普", "拜登", "習近平", "普丁", "澤倫斯基",
    "WHO", "NATO", "UN",
    # 台灣核心
    "賴清德", "蔡英文", "台積電", "鴻海",
    # 軍事
    "MQ-9B", "F-16V", "潛艦", "海峽",
    # 運動
    "大谷翔平", "WBC", "MLB", "NBA",
    "奧運", "世界盃",
    # 文化
    "奧斯卡", "金曲獎", "金馬獎",
    # 科技
    "AI", "ChatGPT", "OpenAI", "輝達", "NVIDIA",
    # 地標公安
    "台中新光三越",
]


@dataclass
class IPMatchResult:
    """IP 匹配結果。"""
    matched: bool
    matched_entities: List[str]
    boost_score: float
    source: str  # "base" | "trained" | "both"


@dataclass
class HeadlineSelector:
    """CP-IP 高優先權匹配引擎。

    結合手動維護的基礎清單與訓練自動注入的 IP 實體，
    對標題進行快速比對，回傳加權結果。
    """

    base_entities: List[str] = field(default_factory=lambda: list(_BASE_CP_IP))
    trained_entities: List[str] = field(default_factory=list)
    entity_boost: float = 20.0  # 每命中一個 IP 實體的加分

    def __post_init__(self) -> None:
        """初始化時自動載入已訓練的 IP 實體。"""
        self._load_trained_entities()
        self._all_entities: Set[str] = set(self.base_entities) | set(self.trained_entities)
        logger.info(
            "HeadlineSelector initialized: %d base + %d trained = %d total IP entities",
            len(self.base_entities),
            len(self.trained_entities),
            len(self._all_entities),
        )

    def _load_trained_entities(self) -> None:
        """載入 v1_train.py 同步的 IP 實體清單。"""
        if _IP_ENTITIES_PATH.exists():
            try:
                data = json.loads(_IP_ENTITIES_PATH.read_text(encoding="utf-8"))
                self.trained_entities = data.get("entities", [])
                self.entity_boost = data.get("boost", self.entity_boost)
                logger.info(
                    "Loaded %d trained IP entities from %s",
                    len(self.trained_entities),
                    _IP_ENTITIES_PATH,
                )
            except (json.JSONDecodeError, KeyError) as e:
                logger.warning("Failed to load trained IP entities: %s", e)

    @property
    def all_entities(self) -> Set[str]:
        """回傳所有 IP 實體（base + trained）。"""
        return self._all_entities

    def match(self, title: str, summary: str = "") -> IPMatchResult:
        """比對標題是否命中 CP-IP 實體。

        Args:
            title: 新聞標題。
            summary: 摘要文字（輔助比對）。

        Returns:
            IPMatchResult 包含命中實體與加權分數。
        """
        text = title + " " + summary
        matched_base = [e for e in self.base_entities if e in text]
        matched_trained = [e for e in self.trained_entities if e in text and e not in matched_base]
        all_matched = matched_base + matched_trained

        if not all_matched:
            return IPMatchResult(
                matched=False,
                matched_entities=[],
                boost_score=0.0,
                source="none",
            )

        # 加權：第一個命中 full boost，後續遞減（避免堆疊過高）
        boost = 0.0
        for i, _ in enumerate(all_matched):
            boost += self.entity_boost * (1.0 / (1.0 + i * 0.3))
        boost = round(min(boost, 50.0), 1)  # cap at 50

        source = "both" if matched_base and matched_trained else (
            "base" if matched_base else "trained"
        )

        return IPMatchResult(
            matched=True,
            matched_entities=all_matched,
            boost_score=boost,
            source=source,
        )


# ─────────────────────────────────────────────
# 訓練同步 API（由 v1_train.py 呼叫）
# ─────────────────────────────────────────────

def sync_ip_entities(entities: List[str], boost: float = 20.0) -> None:
    """將訓練提取的 IP 實體寫入 ip_entities.json。

    Args:
        entities: IP 實體關鍵字清單。
        boost: 每個實體的加權分數。
    """
    # 合併現有 base 清單確保不重複
    all_entities = list(set(entities))
    data = {
        "entities": sorted(all_entities),
        "boost": boost,
        "synced_from": "v1_train.py",
    }
    _IP_ENTITIES_PATH.parent.mkdir(parents=True, exist_ok=True)
    _IP_ENTITIES_PATH.write_text(
        json.dumps(data, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    logger.info(
        "Synced %d IP entities to %s (boost=%.1f)",
        len(all_entities),
        _IP_ENTITIES_PATH,
        boost,
    )
