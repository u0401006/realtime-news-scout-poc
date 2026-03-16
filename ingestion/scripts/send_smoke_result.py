#!/usr/bin/env python3
"""
send_smoke_result.py
====================
讀取 smoke test 輸出的 JSONL 選稿結果，格式化為「入選/未入選 + 理由」清單，
支援 dry-run（預設）與實際 OpenClaw message 發送。

使用方式：
  # Dry-run（預設，僅印出格式化訊息，不發送）
  python3 send_smoke_result.py --input smoke_output.jsonl

  # 實際發送到指定 Telegram 頻道
  python3 send_smoke_result.py --input smoke_output.jsonl --send --channel <chat_id>

  # 限制輸出筆數
  python3 send_smoke_result.py --input smoke_output.jsonl --limit 10

JSONL 輸入格式（每行一個 JSON 物件）：
  {
    "title": "新聞標題",
    "url": "https://...",
    "selected": true,          # true = 入選, false = 未入選
    "reason": "選稿理由說明",
    "score": 0.87,             # 可選，評分
    "source": "CNA",           # 可選，來源
    "publishedAt": "2026-03-16T10:00:00+08:00"  # 可選
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
# 格式化單筆結果
# ─────────────────────────────────────────
def _truncate(text: str, maxlen: int) -> str:
    if len(text) <= maxlen:
        return text
    return text[: maxlen - 1] + "…"


def format_record(rec: dict, index: int) -> str:
    selected: bool = bool(rec.get("selected", False))
    icon = SELECTED_ICON if selected else REJECTED_ICON
    status = "入選" if selected else "未入選"

    title = _truncate(str(rec.get("title", "(無標題)")), MAX_TITLE_LEN)
    reason = _truncate(str(rec.get("reason", "(無理由)")), MAX_REASON_LEN)

    # 可選欄位
    score_str = ""
    if "score" in rec:
        score_str = f"  分數：{rec['score']:.2f}\n"

    source_str = ""
    if "source" in rec:
        source_str = f"  來源：{rec['source']}\n"

    url = rec.get("url", "")
    url_str = f"  {url}\n" if url else ""

    return (
        f"{icon} [{index}] {title}\n"
        f"  狀態：{status}\n"
        f"  理由：{reason}\n"
        f"{score_str}"
        f"{source_str}"
        f"{url_str}"
    )


# ─────────────────────────────────────────
# 格式化完整訊息
# ─────────────────────────────────────────
def build_message(records: list[dict], window_label: str = "") -> str:
    now = datetime.now(TZ_TAIPEI).strftime("%Y-%m-%d %H:%M")
    total = len(records)
    selected_count = sum(1 for r in records if r.get("selected", False))
    rejected_count = total - selected_count

    header = (
        f"📰 *Smoke Test 選稿結果*\n"
        f"時間：{now}"
        + (f"\n視窗：{window_label}" if window_label else "")
        + f"\n入選 {selected_count} 篇 ／ 未入選 {rejected_count} 篇\n"
        + "─" * 30
    )

    # 先列出入選，再列出未入選
    selected_recs = [r for r in records if r.get("selected", False)]
    rejected_recs = [r for r in records if not r.get("selected", False)]

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
