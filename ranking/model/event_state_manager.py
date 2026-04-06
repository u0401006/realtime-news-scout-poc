"""事件鏈狀態管理器：生命週期（孵化 → Peaking → Fading → 退場）。

職責：
  1. 載入與儲存 ``output/event_state.json``
  2. 衰退邏輯（``_decay_unseen``）：未被觀測到的 chain 每輪 momentum 衰減
  3. GC（垃圾回收）：``momentum < 0.1`` 的鏈從 ``self.state["chains"]`` 物理移除
  4. 更新 chain 的 momentum 與 phase（Emerging / Growing / Peaking / Fading）

資料格式（event_state.json）::

    {
        "chains": [
            {
                "chain_id": "chain_001",
                "label": "川普關稅戰",
                "momentum": 0.85,
                "phase": "Peaking",
                "recent_titles": ["..."],
                "promoted_to_wiki": false
            },
            ...
        ]
    }
"""

from __future__ import annotations

import json
import logging
from pathlib import Path
from typing import Any

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# 常數設定
# ---------------------------------------------------------------------------

# GC 門檻：momentum 低於此值的 chain 在 save() 時物理移除
GC_MOMENTUM_THRESHOLD: float = 0.1

# 各 phase 的 momentum 門檻
PHASE_PEAKING: float = 0.8
PHASE_GROWING: float = 0.5
PHASE_EMERGING: float = 0.2

# 衰退係數：每輪未出現的 chain 乘以此係數
DECAY_FACTOR: float = 0.85

# 預設 event_state.json 路徑
DEFAULT_STATE_PATH: Path = Path(__file__).parent.parent.parent / "output" / "event_state.json"


# ---------------------------------------------------------------------------
# phase 判定
# ---------------------------------------------------------------------------


def classify_phase(momentum: float) -> str:
    """依 momentum 判定 chain 所處 phase。

    Args:
        momentum: 0.0 ~ 1.0 浮點數。

    Returns:
        "Peaking" | "Growing" | "Emerging" | "Fading"
    """
    if momentum >= PHASE_PEAKING:
        return "Peaking"
    if momentum >= PHASE_GROWING:
        return "Growing"
    if momentum >= PHASE_EMERGING:
        return "Emerging"
    return "Fading"


# ---------------------------------------------------------------------------
# EventStateManager
# ---------------------------------------------------------------------------


class EventStateManager:
    """管理 event_state.json 的讀寫與生命週期。

    使用方式::

        manager = EventStateManager()
        # 衰退未出現的 chain
        manager.decay_unseen(seen_chain_ids={"chain_001"})
        # 更新特定 chain 的 momentum
        manager.update_chain("chain_002", delta=0.15, recent_titles=["新標題"])
        # 儲存（會自動執行 GC 移除 momentum < 0.1 的鏈）
        manager.save()
    """

    def __init__(self, state_path: Path = DEFAULT_STATE_PATH) -> None:
        """初始化，載入既有狀態（若不存在則建立空狀態）。

        Args:
            state_path: event_state.json 的路徑。
        """
        self.state_path: Path = state_path
        self.state: dict[str, Any] = self._load()

    # ── 載入與儲存 ───────────────────────────────────────────────────────────

    def _load(self) -> dict[str, Any]:
        """讀取 event_state.json，若不存在則回傳空結構。

        Returns:
            包含 ``chains`` 清單的 dict。
        """
        if not self.state_path.exists():
            logger.info("event_state.json 不存在，建立空狀態。path=%s", self.state_path)
            return {"chains": []}
        raw = self.state_path.read_text(encoding="utf-8")
        data: dict[str, Any] = json.loads(raw)
        if "chains" not in data:
            data["chains"] = []
        return data

    def save(self) -> None:
        """執行 GC 後將狀態寫入 event_state.json。

        GC 規則：momentum < GC_MOMENTUM_THRESHOLD (0.1) 的 chain 物理移除。
        """
        self._gc()
        self.state_path.parent.mkdir(parents=True, exist_ok=True)
        self.state_path.write_text(
            json.dumps(self.state, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )
        logger.info("event_state.json 已儲存。chains=%d path=%s", len(self.state["chains"]), self.state_path)

    # ── GC ───────────────────────────────────────────────────────────────────

    def _gc(self) -> list[dict[str, Any]]:
        """從 state["chains"] 物理移除 momentum < GC_MOMENTUM_THRESHOLD 的鏈。

        Returns:
            被移除的 chain 清單（供測試驗證）。
        """
        before = self.state["chains"]
        removed = [c for c in before if c.get("momentum", 0.0) < GC_MOMENTUM_THRESHOLD]
        if removed:
            removed_ids = [c.get("chain_id") for c in removed]
            logger.info("GC：移除 %d 條死鏈。chain_ids=%s", len(removed), removed_ids)
        self.state["chains"] = [c for c in before if c.get("momentum", 0.0) >= GC_MOMENTUM_THRESHOLD]
        return removed

    def gc_removed_chains(self) -> list[dict[str, Any]]:
        """預覽（不寫檔）：回傳會被 GC 移除的 chain 清單。

        Returns:
            momentum < GC_MOMENTUM_THRESHOLD 的 chain 清單。
        """
        return [c for c in self.state["chains"] if c.get("momentum", 0.0) < GC_MOMENTUM_THRESHOLD]

    # ── 衰退 ─────────────────────────────────────────────────────────────────

    def decay_unseen(self, seen_chain_ids: set[str]) -> None:
        """對未出現在本輪新聞的 chain 套用衰減。

        衰減公式：``momentum *= DECAY_FACTOR``。
        衰減後同步更新 phase。

        Args:
            seen_chain_ids: 本輪新聞中有觀測到的 chain_id 集合。
        """
        for chain in self.state["chains"]:
            cid = chain.get("chain_id", "")
            if cid not in seen_chain_ids:
                old_m = chain.get("momentum", 0.0)
                new_m = round(old_m * DECAY_FACTOR, 6)
                chain["momentum"] = new_m
                chain["phase"] = classify_phase(new_m)
                logger.debug("衰退 chain=%s momentum %.4f → %.4f", cid, old_m, new_m)

    # ── 更新 chain ───────────────────────────────────────────────────────────

    def update_chain(
        self,
        chain_id: str,
        delta: float,
        label: str | None = None,
        recent_titles: list[str] | None = None,
    ) -> dict[str, Any]:
        """更新或新增一條 chain 的 momentum。

        若 chain_id 不存在則建立（初始 momentum = delta）。
        momentum 上限 1.0，下限 0.0。

        Args:
            chain_id: chain 的唯一 ID。
            delta: momentum 增減量（正數加分，負數減分）。
            label: 可選的 chain 名稱（僅在新建時設定；若已存在則忽略，除非傳入非 None）。
            recent_titles: 最近關聯的新聞標題清單（覆蓋更新）。

        Returns:
            更新後的 chain dict。
        """
        chain = self._find_chain(chain_id)
        if chain is None:
            chain = {
                "chain_id": chain_id,
                "label": label or chain_id,
                "momentum": 0.0,
                "phase": "Emerging",
                "recent_titles": [],
                "promoted_to_wiki": False,
            }
            self.state["chains"].append(chain)

        old_m = chain.get("momentum", 0.0)
        new_m = min(1.0, max(0.0, round(old_m + delta, 6)))
        chain["momentum"] = new_m
        chain["phase"] = classify_phase(new_m)

        if label is not None:
            chain["label"] = label
        if recent_titles is not None:
            chain["recent_titles"] = recent_titles

        logger.debug("update chain=%s momentum %.4f → %.4f phase=%s", chain_id, old_m, new_m, chain["phase"])
        return chain

    def mark_promoted(self, chain_id: str) -> bool:
        """將 chain 標記為 promoted_to_wiki = True。

        Args:
            chain_id: 目標 chain ID。

        Returns:
            True 若找到並標記；False 若 chain 不存在。
        """
        chain = self._find_chain(chain_id)
        if chain is None:
            return False
        chain["promoted_to_wiki"] = True
        return True

    # ── 查詢 ─────────────────────────────────────────────────────────────────

    def get_peaking_chains(self, exclude_promoted: bool = True) -> list[dict[str, Any]]:
        """回傳所有 Peaking 的 chain。

        Args:
            exclude_promoted: True 表示排除已升格為 Wiki 的 chain。

        Returns:
            符合條件的 chain 清單。
        """
        chains = self.state["chains"]
        result = [c for c in chains if c.get("momentum", 0.0) >= PHASE_PEAKING]
        if exclude_promoted:
            result = [c for c in result if not c.get("promoted_to_wiki", False)]
        return result

    def get_all_chains(self) -> list[dict[str, Any]]:
        """回傳所有 chain 的快照（不含 GC 前的死鏈）。

        Returns:
            self.state["chains"] 的副本。
        """
        return list(self.state["chains"])

    # ── 內部工具 ─────────────────────────────────────────────────────────────

    def _find_chain(self, chain_id: str) -> dict[str, Any] | None:
        """依 chain_id 查找 chain。

        Args:
            chain_id: 目標 chain ID。

        Returns:
            找到的 chain dict（原始引用，可直接修改），或 None。
        """
        for chain in self.state["chains"]:
            if chain.get("chain_id") == chain_id:
                return chain
        return None
