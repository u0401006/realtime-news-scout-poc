#!/usr/bin/env python3
"""Smoke test：CNA 一小時視窗選稿（V2 格式輸出）。

V2 變更：
  - 重構為 SmokeCnaWindow 類別，支援 score_events() / _generate_dry_run_events()
  - 輸出改為嵌套 V2 格式：content / scoring / classification / metadata
  - 評分引擎由 V1 check-point 切換為 V2Scorer（透過 headline_selection.evaluate）
  - V2Scorer 提供浮動門檻、gTrend 動態加分、IP 精準匹配、經濟震盪偵測
  - 排序邏輯：score 降序 → tier 升序（同分時 P0_short 優先）
  - CLI main() 保持向下相容

## V2 JSONL 輸出格式（每行一筆）：

    {
      "content": {
        "title": "...",
        "url": "...",
        "pid": "...",
        "publishedAt": "...",
        "keywords": [...],
        "hasBody": true,
        "bodyLength": 1234
      },
      "scoring": {
        "newsValue": 75,
        "selected": true,
        "reason": "...",
        "isFallback": false,
        "checkPoints": [...],
        "effectiveThreshold": 94.0,
        "gtrendBoost": 5.0,
        "economicBoost": 0.0,
        "ipMatches": ["台積電"]
      },
      "classification": {
        "contentTier": "P0_main",
        "tierReason": "主體報導（無特殊標記）"
      },
      "metadata": {
        "scorerVersion": "v2",
        "windowStart": "...",
        "windowEnd": "..."
      }
    }

用法範例：
    python ingestion/scripts/smoke_cna_window.py \\
        --start 2026-03-16T15:00:00+08:00 \\
        --end   2026-03-16T16:00:00+08:00 \\
        --output output/smoke_result.jsonl
"""

from __future__ import annotations

import argparse
import json
import sys
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional

# 讓 imports 能從專案根目錄載入
_PROJECT_ROOT = Path(__file__).resolve().parent.parent.parent
sys.path.insert(0, str(_PROJECT_ROOT))

from ingestion.adapters.cna_article import get_article  # noqa: E402
from ingestion.adapters.cna_sitemap import (  # noqa: E402
    SitemapEntry,
    fetch_sitemap,
    filter_by_window,
)
from ingestion.adapters.headline_selection import evaluate  # noqa: E402
from ranking.model.v1_scorer import ContentTier  # noqa: E402


# ---------------------------------------------------------------------------
# SmokeCnaWindow — V2 Class-based 主體
# ---------------------------------------------------------------------------


class SmokeCnaWindow:
    """CNA 時間窗選稿 Smoke Test（V2 格式輸出）。

    提供兩種使用方式：
    1. CLI：透過 main() 從 sitemap 實際抓取
    2. API：透過 score_events() 評分已組裝的事件清單
    """

    SCORER_VERSION = "v2"

    def __init__(
        self,
        window_start: Optional[str] = None,
        window_end: Optional[str] = None,
    ) -> None:
        self._window_start = window_start
        self._window_end = window_end

    # ------------------------------------------------------------------
    # V2 Record Builder
    # ------------------------------------------------------------------

    @staticmethod
    def build_v2_record(
        entry: SitemapEntry,
        full_text: Optional[str],
        selected: bool,
        reason: str,
        score: int,
        check_points: Optional[List[Dict[str, Any]]] = None,
        is_fallback: bool = False,
        content_tier: str = "",
        tier_reason: str = "",
        effective_threshold: float = 0.0,
        gtrend_boost: float = 0.0,
        economic_boost: float = 0.0,
        ip_matches: Optional[List[str]] = None,
        window_start: str = "",
        window_end: str = "",
    ) -> Dict[str, Any]:
        """組裝單筆 V2 嵌套 JSONL record。

        Args:
            content_tier: 內容型態分類名稱（字串，例如 "P0_main"）。
            tier_reason: 分類依據說明。
            effective_threshold: V2 浮動門檻值。
            gtrend_boost: gTrend 動態加分。
            economic_boost: 經濟震盪加分。
            ip_matches: IP 精準匹配命中列表。
        """
        return {
            "content": {
                "title": entry.title,
                "url": entry.url,
                "pid": entry.pid,
                "publishedAt": entry.published_at.isoformat(),
                "keywords": entry.keywords,
                "hasBody": full_text is not None,
                "bodyLength": len(full_text) if full_text else 0,
            },
            "scoring": {
                "newsValue": score,
                "selected": selected,
                "reason": reason,
                "isFallback": is_fallback,
                "checkPoints": check_points or [],
                "effectiveThreshold": effective_threshold,
                "gtrendBoost": gtrend_boost,
                "economicBoost": economic_boost,
                "ipMatches": ip_matches or [],
            },
            "classification": {
                "contentTier": content_tier,
                "tierReason": tier_reason,
            },
            "metadata": {
                "scorerVersion": SmokeCnaWindow.SCORER_VERSION,
                "windowStart": window_start,
                "windowEnd": window_end,
            },
        }

    # ------------------------------------------------------------------
    # Dry-run 事件產生器（用於測試）
    # ------------------------------------------------------------------

    def _generate_dry_run_events(self) -> List[Dict[str, Any]]:
        """產生一組模擬事件供 score_events 測試用。

        包含各種 content_tier 類型的標題樣本。
        """
        tz = timezone(timedelta(hours=8))
        base_dt = datetime(2026, 3, 16, 15, 0, 0, tzinfo=tz)

        samples = [
            # P0_short: 快訊
            {
                "title": "快訊：花蓮外海發生規模5.8地震",
                "body": "花蓮外海今日下午發生規模5.8地震，震央深度15公里，全台有感。中央氣象署表示目前無海嘯威脅。",
                "keywords": ["國內"],
            },
            # P0_main: 主體報導
            {
                "title": "台積電宣布2奈米製程良率突破90% 全球半導體業關注",
                "body": "台灣積體電路製造公司今日宣布，其2奈米先進製程良率已突破90%大關，超前業界預期。此消息帶動相關供應鏈股價走揚，輝達等大客戶均已確認追加訂單。分析師表示這將鞏固台積電在全球代工市場的領先地位。",
                "keywords": ["科技", "財經"],
            },
            # P1_followup: 後續追蹤
            {
                "title": "花蓮強震最新：國軍動員搜救 確認3人受困待救",
                "body": "今日花蓮強震後續，國軍已動員第二作戰區兵力投入搜救任務。花蓮縣消防局指出，目前確認有3人受困於秀林鄉山區步道，搜救隊已出發前往。",
                "keywords": ["國內"],
            },
            # P2_response: 回應稿
            {
                "title": "外交部回應中國軍演：嚴正關切並呼籲國際社會共同譴責",
                "body": "外交部今日針對中國解放軍再度在台海周邊舉行軍事演習一事發表聲明，表示嚴正關切此一片面破壞區域穩定的行為，並呼籲國際社會共同譴責。",
                "keywords": ["政治", "國際"],
            },
            # P3_analysis: 分析稿
            {
                "title": "分析：美中科技戰升溫對台灣半導體供應鏈的長期衝擊評估",
                "body": "隨著美中科技戰持續升溫，台灣作為全球半導體製造樞紐的角色愈加關鍵。本文深入分析出口管制、技術封鎖與供應鏈重組對台灣企業的多層面影響，並探討可能的因應策略。",
                "keywords": ["科技", "國際"],
            },
            # P0_main: 突發（cp-breaking 觸發）
            {
                "title": "台中市中清路發生重大車禍 已知2死5傷",
                "body": "台中市北屯區中清路今日下午發生一起重大交通事故，一輛聯結車失控撞上多輛小客車，造成2人當場死亡、5人輕重傷送醫。警方已封鎖現場進行調查。",
                "keywords": ["社會"],
            },
            # P0_main: 政治
            {
                "title": "總統賴清德召開國安會議 討論台海局勢因應方案",
                "body": "總統賴清德今日上午緊急召開國家安全會議，針對近期中國軍事動態與台海局勢進行研判，並討論國防部提出的多項因應方案。與會者包括國防部長、外交部長及國安會秘書長。",
                "keywords": ["政治"],
            },
            # P0_short: 極短標題
            {
                "title": "台股收盤大漲350點",
                "body": "台灣加權股價指數今日收盤大漲350點，成交量突破4000億元，外資大幅買超200億元。",
                "keywords": ["財經"],
            },
        ]

        events: List[Dict[str, Any]] = []
        for i, sample in enumerate(samples):
            entry = SitemapEntry(
                url=f"https://www.cna.com.tw/news/test/20260316{i:04d}.aspx",
                pid=f"20260316{i:04d}",
                title=sample["title"],
                published_at=base_dt + timedelta(minutes=i * 5),
                keywords=sample["keywords"],
            )
            events.append({
                "_entry": entry,
                "_body": sample["body"],
            })
        return events

    # ------------------------------------------------------------------
    # 核心評分 + V2 輸出
    # ------------------------------------------------------------------

    def score_events(
        self,
        events: List[Dict[str, Any]],
    ) -> List[Dict[str, Any]]:
        """對事件清單進行評分，輸出 V2 格式並依 score 降序 + tier 升序排列。

        Args:
            events: 事件清單，每筆含 `_entry` (SitemapEntry) 和 `_body` (str)。

        Returns:
            V2 格式的已排序 record 清單。
        """
        records: List[Dict[str, Any]] = []

        for ev in events:
            entry: SitemapEntry = ev["_entry"]
            body: Optional[str] = ev.get("_body")

            # 使用 headline_selection.evaluate（V2Scorer 驅動）
            verdict = evaluate(entry, body)

            # 序列化 check-points
            cp_list = []
            for cp in verdict.check_points:
                cp_list.append({
                    "name": cp.name,
                    "triggered": cp.triggered,
                    "signal": cp.signal,
                    "detail": cp.detail,
                    "weight": cp.weight,
                })

            # content_tier 來自 verdict（V2Scorer 已內建分類）
            content_tier = verdict.content_tier or ""
            # tier_reason: V2 不再分開回傳 tier_reason，
            # 從 matched_rules 中提取或使用 content_tier 名稱
            tier_reason = f"V2Scorer 分類: {content_tier}" if content_tier else ""

            rec = self.build_v2_record(
                entry=entry,
                full_text=body,
                selected=verdict.selected,
                reason=verdict.reason,
                score=verdict.score,
                check_points=cp_list,
                is_fallback=verdict.is_fallback,
                content_tier=content_tier,
                tier_reason=tier_reason,
                effective_threshold=verdict.effective_threshold,
                gtrend_boost=verdict.gtrend_boost,
                economic_boost=verdict.economic_boost,
                ip_matches=verdict.ip_matches,
                window_start=self._window_start or "",
                window_end=self._window_end or "",
            )
            records.append(rec)

        # 排序：score 降序 → tier 升序（同分時 P0_short 排最前）
        # 使用 ContentTier enum 的 value 排序（若 tier 不存在預設 P0_main = 1）
        def _sort_key(r: Dict[str, Any]) -> tuple:
            tier_name = r["classification"]["contentTier"]
            try:
                tier_val = ContentTier[tier_name].value
            except (KeyError, ValueError):
                tier_val = 1  # 預設 P0_main
            return (-r["scoring"]["newsValue"], tier_val)

        records.sort(key=_sort_key)

        return records

    # ------------------------------------------------------------------
    # 從 sitemap 抓取 + 評分（CLI 用）
    # ------------------------------------------------------------------

    def run_from_sitemap(
        self,
        start_dt: datetime,
        end_dt: datetime,
        delay: float = 0.5,
    ) -> List[Dict[str, Any]]:
        """從 CNA sitemap 抓取時間窗內稿件並評分。

        Returns:
            V2 格式的已排序 record 清單。
        """
        self._window_start = start_dt.isoformat()
        self._window_end = end_dt.isoformat()

        print(f"⏱  時間窗：{start_dt.isoformat()} → {end_dt.isoformat()}", file=sys.stderr)

        # Step 1: 抓 sitemap
        print("📡 正在讀取 CNA sitemap …", file=sys.stderr)
        all_entries = fetch_sitemap()
        print(f"   sitemap 共 {len(all_entries)} 筆", file=sys.stderr)

        # Step 2: 過濾時間窗
        windowed = filter_by_window(all_entries, start_dt, end_dt)
        print(f"   時間窗內 {len(windowed)} 筆", file=sys.stderr)

        if not windowed:
            print("⚠️  時間窗內無稿件，結束。", file=sys.stderr)
            return []

        # Step 3: 逐篇補內文 → 組裝事件
        events: List[Dict[str, Any]] = []
        for i, entry in enumerate(windowed, 1):
            print(
                f"   [{i}/{len(windowed)}] {entry.pid} {entry.title[:30]}…",
                file=sys.stderr,
            )
            article = get_article(entry.pid, entry.url)
            full_text = article.full_text if article else None

            events.append({
                "_entry": entry,
                "_body": full_text,
            })

            if i < len(windowed):
                time.sleep(delay)

        # Step 4: 評分 + 排序
        return self.score_events(events)


# ---------------------------------------------------------------------------
# CLI Summary 輸出
# ---------------------------------------------------------------------------


def _print_summary(
    records: List[Dict[str, Any]],
    start_dt: datetime,
    end_dt: datetime,
    sitemap_total: int,
    output_path: Optional[Path],
) -> None:
    """印出 V2 格式的摘要報告。"""
    scores = [r["scoring"]["newsValue"] for r in records]
    selected_count = sum(1 for r in records if r["scoring"]["selected"])
    fallback_count = sum(1 for r in records if r["scoring"]["isFallback"])
    avg_score = sum(scores) / len(scores) if scores else 0

    # 統計各 tier 數量
    tier_counts: Dict[str, int] = {}
    for r in records:
        t = r["classification"]["contentTier"]
        tier_counts[t] = tier_counts.get(t, 0) + 1

    # 門檻統計
    thresholds = [r["scoring"]["effectiveThreshold"] for r in records]
    avg_threshold = sum(thresholds) / len(thresholds) if thresholds else 0.0

    # Boost 統計
    gtrend_boosted = sum(1 for r in records if r["scoring"]["gtrendBoost"] > 0)
    economic_boosted = sum(1 for r in records if r["scoring"]["economicBoost"] > 0)
    ip_matched = sum(1 for r in records if r["scoring"]["ipMatches"])

    print("\n" + "=" * 60, file=sys.stderr)
    print("📊 Smoke Test Summary（V2 Scorer 驅動）", file=sys.stderr)
    print("=" * 60, file=sys.stderr)
    print(f"   時間窗      ：{start_dt.isoformat()} → {end_dt.isoformat()}", file=sys.stderr)
    print(f"   sitemap 總量：{sitemap_total}", file=sys.stderr)
    print(f"   窗內稿件    ：{len(records)}", file=sys.stderr)
    print(f"   選稿數      ：{selected_count}", file=sys.stderr)
    print(f"   淘汰數      ：{len(records) - selected_count}", file=sys.stderr)
    print(f"   fallback 數 ：{fallback_count}", file=sys.stderr)
    print(f"   平均 score  ：{avg_score:.1f}", file=sys.stderr)
    if scores:
        print(f"   score 範圍  ：{min(scores)}–{max(scores)}", file=sys.stderr)

    # V2 門檻與 Boost
    print(f"   平均門檻    ：{avg_threshold:.1f}", file=sys.stderr)
    print(f"   gTrend 加分 ：{gtrend_boosted} 篇", file=sys.stderr)
    print(f"   經濟震盪加分：{economic_boosted} 篇", file=sys.stderr)
    print(f"   IP 匹配     ：{ip_matched} 篇", file=sys.stderr)

    # Tier 分佈
    print(f"   Tier 分佈   ：", file=sys.stderr)
    for tier_name in ["P0_short", "P0_main", "P1_followup", "P2_response", "P3_analysis"]:
        cnt = tier_counts.get(tier_name, 0)
        if cnt > 0:
            print(f"     {tier_name:<15} {cnt} 篇", file=sys.stderr)

    if output_path:
        print(f"   輸出檔案    ：{output_path}", file=sys.stderr)
    print("=" * 60, file=sys.stderr)

    # 列出選稿清單
    print("\n✅ 選稿清單（依 score 降序 → tier 優先序）：", file=sys.stderr)
    for rec in records:
        s = rec["scoring"]
        c = rec["content"]
        cl = rec["classification"]
        mark = "✅" if s["selected"] else "❌"
        threshold_info = f"thr={s['effectiveThreshold']:.0f}"
        boost_parts = []
        if s["gtrendBoost"] > 0:
            boost_parts.append(f"gT+{s['gtrendBoost']:.1f}")
        if s["economicBoost"] > 0:
            boost_parts.append(f"ec+{s['economicBoost']:.1f}")
        if s["ipMatches"]:
            boost_parts.append(f"IP={','.join(s['ipMatches'][:2])}")
        boost_str = f"  [{', '.join(boost_parts)}]" if boost_parts else ""
        print(
            f"   {mark} [{c['pid']}] score={s['newsValue']:3d}/{threshold_info}  "
            f"tier={cl['contentTier']:<15} "
            f"{c['title'][:40]}{boost_str}",
            file=sys.stderr,
        )


# ---------------------------------------------------------------------------
# Main（CLI 入口，向下相容）
# ---------------------------------------------------------------------------


def main() -> None:  # noqa: C901
    parser = argparse.ArgumentParser(
        description="CNA smoke test：時間窗選稿（V2 Scorer 驅動）",
    )
    parser.add_argument(
        "--start",
        required=True,
        help="起始時間（ISO 8601，含時區），例如 2026-03-16T15:00:00+08:00",
    )
    parser.add_argument(
        "--end",
        required=True,
        help="結束時間（ISO 8601，含時區），例如 2026-03-16T16:00:00+08:00",
    )
    parser.add_argument(
        "--output",
        default=None,
        help="JSONL 輸出路徑（預設 stdout）",
    )
    parser.add_argument(
        "--delay",
        type=float,
        default=0.5,
        help="每篇文章抓取間隔秒數（預設 0.5）",
    )
    args = parser.parse_args()

    start_dt = datetime.fromisoformat(args.start)
    end_dt = datetime.fromisoformat(args.end)

    # 使用 SmokeCnaWindow 類別
    window = SmokeCnaWindow(
        window_start=start_dt.isoformat(),
        window_end=end_dt.isoformat(),
    )
    records = window.run_from_sitemap(start_dt, end_dt, delay=args.delay)

    if not records:
        sys.exit(0)

    # 輸出 JSONL
    out_fh = sys.stdout
    output_path: Optional[Path] = None
    if args.output:
        output_path = Path(args.output)
        output_path.parent.mkdir(parents=True, exist_ok=True)
        out_fh = open(output_path, "w", encoding="utf-8")  # noqa: SIM115

    for rec in records:
        out_fh.write(json.dumps(rec, ensure_ascii=False) + "\n")

    if out_fh is not sys.stdout:
        out_fh.close()

    # Summary（估算 sitemap 總量 — CLI 模式下已印出，這裡再印摘要）
    _print_summary(records, start_dt, end_dt, 0, output_path)


if __name__ == "__main__":
    main()
