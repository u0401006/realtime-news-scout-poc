"""CNA Agent skill — headline-selection / check-points 主判斷。

提供結構化檢查點（check-points）對候選稿進行新聞價值評估，
取代原先的純關鍵字匹配邏輯。每個 check-point 回傳獨立判定與理由，
最終由 evaluate() 綜合所有 check-point 結果做出選稿決策。

## 計分公式（score: 0–100）

    score = clamp(BASE + Σ(triggered check-point weights), 0, 100)

    BASE = 50（中性基線，代表「資訊不足以明確判斷」）

    正向 check-points（signal="select"）：
      cp-breaking       +30   突發 / 重大事件
      cp-political      +25   政治顯著性
      cp-economic        +20   經濟影響力
      cp-international  +15   國際關注度

    負向 check-points（signal="skip"）：
      cp-category       −25   低優先類別
      cp-completeness   −30   內容缺失 / 過短

    門檻：score >= 50 → selected=True；< 50 → selected=False
    （若所有 check-points 皆 neutral，score=50，走 fallback 選入）

設計原則：
- 每筆輸出的 reason 必須對應具體 check-point 名稱與判斷依據。
- 若所有 check-point 皆無法判定，才走 fallback 並在 reason 中註記。
- 不實作 hard reject（保持寬鬆選入策略）。
- 不實作 Top N 排名（僅做 selected / not-selected 二元判斷 + score）。
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Optional

from ingestion.adapters.cna_sitemap import SitemapEntry

# ---------------------------------------------------------------------------
# 計分常數
# ---------------------------------------------------------------------------

BASE_SCORE: int = 50
SELECT_THRESHOLD: int = 50  # >= 此值選入

# check-point 權重（正=加分，負=扣分）
CP_WEIGHTS: dict[str, int] = {
    "cp-breaking": 30,
    "cp-political": 25,
    "cp-economic": 20,
    "cp-international": 15,
    "cp-category": -25,
    "cp-completeness": -30,
}


# ---------------------------------------------------------------------------
# Check-point 定義
# ---------------------------------------------------------------------------

@dataclass
class CheckPointResult:
    """單一 check-point 的判定結果。"""

    name: str
    triggered: bool
    signal: str  # "select" | "skip" | "neutral"
    detail: str
    weight: int = 0  # 實際貢獻分數（triggered 時）


@dataclass
class HeadlineVerdict:
    """headline-selection 綜合判定結果。"""

    selected: bool
    reason: str
    score: int  # 0–100
    check_points: list[CheckPointResult] = field(default_factory=list)
    is_fallback: bool = False


# ---------------------------------------------------------------------------
# Check-point 規則定義
# ---------------------------------------------------------------------------

# CP-1: 突發 / 重大事件指標
BREAKING_KEYWORDS: list[str] = [
    "爆炸", "傷亡", "地震", "颱風", "海嘯", "墜機",
    "槍擊", "恐攻", "核災", "疫情", "封城", "戒嚴",
]

# CP-2: 政治顯著性
POLITICAL_KEYWORDS: list[str] = [
    "總統", "行政院", "立法院", "監察院", "司法院",
    "選舉", "罷免", "彈劾", "修憲", "公投",
    "國防", "外交", "兩岸",
]

# CP-3: 經濟影響力
ECONOMIC_KEYWORDS: list[str] = [
    "半導體", "台積電", "AI", "人工智慧",
    "股市", "央行", "利率", "通膨", "GDP",
    "貿易", "關稅", "制裁",
]

# CP-4: 國際關注度
INTERNATIONAL_KEYWORDS: list[str] = [
    "中國", "美國", "日本", "歐盟", "北約",
    "聯合國", "G7", "G20", "APEC",
    "俄羅斯", "烏克蘭", "以色列",
]

# CP-5: 低優先類別（sitemap keywords）
LOW_PRIORITY_CATEGORIES: set[str] = {
    "entertainment",
    "sport",
    "lifestyle",
}

# CP-6: 內容完整度門檻
MIN_BODY_LENGTH = 100


def _check_keywords_in_text(
    keywords: list[str],
    title: str,
    body: Optional[str],
) -> Optional[str]:
    """檢查標題或內文是否包含任一關鍵字，回傳首個命中或 None。"""
    for kw in keywords:
        if kw in title:
            return f"標題含「{kw}」"
    if body:
        for kw in keywords:
            if kw in body:
                return f"內文含「{kw}」"
    return None


# ---------------------------------------------------------------------------
# 個別 check-point 評估函式
# ---------------------------------------------------------------------------

def cp_breaking_news(
    entry: SitemapEntry,
    body: Optional[str],
) -> CheckPointResult:
    """CP-1: 突發 / 重大事件。"""
    hit = _check_keywords_in_text(BREAKING_KEYWORDS, entry.title, body)
    if hit:
        return CheckPointResult(
            name="cp-breaking",
            triggered=True,
            signal="select",
            detail=f"突發事件指標：{hit}",
            weight=CP_WEIGHTS["cp-breaking"],
        )
    return CheckPointResult(
        name="cp-breaking", triggered=False, signal="neutral",
        detail="未觸發突發事件指標",
    )


def cp_political_significance(
    entry: SitemapEntry,
    body: Optional[str],
) -> CheckPointResult:
    """CP-2: 政治顯著性。"""
    hit = _check_keywords_in_text(POLITICAL_KEYWORDS, entry.title, body)
    if hit:
        return CheckPointResult(
            name="cp-political",
            triggered=True,
            signal="select",
            detail=f"政治顯著性：{hit}",
            weight=CP_WEIGHTS["cp-political"],
        )
    return CheckPointResult(
        name="cp-political", triggered=False, signal="neutral",
        detail="未觸發政治顯著性指標",
    )


def cp_economic_impact(
    entry: SitemapEntry,
    body: Optional[str],
) -> CheckPointResult:
    """CP-3: 經濟影響力。"""
    hit = _check_keywords_in_text(ECONOMIC_KEYWORDS, entry.title, body)
    if hit:
        return CheckPointResult(
            name="cp-economic",
            triggered=True,
            signal="select",
            detail=f"經濟影響力：{hit}",
            weight=CP_WEIGHTS["cp-economic"],
        )
    return CheckPointResult(
        name="cp-economic", triggered=False, signal="neutral",
        detail="未觸發經濟影響力指標",
    )


def cp_international_relevance(
    entry: SitemapEntry,
    body: Optional[str],
) -> CheckPointResult:
    """CP-4: 國際關注度。"""
    hit = _check_keywords_in_text(INTERNATIONAL_KEYWORDS, entry.title, body)
    if hit:
        return CheckPointResult(
            name="cp-international",
            triggered=True,
            signal="select",
            detail=f"國際關注度：{hit}",
            weight=CP_WEIGHTS["cp-international"],
        )
    return CheckPointResult(
        name="cp-international", triggered=False, signal="neutral",
        detail="未觸發國際關注度指標",
    )


def cp_category_priority(
    entry: SitemapEntry,
    _body: Optional[str],
) -> CheckPointResult:
    """CP-5: 類別優先度（依 sitemap keywords）。"""
    entry_cats = {k.lower() for k in entry.keywords}
    if entry_cats and entry_cats.issubset(LOW_PRIORITY_CATEGORIES):
        return CheckPointResult(
            name="cp-category",
            triggered=True,
            signal="skip",
            detail=f"類別皆為低優先（{', '.join(entry.keywords)}）",
            weight=CP_WEIGHTS["cp-category"],
        )
    return CheckPointResult(
        name="cp-category", triggered=False, signal="neutral",
        detail="類別非低優先",
    )


def cp_content_completeness(
    entry: SitemapEntry,
    body: Optional[str],
) -> CheckPointResult:
    """CP-6: 內容完整度。"""
    if body is None:
        return CheckPointResult(
            name="cp-completeness",
            triggered=True,
            signal="skip",
            detail="無法取得內文",
            weight=CP_WEIGHTS["cp-completeness"],
        )
    if len(body) < MIN_BODY_LENGTH:
        return CheckPointResult(
            name="cp-completeness",
            triggered=True,
            signal="skip",
            detail=f"內文過短（{len(body)} 字 < {MIN_BODY_LENGTH}）",
            weight=CP_WEIGHTS["cp-completeness"],
        )
    return CheckPointResult(
        name="cp-completeness", triggered=False, signal="neutral",
        detail=f"內文長度充足（{len(body)} 字）",
    )


# ---------------------------------------------------------------------------
# 所有 check-point 函式（按執行順序）
# ---------------------------------------------------------------------------

ALL_CHECKPOINTS = [
    cp_breaking_news,
    cp_political_significance,
    cp_economic_impact,
    cp_international_relevance,
    cp_category_priority,
    cp_content_completeness,
]


# ---------------------------------------------------------------------------
# 主判斷入口
# ---------------------------------------------------------------------------

def evaluate(
    entry: SitemapEntry,
    body: Optional[str],
) -> HeadlineVerdict:
    """執行所有 check-points，計算 score 並綜合判定選稿。

    計分：score = clamp(BASE_SCORE + Σ triggered weights, 0, 100)
    門檻：score >= SELECT_THRESHOLD → selected
    """
    results: list[CheckPointResult] = []
    for cp_fn in ALL_CHECKPOINTS:
        result = cp_fn(entry, body)
        results.append(result)

    # 計算分數
    delta = sum(r.weight for r in results if r.triggered)
    score = max(0, min(100, BASE_SCORE + delta))

    # 收集觸發的 check-points 做 reason
    triggered = [r for r in results if r.triggered]

    if not triggered:
        # 全部 neutral → fallback
        return HeadlineVerdict(
            selected=True,
            reason=f"[fallback] 所有 check-points 皆未觸發，預設選入（score={score}）",
            score=score,
            check_points=results,
            is_fallback=True,
        )

    # 組裝 reason：列出所有觸發的 check-point
    parts: list[str] = []
    for r in triggered:
        sign = "+" if r.weight >= 0 else ""
        parts.append(f"[{r.name} {sign}{r.weight}] {r.detail}")
    reason = "; ".join(parts)

    selected = score >= SELECT_THRESHOLD

    return HeadlineVerdict(
        selected=selected,
        reason=reason,
        score=score,
        check_points=results,
        is_fallback=False,
    )
