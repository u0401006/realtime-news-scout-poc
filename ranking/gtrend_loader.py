"""Google Trends CSV Loader — 讀取 gTrend 匯出的 CSV 進行動態加分

支援兩種格式：
1. 標準 Google Trends CSV（前幾行為 metadata，實際數據從第 3 行開始）
2. 簡化格式：直接 header + data rows，columns = [keyword, score]

使用方式：
    from ranking.gtrend_loader import GTrendLoader

    loader = GTrendLoader("data/gtrend_daily.csv")
    boost = loader.get_boost("台積電")  # → 15.0 (依 trend score 動態計算)
"""

from __future__ import annotations

import csv
import logging
import re
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

logger = logging.getLogger(__name__)

# gTrend score 區間對應加分
_BOOST_TIERS: List[Tuple[int, float]] = [
    (90, 20.0),   # 90-100: 爆量趨勢
    (70, 15.0),   # 70-89: 高度關注
    (50, 10.0),   # 50-69: 中等關注
    (30, 5.0),    # 30-49: 輕度關注
    (0, 0.0),     # 0-29: 無顯著趨勢
]

# gTrend CSV 常見的 metadata 行數上限（Google Trends 匯出通常前 1-3 行是描述）
_MAX_METADATA_LINES: int = 5


@dataclass
class TrendEntry:
    """單一 gTrend 關鍵字條目。"""
    keyword: str
    score: int  # 0-100, Google Trends 相對搜尋量
    boost: float  # 計算後的加分值
    category: str = ""  # 可選分類標籤


@dataclass
class GTrendSnapshot:
    """一次 gTrend CSV 載入的快照。"""
    entries: List[TrendEntry]
    loaded_at: str
    source_path: str
    keyword_count: int = 0

    def __post_init__(self) -> None:
        self.keyword_count = len(self.entries)


def _score_to_boost(score: int) -> float:
    """將 gTrend score (0-100) 轉換為加分值。

    Args:
        score: Google Trends 相對搜尋量 0-100。

    Returns:
        對應的加分值。
    """
    for threshold, boost in _BOOST_TIERS:
        if score >= threshold:
            return boost
    return 0.0


def _detect_header_row(lines: List[str]) -> int:
    """偵測 CSV 檔案中實際 header 所在行（跳過 metadata）。

    Args:
        lines: CSV 檔案所有行。

    Returns:
        Header 行的 index（0-based）。
    """
    for i, line in enumerate(lines[:_MAX_METADATA_LINES]):
        stripped = line.strip().lower()
        # Google Trends 匯出的 header 通常含 "keyword" 或日期欄
        if any(marker in stripped for marker in ["keyword", "關鍵字", "搜尋字詞", "search"]):
            return i
        # 嘗試偵測 CSV 格式：至少兩個逗號分隔的欄位且第二欄為數字
        parts = stripped.split(",")
        if len(parts) >= 2:
            try:
                int(parts[1].strip().rstrip("%"))
                # 這行可能已經是數據行，header 在前一行
                return max(0, i - 1) if i > 0 else i
            except ValueError:
                if i == 0 and len(parts) >= 2:
                    return i
    return 0


class GTrendLoader:
    """Google Trends CSV 載入器。

    載入 gTrend 匯出的 CSV，建立關鍵字 → 分數的對照表，
    提供 get_boost() 方法供 Scorer 查詢動態加分。
    """

    def __init__(
        self,
        csv_path: Optional[str] = None,
        csv_dir: Optional[str] = None,
        min_score: int = 30,
    ) -> None:
        """初始化 GTrendLoader。

        Args:
            csv_path: 單一 CSV 檔案路徑。
            csv_dir: CSV 檔案目錄（自動載入所有 .csv）。
            min_score: 最低有效分數（低於此值的關鍵字不加分）。
        """
        self._min_score = min_score
        self._keyword_map: Dict[str, TrendEntry] = {}
        self._snapshots: List[GTrendSnapshot] = []

        if csv_path:
            self.load_csv(csv_path)
        if csv_dir:
            self.load_dir(csv_dir)

    @property
    def keyword_count(self) -> int:
        """已載入的關鍵字數量。"""
        return len(self._keyword_map)

    @property
    def keywords(self) -> List[str]:
        """所有已載入的關鍵字。"""
        return list(self._keyword_map.keys())

    def load_csv(self, csv_path: str) -> GTrendSnapshot:
        """載入單一 gTrend CSV 檔案。

        Args:
            csv_path: CSV 檔案路徑。

        Returns:
            載入的快照。

        Raises:
            FileNotFoundError: 檔案不存在。
            ValueError: CSV 格式無法解析。
        """
        path = Path(csv_path)
        if not path.exists():
            raise FileNotFoundError(f"gTrend CSV not found: {csv_path}")

        raw_lines = path.read_text(encoding="utf-8").splitlines()
        if not raw_lines:
            raise ValueError(f"Empty CSV file: {csv_path}")

        header_idx = _detect_header_row(raw_lines)
        data_lines = raw_lines[header_idx:]

        entries: List[TrendEntry] = []
        reader = csv.reader(data_lines)
        header: Optional[List[str]] = None

        for row_idx, row in enumerate(reader):
            if not row or all(not cell.strip() for cell in row):
                continue

            # 第一個有效行為 header
            if header is None:
                header = [col.strip().lower() for col in row]
                continue

            # 解析數據行
            entry = self._parse_row(row, header, csv_path)
            if entry and entry.score >= self._min_score:
                entries.append(entry)
                # 若關鍵字重複，取最高分
                existing = self._keyword_map.get(entry.keyword)
                if existing is None or entry.score > existing.score:
                    self._keyword_map[entry.keyword] = entry

        snapshot = GTrendSnapshot(
            entries=entries,
            loaded_at=datetime.now(timezone.utc).isoformat(),
            source_path=str(path),
        )
        self._snapshots.append(snapshot)

        logger.info(
            "Loaded %d trend entries from %s (min_score=%d, total keywords=%d)",
            len(entries), csv_path, self._min_score, self.keyword_count,
        )
        return snapshot

    def load_dir(self, csv_dir: str) -> List[GTrendSnapshot]:
        """載入目錄下所有 .csv 檔案。

        Args:
            csv_dir: 目錄路徑。

        Returns:
            所有載入的快照。
        """
        dir_path = Path(csv_dir)
        if not dir_path.is_dir():
            logger.warning("gTrend CSV directory not found: %s", csv_dir)
            return []

        snapshots: List[GTrendSnapshot] = []
        for csv_file in sorted(dir_path.glob("*.csv")):
            try:
                snap = self.load_csv(str(csv_file))
                snapshots.append(snap)
            except (ValueError, FileNotFoundError) as e:
                logger.warning("Skip invalid CSV %s: %s", csv_file, e)

        return snapshots

    def _parse_row(
        self,
        row: List[str],
        header: List[str],
        source: str,
    ) -> Optional[TrendEntry]:
        """解析一行 CSV 數據。

        支援的欄位名稱：
        - keyword / 關鍵字 / 搜尋字詞 / search_term
        - score / 分數 / interest / value / 搜尋量
        - category / 分類 (optional)

        Args:
            row: CSV 行數據。
            header: CSV header 列表。
            source: 來源檔案路徑。

        Returns:
            TrendEntry 或 None（若無法解析）。
        """
        if len(row) < 2:
            return None

        # 找出 keyword 欄位
        kw_idx = self._find_column(header, ["keyword", "關鍵字", "搜尋字詞", "search_term", "search"])
        score_idx = self._find_column(header, ["score", "分數", "interest", "value", "搜尋量"])
        cat_idx = self._find_column(header, ["category", "分類"])

        # Fallback: 第一欄=keyword, 第二欄=score
        if kw_idx is None:
            kw_idx = 0
        if score_idx is None:
            score_idx = 1

        if kw_idx >= len(row) or score_idx >= len(row):
            return None

        keyword = row[kw_idx].strip()
        if not keyword:
            return None

        # 解析分數（可能帶 % 或為空）
        score_raw = row[score_idx].strip().rstrip("%")
        if not score_raw or score_raw == "<1":
            return None
        try:
            score = int(score_raw)
        except ValueError:
            try:
                score = int(float(score_raw))
            except ValueError:
                return None

        score = max(0, min(100, score))
        boost = _score_to_boost(score)
        category = row[cat_idx].strip() if cat_idx is not None and cat_idx < len(row) else ""

        return TrendEntry(
            keyword=keyword,
            score=score,
            boost=boost,
            category=category,
        )

    @staticmethod
    def _find_column(header: List[str], candidates: List[str]) -> Optional[int]:
        """在 header 中搜尋匹配的欄位名稱。"""
        for i, col in enumerate(header):
            for cand in candidates:
                if cand in col:
                    return i
        return None

    def get_boost(self, keyword: str) -> float:
        """查詢關鍵字的動態加分。

        Args:
            keyword: 要查詢的關鍵字。

        Returns:
            加分值（0.0 表示無趨勢或未載入）。
        """
        entry = self._keyword_map.get(keyword)
        return entry.boost if entry else 0.0

    def get_entry(self, keyword: str) -> Optional[TrendEntry]:
        """查詢關鍵字的完整趨勢條目。"""
        return self._keyword_map.get(keyword)

    def match_text(self, text: str) -> List[TrendEntry]:
        """在文本中搜尋所有命中的趨勢關鍵字。

        Args:
            text: 要搜尋的文本。

        Returns:
            命中的趨勢條目列表（依 score 降序）。
        """
        matched: List[TrendEntry] = []
        for keyword, entry in self._keyword_map.items():
            if keyword in text:
                matched.append(entry)
        # 依 score 降序
        matched.sort(key=lambda e: -e.score)
        return matched

    def compute_text_boost(self, text: str, cap: float = 25.0) -> Tuple[float, List[str]]:
        """計算文本的總趨勢加分。

        多關鍵字命中時遞減加分：
        - 第 1 個：full boost
        - 第 2 個：70% boost
        - 第 3 個以後：50% boost
        最終加總 cap 在指定上限。

        Args:
            text: 要計算的文本。
            cap: 加分上限。

        Returns:
            (total_boost, matched_keywords) 元組。
        """
        matched = self.match_text(text)
        if not matched:
            return 0.0, []

        total = 0.0
        keywords: List[str] = []
        for i, entry in enumerate(matched):
            if i == 0:
                multiplier = 1.0
            elif i == 1:
                multiplier = 0.7
            else:
                multiplier = 0.5
            total += entry.boost * multiplier
            keywords.append(entry.keyword)

        return min(total, cap), keywords
