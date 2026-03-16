"""SkillEvo v1 — 訓練資料 Schema 定義。

定義 positive / negative / unlabeled 三類標註資料結構，
可由 smoke test 結果自動回填，也支援人工標註。

欄位設計參照 headline_selection 的 HeadlineVerdict 與 SitemapEntry，
確保 ingestion → training_data 可無縫轉換。
"""

from __future__ import annotations

import enum
from datetime import datetime
from typing import Optional

from pydantic import BaseModel, Field


class Label(str, enum.Enum):
    """標註類別。"""

    POSITIVE = "positive"    # 應選入（editor 採用 / smoke 高分 + 人工確認）
    NEGATIVE = "negative"    # 應排除（editor 拒絕 / smoke 低分 + 人工確認）
    UNLABELED = "unlabeled"  # 尚未標註（smoke 產出但未經人工確認）


class TrainingSample(BaseModel):
    """單筆訓練樣本。

    Attributes:
        pid: CNA 稿件 ID（如 202603160156）。
        url: 原始稿件 URL。
        title: 標題。
        published_at: 發佈時間（ISO-8601）。
        keywords: sitemap 分類關鍵字。
        body_length: 內文字數（0 表示未取得）。
        score: 選稿分數（0–100，rule-based 或模型輸出）。
        reason: 選稿理由（check-point 摘要或模型解釋）。
        label: 標註類別。
        label_source: 標註來源（"smoke_auto" / "editor" / "manual"）。
        labeled_at: 標註時間。
        editor_note: 編輯備註（選填）。
    """

    pid: str
    url: str
    title: str
    published_at: datetime
    keywords: list[str] = Field(default_factory=list)
    body_length: int = 0
    score: int = Field(ge=0, le=100)
    reason: str = ""
    label: Label = Label.UNLABELED
    label_source: str = "smoke_auto"
    labeled_at: Optional[datetime] = None
    editor_note: Optional[str] = None

    model_config = {"json_schema_extra": {
        "examples": [
            {
                "pid": "202603160001",
                "url": "https://www.cna.com.tw/news/afe/202603160001.aspx",
                "title": "台積電宣布新一代 2nm 晶片量產時程提前至 Q3",
                "published_at": "2026-03-16T09:15:00+08:00",
                "keywords": ["business", "economy", "stock", "taiwan"],
                "body_length": 820,
                "score": 70,
                "reason": "[cp-economic +20] 經濟影響力：內文含「半導體」",
                "label": "positive",
                "label_source": "smoke_auto",
                "labeled_at": "2026-03-16T18:00:00+08:00",
                "editor_note": None,
            }
        ]
    }}
