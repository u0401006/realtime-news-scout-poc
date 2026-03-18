"""CNA Agent skill — headline-selection / V2 Scorer 驅動模式。

v2.0 升級（2026-03-18）：
  - 評分引擎由 V1 check-point 關鍵字匹配切換為 V2Scorer
  - V2Scorer 提供浮動門檻、劇烈震盪偵測、gTrend 動態加分、
    IP 精準匹配、經濟震盪偵測、Firebase 即時數據等進階功能
  - 保留 HeadlineVerdict / CheckPointResult 資料結構，確保下游相容
  - evaluate() 介面不變：接受 SitemapEntry + body，回傳 HeadlineVerdict
  - 舊版 V1 check-point 邏輯保留於 evaluate_v1() 供回退使用

v1.1 歷史（2026-03-16）：
  - 新增 cp-ip、cp-novelty、cp-sports、cp-generic-intl
  - 門檻提高至 55

## V2 計分概要

    V2Scorer 綜合下列維度產生 0–100 分：
      - V1 基礎規則繼承（政治、經濟、突發等 topic 權重）
      - 內容型態分類（P0_short / P0_main / P1_followup …）× tier 乘數
      - TSMC 分層 IP 匹配（L1–L4）
      - gTrend 動態加分
      - 經濟劇烈震盪偵測
      - Firebase trending 加分
      - 浮動門檻（滑動視窗 P75 動態調整，震盪保護）

    門檻：score >= effective_threshold（浮動，預設 ≥ 94.0）→ eligible
    映射至 HeadlineVerdict：eligible → selected

設計原則：
- evaluate() 回傳的 HeadlineVerdict 與 V1 結構完全相容。
- reason 包含 V2Scorer 的 headline_reason 與主要命中規則。
- check_points 欄位填入 V2 命中的 matched_rules 轉換結果。
- is_fallback 在無任何 matched_rules 時為 True。
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from typing import List, Optional

from ingestion.adapters.cna_sitemap import SitemapEntry

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# 資料結構（向下相容 V1）
# ---------------------------------------------------------------------------


@dataclass
class CheckPointResult:
    """單一 check-point 的判定結果。

    V2 模式下，每條 matched_rule 映射為一個 CheckPointResult。
    """

    name: str
    triggered: bool
    signal: str  # "select" | "skip" | "neutral"
    detail: str
    weight: int = 0  # V2 模式下為 0（分數由 V2Scorer 整體計算）


@dataclass
class HeadlineVerdict:
    """headline-selection 綜合判定結果。"""

    selected: bool
    reason: str
    score: int  # 0–100（V2 total_score 四捨五入取整）
    check_points: list[CheckPointResult] = field(default_factory=list)
    is_fallback: bool = False
    # V2 擴充欄位
    effective_threshold: float = 0.0
    content_tier: str = ""
    ip_matches: list[str] = field(default_factory=list)
    gtrend_boost: float = 0.0
    economic_boost: float = 0.0


# ---------------------------------------------------------------------------
# V2 Scorer 單例（Lazy Init）
# ---------------------------------------------------------------------------

_v2_scorer_instance = None


def _get_v2_scorer():
    """取得或建立 V2Scorer 單例。

    Lazy init 避免 import 時期就載入重量級依賴。
    """
    global _v2_scorer_instance  # noqa: PLW0603
    if _v2_scorer_instance is None:
        from ranking.model.v2_scorer import V2Scorer

        _v2_scorer_instance = V2Scorer()
        logger.info("V2Scorer singleton initialized for headline_selection adapter")
    return _v2_scorer_instance


def reset_scorer() -> None:
    """重設 V2Scorer 單例（用於測試或重新初始化）。"""
    global _v2_scorer_instance  # noqa: PLW0603
    _v2_scorer_instance = None


def configure_scorer(
    *,
    weights_path: Optional[str] = None,
    gtrend_csv: Optional[str] = None,
    gtrend_dir: Optional[str] = None,
    base_threshold: Optional[float] = None,
    firebase_project_id: Optional[str] = None,
    firebase_cache: Optional[str] = None,
) -> None:
    """以自訂參數初始化 V2Scorer 單例。

    必須在第一次呼叫 evaluate() 之前呼叫，否則會使用預設參數。

    Args:
        weights_path: v1_weights.json 路徑。
        gtrend_csv: gTrend CSV 檔案路徑。
        gtrend_dir: gTrend CSV 目錄。
        base_threshold: 基礎門檻值。
        firebase_project_id: Firebase 專案 ID。
        firebase_cache: Firebase 快取路徑。
    """
    global _v2_scorer_instance  # noqa: PLW0603
    from ranking.model.v2_scorer import V2Scorer

    _v2_scorer_instance = V2Scorer(
        weights_path=weights_path,
        gtrend_csv=gtrend_csv,
        gtrend_dir=gtrend_dir,
        base_threshold=base_threshold,
        firebase_project_id=firebase_project_id,
        firebase_cache=firebase_cache,
    )
    logger.info("V2Scorer singleton configured with custom params")


# ---------------------------------------------------------------------------
# V2 驅動主判斷入口
# ---------------------------------------------------------------------------


def evaluate(
    entry: SitemapEntry,
    body: Optional[str],
) -> HeadlineVerdict:
    """執行 V2Scorer 評分，回傳 HeadlineVerdict（向下相容 V1 介面）。

    Args:
        entry: CNA sitemap 條目（含 title、keywords 等）。
        body: 文章內文（可為 None）。

    Returns:
        HeadlineVerdict，其中：
          - selected: V2Scorer 的 headline_eligible
          - score: V2Scorer 的 total_score（取整）
          - reason: V2Scorer 的 headline_reason + matched_rules 摘要
          - check_points: matched_rules 轉換為 CheckPointResult 列表
          - effective_threshold: V2 浮動門檻值
          - content_tier: 內容型態分類名稱
          - ip_matches: IP 精準匹配命中列表
    """
    scorer = _get_v2_scorer()

    # V2Scorer.score() 接受 title, topic_tags, summary_text 等
    result = scorer.score(
        title=entry.title,
        topic_tags=entry.keywords,
        summary_text=body or "",
    )

    # 將 matched_rules 映射為 CheckPointResult
    check_points: list[CheckPointResult] = []
    for rule in result.matched_rules:
        check_points.append(
            CheckPointResult(
                name=rule,
                triggered=True,
                signal="select" if result.headline_eligible else "neutral",
                detail=rule,
                weight=0,
            ),
        )

    is_fallback = len(result.matched_rules) == 0
    score_int = round(result.total_score)

    # 組裝 reason
    reason_parts: list[str] = [result.headline_reason]
    if result.matched_rules:
        rules_summary = ", ".join(result.matched_rules[:5])
        if len(result.matched_rules) > 5:
            rules_summary += f" … (+{len(result.matched_rules) - 5})"
        reason_parts.append(f"rules=[{rules_summary}]")
    if is_fallback:
        reason_parts.append("[fallback] 無 matched_rules")

    reason = " | ".join(reason_parts)

    # 取得 content_tier 名稱
    tier_name = ""
    if hasattr(result, "content_tier") and result.content_tier is not None:
        tier_name = (
            result.content_tier.name
            if hasattr(result.content_tier, "name")
            else str(result.content_tier)
        )

    # 取得 ip_matches
    ip_matches: list[str] = []
    if hasattr(result, "ip_strict_matches"):
        ip_matches = list(result.ip_strict_matches)

    # 取得 gtrend / economic boost
    gtrend_boost = getattr(result, "gtrend_boost", 0.0)
    economic_boost = getattr(result, "economic_boost", 0.0)

    # 取得 effective_threshold
    effective_threshold = getattr(result, "effective_threshold", 0.0)

    return HeadlineVerdict(
        selected=result.headline_eligible,
        reason=reason,
        score=score_int,
        check_points=check_points,
        is_fallback=is_fallback,
        effective_threshold=effective_threshold,
        content_tier=tier_name,
        ip_matches=ip_matches,
        gtrend_boost=gtrend_boost,
        economic_boost=economic_boost,
    )


# ---------------------------------------------------------------------------
# V1 Legacy（保留供回退或 A/B 測試）
# ---------------------------------------------------------------------------

# V1 計分常數
_V1_BASE_SCORE: int = 50
_V1_SELECT_THRESHOLD: int = 55

_V1_CP_WEIGHTS: dict[str, int] = {
    "cp-breaking": 30,
    "cp-political": 25,
    "cp-economic": 20,
    "cp-international": 15,
    "cp-ip": 20,
    "cp-novelty": 15,
    "cp-sports": 10,
    "cp-category": -25,
    "cp-completeness": -30,
    "cp-generic-intl": -15,
}

# V1 關鍵字清單
_V1_BREAKING_KEYWORDS: list[str] = [
    "爆炸", "傷亡", "地震", "颱風", "海嘯", "墜機",
    "槍擊", "恐攻", "核災", "疫情", "封城", "戒嚴",
    "火警", "衝突", "落石", "骨折", "開槍", "追捕",
    "事故", "罹難", "搜救", "直升機",
]

_V1_POLITICAL_KEYWORDS: list[str] = [
    "總統", "行政院", "立法院", "監察院", "司法院",
    "選舉", "罷免", "彈劾", "修憲", "公投",
    "國防", "外交", "兩岸", "內政部", "財政部",
    "卓榮泰", "管碧玲", "國土安全",
]

_V1_ECONOMIC_KEYWORDS: list[str] = [
    "半導體", "台積電", "AI", "人工智慧",
    "股市", "央行", "利率", "通膨", "GDP",
    "貿易", "關稅", "制裁", "薪資", "營收",
    "增資", "量產",
]

_V1_INTERNATIONAL_KEYWORDS: list[str] = [
    "以色列", "真主黨", "伊朗", "烏克蘭", "俄羅斯",
    "北約", "聯合國", "G7", "G20", "APEC",
]

_V1_IP_KEYWORDS: list[str] = [
    "台積電", "鴻海", "中華電", "聯發科", "NVIDIA",
    "Apple", "Google", "Tesla", "TSMC",
    "騰輝", "永豐餘", "寶可夢",
    "GTC", "人形機器人", "AI伺服器",
]

_V1_NOVELTY_KEYWORDS: list[str] = [
    "首次", "首度", "創紀錄", "突破", "新種", "命名",
    "史上", "最高", "最大", "翻倍", "里程碑",
]

_V1_SPORTS_KEYWORDS: list[str] = [
    "奧運", "世足", "WBC", "MLB", "NBA", "世界盃",
    "亞運", "大聯盟", "冬奧",
]

_V1_GENERIC_INTL_KEYWORDS: list[str] = [
    "中國", "美國", "日本", "歐盟",
]

_V1_LOW_PRIORITY_CATEGORIES: set[str] = {
    "entertainment", "sport", "lifestyle",
}

_V1_MIN_BODY_LENGTH = 100


def _v1_check_keywords(
    keywords: list[str],
    title: str,
    body: Optional[str],
) -> Optional[str]:
    """V1: 檢查標題或內文是否包含任一關鍵字。"""
    for kw in keywords:
        if kw in title:
            return f"標題含「{kw}」"
    if body:
        for kw in keywords:
            if kw in body:
                return f"內文含「{kw}」"
    return None


def evaluate_v1(
    entry: SitemapEntry,
    body: Optional[str],
) -> HeadlineVerdict:
    """V1 Legacy 評估入口（check-point 關鍵字匹配模式）。

    保留原始 V1 邏輯，供回退或 A/B 對照使用。
    計分：score = clamp(50 + Σ triggered weights, 0, 100)
    門檻：score >= 55 → selected
    """
    results: list[CheckPointResult] = []

    # CP-1: 突發
    hit = _v1_check_keywords(_V1_BREAKING_KEYWORDS, entry.title, body)
    results.append(CheckPointResult(
        name="cp-breaking", triggered=bool(hit), signal="select" if hit else "neutral",
        detail=f"突發事件指標：{hit}" if hit else "未觸發",
        weight=_V1_CP_WEIGHTS["cp-breaking"] if hit else 0,
    ))

    # CP-2: 政治
    hit = _v1_check_keywords(_V1_POLITICAL_KEYWORDS, entry.title, body)
    results.append(CheckPointResult(
        name="cp-political", triggered=bool(hit), signal="select" if hit else "neutral",
        detail=f"政治顯著性：{hit}" if hit else "未觸發",
        weight=_V1_CP_WEIGHTS["cp-political"] if hit else 0,
    ))

    # CP-3: 經濟
    hit = _v1_check_keywords(_V1_ECONOMIC_KEYWORDS, entry.title, body)
    results.append(CheckPointResult(
        name="cp-economic", triggered=bool(hit), signal="select" if hit else "neutral",
        detail=f"經濟影響力：{hit}" if hit else "未觸發",
        weight=_V1_CP_WEIGHTS["cp-economic"] if hit else 0,
    ))

    # CP-4: 國際
    hit = _v1_check_keywords(_V1_INTERNATIONAL_KEYWORDS, entry.title, body)
    results.append(CheckPointResult(
        name="cp-international", triggered=bool(hit), signal="select" if hit else "neutral",
        detail=f"國際關注度：{hit}" if hit else "未觸發",
        weight=_V1_CP_WEIGHTS["cp-international"] if hit else 0,
    ))

    # CP-7: IP
    hit = _v1_check_keywords(_V1_IP_KEYWORDS, entry.title, body)
    results.append(CheckPointResult(
        name="cp-ip", triggered=bool(hit), signal="select" if hit else "neutral",
        detail=f"IP/知名品牌：{hit}" if hit else "未觸發",
        weight=_V1_CP_WEIGHTS["cp-ip"] if hit else 0,
    ))

    # CP-8: 新奇性
    hit = _v1_check_keywords(_V1_NOVELTY_KEYWORDS, entry.title, body)
    results.append(CheckPointResult(
        name="cp-novelty", triggered=bool(hit), signal="select" if hit else "neutral",
        detail=f"新奇性：{hit}" if hit else "未觸發",
        weight=_V1_CP_WEIGHTS["cp-novelty"] if hit else 0,
    ))

    # CP-9: 運動
    hit = _v1_check_keywords(_V1_SPORTS_KEYWORDS, entry.title, body)
    results.append(CheckPointResult(
        name="cp-sports", triggered=bool(hit), signal="select" if hit else "neutral",
        detail=f"高關注賽事：{hit}" if hit else "未觸發",
        weight=_V1_CP_WEIGHTS["cp-sports"] if hit else 0,
    ))

    # CP-5: 低優先類別
    entry_cats = {k.lower() for k in entry.keywords}
    is_low = bool(entry_cats and entry_cats.issubset(_V1_LOW_PRIORITY_CATEGORIES))
    results.append(CheckPointResult(
        name="cp-category", triggered=is_low, signal="skip" if is_low else "neutral",
        detail=f"類別皆為低優先（{', '.join(entry.keywords)}）" if is_low else "類別非低優先",
        weight=_V1_CP_WEIGHTS["cp-category"] if is_low else 0,
    ))

    # CP-6: 完整度
    if body is None:
        results.append(CheckPointResult(
            name="cp-completeness", triggered=True, signal="skip",
            detail="無法取得內文", weight=_V1_CP_WEIGHTS["cp-completeness"],
        ))
    elif len(body) < _V1_MIN_BODY_LENGTH:
        results.append(CheckPointResult(
            name="cp-completeness", triggered=True, signal="skip",
            detail=f"內文過短（{len(body)} 字 < {_V1_MIN_BODY_LENGTH}）",
            weight=_V1_CP_WEIGHTS["cp-completeness"],
        ))
    else:
        results.append(CheckPointResult(
            name="cp-completeness", triggered=False, signal="neutral",
            detail=f"內文長度充足（{len(body)} 字）",
        ))

    # CP-10: 泛國際
    hit = _v1_check_keywords(_V1_GENERIC_INTL_KEYWORDS, entry.title, None)
    results.append(CheckPointResult(
        name="cp-generic-intl", triggered=bool(hit), signal="skip" if hit else "neutral",
        detail=f"泛國際匹配：{hit}" if hit else "未觸發",
        weight=_V1_CP_WEIGHTS["cp-generic-intl"] if hit else 0,
    ))

    # 泛國際特殊邏輯：有正向 → 取消扣分
    has_positive = any(r.triggered and r.signal == "select" for r in results)
    for r in results:
        if r.name == "cp-generic-intl" and r.triggered and has_positive:
            r.triggered = False
            r.weight = 0
            r.detail = "泛國際匹配已被正向信號抵銷"

    # 計分
    delta = sum(r.weight for r in results if r.triggered)
    score = max(0, min(100, _V1_BASE_SCORE + delta))

    triggered = [r for r in results if r.triggered]
    if not triggered:
        return HeadlineVerdict(
            selected=True,
            reason=f"[fallback] 所有 check-points 皆未觸發，預設選入（score={score}）",
            score=score,
            check_points=results,
            is_fallback=True,
        )

    parts: list[str] = []
    for r in triggered:
        sign = "+" if r.weight >= 0 else ""
        parts.append(f"[{r.name} {sign}{r.weight}] {r.detail}")
    reason = "; ".join(parts)

    return HeadlineVerdict(
        selected=score >= _V1_SELECT_THRESHOLD,
        reason=reason,
        score=score,
        check_points=results,
        is_fallback=False,
    )
