#!/usr/bin/env python3
"""
send_smoke_result.py
====================
讀取 smoke test 輸出的 JSONL 選稿結果，格式化為「入選/未入選 + 理由」清單，
支援 dry-run（預設）與實際 OpenClaw message 發送。

V2 升級（2026-03-18）：
  - 完整支援 V2 嵌套格式（content / scoring / classification / metadata）
  - 顯示 V2Scorer 浮動門檻（effectiveThreshold）
  - 顯示 gTrend / 經濟震盪 / IP 匹配等 Boost 資訊
  - Content Tier 分層顯示與統計
  - 保留 V1 flat 格式向下相容

使用方式：
  # Dry-run（預設，僅印出格式化訊息，不發送）
  python3 send_smoke_result.py --input smoke_output.jsonl

  # 實際發送到指定 Telegram 頻道
  python3 send_smoke_result.py --input smoke_output.jsonl --send --channel <chat_id>

  # 限制輸出筆數
  python3 send_smoke_result.py --input smoke_output.jsonl --limit 10

V2 JSONL 輸入格式（每行一個 JSON 物件）：
  {
    "content": { "title": "...", "url": "...", "pid": "...", ... },
    "scoring": {
      "newsValue": 75,
      "selected": true,
      "reason": "...",
      "effectiveThreshold": 94.0,
      "gtrendBoost": 5.0,
      "economicBoost": 0.0,
      "ipMatches": ["台積電"]
    },
    "classification": { "contentTier": "P0_main", "tierReason": "..." },
    "metadata": { "scorerVersion": "v2", ... }
  }

V1 JSONL 輸入格式（向下相容）：
  {
    "title": "...", "url": "...", "selected": true,
    "reason": "...", "score": 0.87
  }
"""

import argparse
import json
import subprocess
import sys
from datetime import datetime, timezone, timedelta
from pathlib import Path

# ─────────────────────────────────────────
# 常數
# ─────────────────────────────────────────
SELECTED_ICON = "✅"
REJECTED_ICON = "❌"
MAX_TITLE_LEN = 60
MAX_REASON_LEN = 120
TZ_TAIPEI = timezone(timedelta(hours=8))

# Content Tier 顯示名稱映射
TIER_LABELS: dict[str, str] = {
    "P0_short": "🔴 快訊",
    "P0_main": "🟠 主體",
    "P1_followup": "🟡 追蹤",
    "P2_response": "🔵 回應",
    "P3_analysis": "🟣 分析",
}


# ─────────────────────────────────────────
# 讀取 JSONL
# ─────────────────────────────────────────
def load_jsonl(path: Path) -> list[dict]:
    """讀取 JSONL 檔案，每行解析為 dict。跳過空行與解析錯誤的行。"""
    records = []
    with open(path, encoding="utf-8") as f:
        for lineno, raw in enumerate(f, 1):
            raw = raw.strip()
            if not raw:
                continue
            try:
                records.append(json.loads(raw))
            except json.JSONDecodeError as e:
                print(f"[WARN] 第 {lineno} 行 JSON 解析失敗：{e}", file=sys.stderr)
    return records


# ─────────────────────────────────────────
# V2 格式偵測
# ─────────────────────────────────────────
def _is_v2(rec: dict) -> bool:
    """偵測是否為 V2 嵌套格式。"""
    return "content" in rec and "scoring" in rec


# ─────────────────────────────────────────
# 統一欄位存取（V1/V2 相容）
# ─────────────────────────────────────────
def _get_field(rec: dict, field: str, default=None):
    """從 V1 或 V2 格式中統一取欄位值。"""
    if _is_v2(rec):
        mapping = {
            "selected": ("scoring", "selected"),
            "title": ("content", "title"),
            "url": ("content", "url"),
            "pid": ("content", "pid"),
            "reason": ("scoring", "reason"),
            "score": ("scoring", "newsValue"),
            "contentTier": ("classification", "contentTier"),
            "tierReason": ("classification", "tierReason"),
            "effectiveThreshold": ("scoring", "effectiveThreshold"),
            "gtrendBoost": ("scoring", "gtrendBoost"),
            "economicBoost": ("scoring", "economicBoost"),
            "ipMatches": ("scoring", "ipMatches"),
            "isFallback": ("scoring", "isFallback"),
            "scorerVersion": ("metadata", "scorerVersion"),
        }
        if field in mapping:
            section, key = mapping[field]
            return rec.get(section, {}).get(key, default)
        return default
    else:
        # V1 直接取 key
        return rec.get(field, default)


# ─────────────────────────────────────────
# 格式化單筆結果
# ─────────────────────────────────────────
def _truncate(text: str, maxlen: int) -> str:
    if len(text) <= maxlen:
        return text
    return text[: maxlen - 1] + "…"


def format_record(rec: dict, index: int) -> str:
    """格式化單筆結果。支援 V1 (flat) 與 V2 (nested) 格式。"""
    selected = bool(_get_field(rec, "selected", False))
    title = _truncate(str(_get_field(rec, "title", "(無標題)")), MAX_TITLE_LEN)
    reason = _truncate(str(_get_field(rec, "reason", "(無理由)")), MAX_REASON_LEN)
    score = _get_field(rec, "score")
    url = _get_field(rec, "url", "")

    icon = SELECTED_ICON if selected else REJECTED_ICON
    status = "入選" if selected else "未入選"

    # 組合訊息
    parts = [f"{icon} [{index}] {title}"]
    parts.append(f"  狀態：{status}")

    # Content Tier（V2）
    tier = _get_field(rec, "contentTier", "")
    if tier:
        tier_label = TIER_LABELS.get(tier, tier)
        parts.append(f"  分層：{tier_label}")

    # 分數與門檻（V2 顯示門檻對比）
    threshold = _get_field(rec, "effectiveThreshold", 0.0)
    if score is not None:
        if threshold and threshold > 0:
            margin = score - threshold
            margin_str = f"+{margin:.0f}" if margin >= 0 else f"{margin:.0f}"
            parts.append(f"  分數：{score} / 門檻 {threshold:.0f}（{margin_str}）")
        else:
            parts.append(f"  分數：{score}")

    parts.append(f"  理由：{reason}")

    # V2 Boost 詳情
    gtrend = _get_field(rec, "gtrendBoost", 0.0)
    economic = _get_field(rec, "economicBoost", 0.0)
    ip_matches = _get_field(rec, "ipMatches", [])

    boosts = []
    if gtrend and gtrend > 0:
        boosts.append(f"gTrend +{gtrend:.1f}")
    if economic and economic > 0:
        boosts.append(f"經濟震盪 +{economic:.1f}")
    if ip_matches:
        boosts.append(f"IP: {', '.join(ip_matches[:3])}")

    if boosts:
        parts.append(f"  加分：{' | '.join(boosts)}")

    # Tier 理由（V2）
    tier_reason = _get_field(rec, "tierReason", "")
    if tier_reason:
        parts.append(f"  分層依據：{tier_reason}")

    # V1 source
    if not _is_v2(rec) and "source" in rec:
        parts.append(f"  來源：{rec['source']}")

    if url:
        parts.append(f"  {url}")

    return "\n".join(parts) + "\n"


# ─────────────────────────────────────────
# 格式化完整訊息
# ─────────────────────────────────────────
def build_message(records: list[dict], window_label: str = "") -> str:
    now = datetime.now(TZ_TAIPEI).strftime("%Y-%m-%d %H:%M")

    total = len(records)
    selected_recs = [r for r in records if _get_field(r, "selected", False)]
    rejected_recs = [r for r in records if not _get_field(r, "selected", False)]

    selected_count = len(selected_recs)
    rejected_count = len(rejected_recs)

    # 偵測版本
    any_v2 = any(_is_v2(r) for r in records)
    version_tag = "V2 Scorer" if any_v2 else "V1"

    # V2 統計
    stats_parts = []
    if any_v2:
        scores = [_get_field(r, "score", 0) for r in records]
        thresholds = [_get_field(r, "effectiveThreshold", 0.0) for r in records]
        avg_score = sum(scores) / len(scores) if scores else 0
        avg_threshold = sum(thresholds) / len(thresholds) if thresholds else 0

        stats_parts.append(f"平均分數：{avg_score:.1f} / 平均門檻：{avg_threshold:.0f}")

        # Tier 分佈
        tier_counts: dict[str, int] = {}
        for r in records:
            t = _get_field(r, "contentTier", "unknown")
            tier_counts[t] = tier_counts.get(t, 0) + 1

        tier_strs = []
        for tier_name in ["P0_short", "P0_main", "P1_followup", "P2_response", "P3_analysis"]:
            cnt = tier_counts.get(tier_name, 0)
            if cnt > 0:
                label = TIER_LABELS.get(tier_name, tier_name)
                tier_strs.append(f"{label} ×{cnt}")
        if tier_strs:
            stats_parts.append("分層：" + "  ".join(tier_strs))

        # Boost 統計
        gtrend_n = sum(1 for r in records if (_get_field(r, "gtrendBoost", 0.0) or 0) > 0)
        economic_n = sum(1 for r in records if (_get_field(r, "economicBoost", 0.0) or 0) > 0)
        ip_n = sum(1 for r in records if _get_field(r, "ipMatches", []))
        boost_strs = []
        if gtrend_n:
            boost_strs.append(f"gTrend {gtrend_n}篇")
        if economic_n:
            boost_strs.append(f"經濟震盪 {economic_n}篇")
        if ip_n:
            boost_strs.append(f"IP匹配 {ip_n}篇")
        if boost_strs:
            stats_parts.append("加分：" + " | ".join(boost_strs))

    header = (
        f"📰 *Smoke Test 選稿結果 ({version_tag})*\n"
        f"時間：{now}"
        + (f"\n視窗：{window_label}" if window_label else "")
        + f"\n入選 {selected_count} 篇 ／ 未入選 {rejected_count} 篇\n"
    )

    if stats_parts:
        header += "\n".join(stats_parts) + "\n"

    header += "─" * 30

    parts = [header]

    if selected_recs:
        parts.append(f"\n{SELECTED_ICON} **入選稿件**")
        for i, rec in enumerate(selected_recs, 1):
            parts.append(format_record(rec, i))

    if rejected_recs:
        parts.append(f"\n{REJECTED_ICON} **未入選稿件**")
        for i, rec in enumerate(rejected_recs, 1):
            parts.append(format_record(rec, i))

    parts.append("─" * 30)
    return "\n".join(parts)


# ─────────────────────────────────────────
# 發送訊息（透過 openclaw message send）
# ─────────────────────────────────────────
def send_via_openclaw(message: str, channel: str) -> bool:
    """呼叫 openclaw CLI 發送訊息到指定頻道。"""
    try:
        result = subprocess.run(
            ["openclaw", "message", "send", "--channel", channel, "--text", message],
            capture_output=True,
            text=True,
            timeout=30,
        )
        if result.returncode == 0:
            print(f"[OK] 訊息已發送至頻道 {channel}")
            return True
        else:
            print(f"[ERROR] openclaw 發送失敗（exit {result.returncode}）", file=sys.stderr)
            print(result.stderr, file=sys.stderr)
            return False
    except FileNotFoundError:
        print("[ERROR] 找不到 `openclaw` 指令，請確認 PATH 設定", file=sys.stderr)
        return False
    except subprocess.TimeoutExpired:
        print("[ERROR] openclaw 指令逾時", file=sys.stderr)
        return False


# ─────────────────────────────────────────
# 主程式
# ─────────────────────────────────────────
def main():
    parser = argparse.ArgumentParser(
        description="將 smoke test JSONL 結果格式化並（可選）發送訊息",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
範例：
  # Dry-run，印出格式化訊息
  python3 send_smoke_result.py --input ingestion/sample_data/smoke_output.jsonl

  # 實際發送到 Telegram
  python3 send_smoke_result.py --input smoke_output.jsonl --send --channel -1001234567890

  # 限制顯示 5 筆，並加上視窗標籤
  python3 send_smoke_result.py --input smoke_output.jsonl --limit 5 --window "2026-03-16 10:00~11:00"
""",
    )
    parser.add_argument(
        "--input", "-i",
        required=True,
        help="smoke test 輸出 JSONL 檔案路徑",
    )
    parser.add_argument(
        "--send",
        action="store_true",
        default=False,
        help="實際發送訊息（預設為 dry-run，僅印出）",
    )
    parser.add_argument(
        "--channel", "-c",
        default="",
        help="發送目標頻道 ID（--send 時必填）",
    )
    parser.add_argument(
        "--limit", "-n",
        type=int,
        default=0,
        help="最多顯示 N 筆（0 = 全部）",
    )
    parser.add_argument(
        "--window", "-w",
        default="",
        help="視窗標籤，例如 '2026-03-16 10:00~11:00'",
    )
    parser.add_argument(
        "--output", "-o",
        default="",
        help="將格式化訊息另存為純文字檔",
    )

    args = parser.parse_args()

    # 驗證輸入檔案
    input_path = Path(args.input)
    if not input_path.exists():
        print(f"[ERROR] 找不到輸入檔案：{input_path}", file=sys.stderr)
        sys.exit(1)

    # 發送模式需要 channel
    if args.send and not args.channel:
        print("[ERROR] 使用 --send 時必須指定 --channel", file=sys.stderr)
        sys.exit(1)

    # 讀取資料
    records = load_jsonl(input_path)
    if not records:
        print("[WARN] JSONL 檔案無有效資料，結束。", file=sys.stderr)
        sys.exit(0)

    print(f"[INFO] 讀取 {len(records)} 筆資料", file=sys.stderr)

    # 偵測格式版本
    v2_count = sum(1 for r in records if _is_v2(r))
    if v2_count > 0:
        print(f"[INFO] 偵測到 V2 格式 ({v2_count}/{len(records)} 筆)", file=sys.stderr)
    else:
        print("[INFO] V1 格式", file=sys.stderr)

    # 限制筆數
    if args.limit > 0:
        records = records[: args.limit]
        print(f"[INFO] 限制輸出前 {args.limit} 筆", file=sys.stderr)

    # 建立格式化訊息
    message = build_message(records, window_label=args.window)

    # 輸出
    if args.send:
        print("[MODE] 實際發送模式", file=sys.stderr)
        print("=" * 50)
        print(message)
        print("=" * 50)
        ok = send_via_openclaw(message, args.channel)
        sys.exit(0 if ok else 1)
    else:
        print("[MODE] Dry-run 模式（不發送）", file=sys.stderr)
        print("=" * 50)
        print(message)
        print("=" * 50)
        print("[DRY-RUN] 以上為預覽訊息，加上 --send --channel <id> 可實際發送", file=sys.stderr)

    # 可選：存檔
    if args.output:
        out_path = Path(args.output)
        out_path.write_text(message, encoding="utf-8")
        print(f"[INFO] 訊息已儲存至 {out_path}", file=sys.stderr)


if __name__ == "__main__":
    main()
