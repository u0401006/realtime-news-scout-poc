"""測試 EventStateManager：GC 與生命週期管理。

驗收：
  - GC：momentum < 0.1 的 chain 確實從 list 移除（物理刪除）
  - Decay：未出現的 chain 正確衰減並更新 phase
  - Update：update_chain 新增與更新 momentum，phase 同步
  - mark_promoted：標記 promoted_to_wiki = True
  - get_peaking_chains：正確過濾 momentum >= 0.8 且未升格的鏈
  - save/load round-trip：寫入後重新載入結果一致
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from ranking.model.event_state_manager import (
    GC_MOMENTUM_THRESHOLD,
    PHASE_PEAKING,
    EventStateManager,
    classify_phase,
)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_manager(tmp_path: Path, chains: list[dict] | None = None) -> EventStateManager:
    """建立 EventStateManager，初始化指定的 chains。"""
    state_path = tmp_path / "event_state.json"
    data = {"chains": chains or []}
    state_path.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
    return EventStateManager(state_path=state_path)


# ---------------------------------------------------------------------------
# classify_phase
# ---------------------------------------------------------------------------


class TestClassifyPhase:
    def test_peaking(self) -> None:
        assert classify_phase(0.8) == "Peaking"
        assert classify_phase(1.0) == "Peaking"
        assert classify_phase(0.95) == "Peaking"

    def test_growing(self) -> None:
        assert classify_phase(0.5) == "Growing"
        assert classify_phase(0.79) == "Growing"

    def test_emerging(self) -> None:
        assert classify_phase(0.2) == "Emerging"
        assert classify_phase(0.49) == "Emerging"

    def test_fading(self) -> None:
        assert classify_phase(0.0) == "Fading"
        assert classify_phase(0.09) == "Fading"
        assert classify_phase(0.19) == "Fading"


# ---------------------------------------------------------------------------
# GC 邏輯（核心驗收）
# ---------------------------------------------------------------------------


class TestGarbageCollection:
    def test_gc_removes_dead_chain(self, tmp_path: Path) -> None:
        """momentum < 0.1 的 chain 在 save() 後物理消失。"""
        manager = _make_manager(
            tmp_path,
            chains=[
                {"chain_id": "dead-001", "label": "死鏈", "momentum": 0.05, "phase": "Fading", "recent_titles": [], "promoted_to_wiki": False},
                {"chain_id": "alive-002", "label": "活鏈", "momentum": 0.5, "phase": "Growing", "recent_titles": [], "promoted_to_wiki": False},
            ],
        )
        manager.save()

        ids = [c["chain_id"] for c in manager.state["chains"]]
        assert "dead-001" not in ids, "死鏈應被 GC 移除"
        assert "alive-002" in ids, "活鏈應保留"

    def test_gc_removes_momentum_exactly_zero(self, tmp_path: Path) -> None:
        """momentum == 0.0 的 chain 應被移除。"""
        manager = _make_manager(
            tmp_path,
            chains=[
                {"chain_id": "zero", "momentum": 0.0, "phase": "Fading", "recent_titles": [], "promoted_to_wiki": False},
            ],
        )
        manager.save()
        assert manager.state["chains"] == []

    def test_gc_keeps_boundary_value(self, tmp_path: Path) -> None:
        """momentum == 0.1（等於門檻）的 chain 應保留（< 0.1 才移除）。"""
        manager = _make_manager(
            tmp_path,
            chains=[
                {"chain_id": "boundary", "momentum": GC_MOMENTUM_THRESHOLD, "phase": "Fading", "recent_titles": [], "promoted_to_wiki": False},
            ],
        )
        manager.save()
        ids = [c["chain_id"] for c in manager.state["chains"]]
        assert "boundary" in ids

    def test_gc_removes_multiple_dead_chains(self, tmp_path: Path) -> None:
        """多條死鏈一次全數移除。"""
        manager = _make_manager(
            tmp_path,
            chains=[
                {"chain_id": f"dead-{i}", "momentum": 0.01 * i, "phase": "Fading", "recent_titles": [], "promoted_to_wiki": False}
                for i in range(5)  # 0.0, 0.01, 0.02, 0.03, 0.04 → 全數 < 0.1
            ] + [
                {"chain_id": "alive", "momentum": 0.5, "phase": "Growing", "recent_titles": [], "promoted_to_wiki": False},
            ],
        )
        manager.save()
        ids = [c["chain_id"] for c in manager.state["chains"]]
        assert ids == ["alive"]

    def test_gc_preview_does_not_modify(self, tmp_path: Path) -> None:
        """gc_removed_chains() 不修改 state。"""
        manager = _make_manager(
            tmp_path,
            chains=[
                {"chain_id": "dead", "momentum": 0.05, "phase": "Fading", "recent_titles": [], "promoted_to_wiki": False},
            ],
        )
        removed = manager.gc_removed_chains()
        assert len(removed) == 1
        assert removed[0]["chain_id"] == "dead"
        # state 未被修改
        assert len(manager.state["chains"]) == 1


# ---------------------------------------------------------------------------
# decay_unseen
# ---------------------------------------------------------------------------


class TestDecayUnseen:
    def test_decay_not_seen_chain(self, tmp_path: Path) -> None:
        """未見到的 chain 正確衰減。"""
        manager = _make_manager(
            tmp_path,
            chains=[
                {"chain_id": "c1", "momentum": 0.8, "phase": "Peaking", "recent_titles": [], "promoted_to_wiki": False},
            ],
        )
        manager.decay_unseen(seen_chain_ids=set())
        c = manager._find_chain("c1")
        assert c is not None
        assert c["momentum"] < 0.8
        assert c["phase"] == classify_phase(c["momentum"])

    def test_seen_chain_not_decayed(self, tmp_path: Path) -> None:
        """已見到的 chain 不應衰減。"""
        manager = _make_manager(
            tmp_path,
            chains=[
                {"chain_id": "seen", "momentum": 0.75, "phase": "Growing", "recent_titles": [], "promoted_to_wiki": False},
            ],
        )
        manager.decay_unseen(seen_chain_ids={"seen"})
        c = manager._find_chain("seen")
        assert c is not None
        assert c["momentum"] == 0.75

    def test_decay_updates_phase(self, tmp_path: Path) -> None:
        """衰退後 phase 同步更新（Peaking → Growing / Fading）。"""
        # 初始 0.81，衰退係數 0.85 → 0.81*0.85 = 0.6885 → Growing
        manager = _make_manager(
            tmp_path,
            chains=[
                {"chain_id": "x", "momentum": 0.81, "phase": "Peaking", "recent_titles": [], "promoted_to_wiki": False},
            ],
        )
        manager.decay_unseen(seen_chain_ids=set())
        c = manager._find_chain("x")
        assert c is not None
        assert c["phase"] == classify_phase(c["momentum"])
        assert c["phase"] != "Peaking"  # 已從 Peaking 降級


# ---------------------------------------------------------------------------
# update_chain
# ---------------------------------------------------------------------------


class TestUpdateChain:
    def test_update_existing_chain(self, tmp_path: Path) -> None:
        """更新已存在的 chain：momentum 正確增加。"""
        manager = _make_manager(
            tmp_path,
            chains=[
                {"chain_id": "c1", "momentum": 0.5, "phase": "Growing", "recent_titles": [], "promoted_to_wiki": False},
            ],
        )
        chain = manager.update_chain("c1", delta=0.2)
        assert abs(chain["momentum"] - 0.7) < 1e-5
        assert chain["phase"] == "Growing"

    def test_update_creates_new_chain(self, tmp_path: Path) -> None:
        """chain 不存在時，update_chain 應自動建立。"""
        manager = _make_manager(tmp_path)
        chain = manager.update_chain("new-001", delta=0.3, label="新事件")
        assert chain["chain_id"] == "new-001"
        assert chain["label"] == "新事件"
        assert abs(chain["momentum"] - 0.3) < 1e-5

    def test_update_capped_at_1(self, tmp_path: Path) -> None:
        """momentum 上限 1.0，不應超過。"""
        manager = _make_manager(
            tmp_path,
            chains=[{"chain_id": "c", "momentum": 0.9, "phase": "Peaking", "recent_titles": [], "promoted_to_wiki": False}],
        )
        chain = manager.update_chain("c", delta=0.5)
        assert chain["momentum"] == 1.0

    def test_update_floored_at_0(self, tmp_path: Path) -> None:
        """momentum 下限 0.0，不應為負數。"""
        manager = _make_manager(
            tmp_path,
            chains=[{"chain_id": "c", "momentum": 0.05, "phase": "Fading", "recent_titles": [], "promoted_to_wiki": False}],
        )
        chain = manager.update_chain("c", delta=-1.0)
        assert chain["momentum"] == 0.0


# ---------------------------------------------------------------------------
# mark_promoted
# ---------------------------------------------------------------------------


class TestMarkPromoted:
    def test_mark_existing_chain(self, tmp_path: Path) -> None:
        manager = _make_manager(
            tmp_path,
            chains=[{"chain_id": "hot", "momentum": 0.85, "phase": "Peaking", "recent_titles": [], "promoted_to_wiki": False}],
        )
        result = manager.mark_promoted("hot")
        assert result is True
        c = manager._find_chain("hot")
        assert c is not None
        assert c["promoted_to_wiki"] is True

    def test_mark_nonexistent_chain(self, tmp_path: Path) -> None:
        manager = _make_manager(tmp_path)
        result = manager.mark_promoted("ghost-999")
        assert result is False


# ---------------------------------------------------------------------------
# get_peaking_chains（升格邏輯核心驗收）
# ---------------------------------------------------------------------------


class TestGetPeakingChains:
    def test_returns_peaking_not_promoted(self, tmp_path: Path) -> None:
        """正確回傳 momentum >= 0.8 且未升格的 chain。"""
        manager = _make_manager(
            tmp_path,
            chains=[
                {"chain_id": "peak-1", "momentum": 0.85, "phase": "Peaking", "recent_titles": ["T1", "T2"], "promoted_to_wiki": False},
                {"chain_id": "peak-already", "momentum": 0.9, "phase": "Peaking", "recent_titles": [], "promoted_to_wiki": True},
                {"chain_id": "growing", "momentum": 0.6, "phase": "Growing", "recent_titles": [], "promoted_to_wiki": False},
            ],
        )
        peaking = manager.get_peaking_chains()
        ids = [c["chain_id"] for c in peaking]
        assert "peak-1" in ids
        assert "peak-already" not in ids, "已升格的不應返回"
        assert "growing" not in ids, "Growing 的不應返回"

    def test_include_promoted_when_flag_false(self, tmp_path: Path) -> None:
        """exclude_promoted=False 時應包含已升格的 Peaking 鏈。"""
        manager = _make_manager(
            tmp_path,
            chains=[
                {"chain_id": "promoted", "momentum": 0.9, "phase": "Peaking", "recent_titles": [], "promoted_to_wiki": True},
            ],
        )
        peaking = manager.get_peaking_chains(exclude_promoted=False)
        ids = [c["chain_id"] for c in peaking]
        assert "promoted" in ids

    def test_boundary_momentum_08(self, tmp_path: Path) -> None:
        """momentum == 0.8 應視為 Peaking（>= 門檻）。"""
        manager = _make_manager(
            tmp_path,
            chains=[
                {"chain_id": "boundary", "momentum": PHASE_PEAKING, "phase": "Peaking", "recent_titles": [], "promoted_to_wiki": False},
            ],
        )
        peaking = manager.get_peaking_chains()
        assert any(c["chain_id"] == "boundary" for c in peaking)


# ---------------------------------------------------------------------------
# save / load round-trip
# ---------------------------------------------------------------------------


class TestSaveLoad:
    def test_round_trip(self, tmp_path: Path) -> None:
        """save 後重新載入，資料應一致。"""
        manager = _make_manager(
            tmp_path,
            chains=[
                {"chain_id": "x", "momentum": 0.6, "phase": "Growing", "recent_titles": ["A"], "promoted_to_wiki": False},
            ],
        )
        manager.save()

        manager2 = EventStateManager(state_path=tmp_path / "event_state.json")
        assert len(manager2.state["chains"]) == 1
        assert manager2.state["chains"][0]["chain_id"] == "x"
        assert manager2.state["chains"][0]["momentum"] == 0.6

    def test_missing_file_creates_empty(self, tmp_path: Path) -> None:
        """state_path 不存在時，建立空狀態。"""
        manager = EventStateManager(state_path=tmp_path / "nonexistent.json")
        assert manager.state == {"chains": []}
