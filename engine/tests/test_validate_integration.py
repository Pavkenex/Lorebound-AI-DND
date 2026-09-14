"""Validator against the REAL ``engine.store.Store`` (R2 card integration).

The R1 card lands the SQLite store in parallel; until it exists these tests
skip, and once it does they prove the committed rows round-trip through real
SQLite (including the JSON columns the validator writes).
"""
from __future__ import annotations

import json
from typing import Any

import pytest

from engine.config import EngineConfig
from engine.models import NPC, Character, to_row
from engine.validate import Validator


def _store_ok() -> bool:
    """True once the R1 card's real Store lands (this file activates then)."""
    from engine.store import Store

    try:
        Store(":memory:").close()
    except NotImplementedError:
        return False
    return True


pytestmark = pytest.mark.skipif(not _store_ok(), reason="store lands in parallel")


def _real_store() -> Any:
    """Open a real in-memory store (guarded twice: module skip + this catch)."""
    from engine.store import Store

    try:
        return Store(":memory:")
    except NotImplementedError:
        pytest.skip("engine.store.Store is not implemented yet (R1 card)")


def _validator(store) -> Validator:
    return Validator(store, EngineConfig())


def _seed_npc(store, name: str = "Marla", *, alive: bool = True, personality: dict | None = None) -> int:
    return store.insert("npcs", to_row(NPC(name=name, alive=alive, personality=personality or {})))


def _seed_character(store, **overrides) -> int:
    values = {
        "name": "Player",
        "stats": {"hp": 10, "max_hp": 12, "currency": 25},
        "inventory": [],
        "status_effects": [],
        "location_id": "yard",
    }
    values.update(overrides)
    return store.insert("characters", to_row(Character(**values)))


def _commit(validator: Validator, deltas, turn: int):
    return validator.commit(validator.validate(deltas, turn=turn), turn=turn)


def test_mood_upsert_round_trips_through_sqlite() -> None:
    from engine.models import Delta

    store = _real_store()
    npc_id = _seed_npc(store, personality={"baseline_valence": 0.4})
    validator = _validator(store)
    _commit(validator, [Delta(kind="mood", target=f"npc:{npc_id}",
                              data={"valence_delta": 0.3, "arousal_delta": 0.1})], turn=1)
    _commit(validator, [Delta(kind="mood", target=f"npc:{npc_id}",
                              data={"valence_delta": 0.2, "arousal_delta": 0.0})], turn=2)
    rows = store.find("moods", {"npc_id": f"npc:{npc_id}"})
    assert len(rows) == 1
    assert rows[0]["valence"] == pytest.approx(0.9)
    assert rows[0]["baseline_valence"] == pytest.approx(0.4)
    assert rows[0]["last_updated_turn"] == 2
    store.close()


def test_relationship_ledger_rows_accumulate() -> None:
    from engine.models import Delta

    store = _real_store()
    npc_id = _seed_npc(store)
    validator = _validator(store)
    for turn in (1, 2, 3):
        _commit(validator, [Delta(kind="relationship", target=f"npc:{npc_id}", data={
            "category": "trust", "delta": 10, "reason": f"turn {turn} courtesy"})], turn=turn)
    rows = store.find("relationship_ledger", {"npc_id": f"npc:{npc_id}"}, order_by="turn")
    assert [row["turn"] for row in rows] == [1, 2, 3]
    assert sum(row["delta"] for row in rows) == pytest.approx(30.0)
    assert all(row["decay_class"] == "slow" for row in rows)
    store.close()


def test_fact_rows_round_trip_and_contradictions_stay_rejected() -> None:
    from engine.models import Delta

    store = _real_store()
    validator = _validator(store)
    _commit(validator, [Delta(kind="fact", target="", data={
        "statement": "Marla is dead", "pinned": True, "tags": ["marla"]})], turn=1)
    report = _commit(validator, [Delta(kind="fact", target="", data={
        "statement": "Marla is alive"})], turn=2)
    assert len(report.rejected) == 1
    rows = store.find("world_facts")
    assert len(rows) == 1
    assert json.loads(rows[0]["tags"]) == ["marla"]
    assert rows[0]["pinned"] == 1
    store.close()


def test_character_deltas_round_trip_through_json_columns() -> None:
    from engine.models import Delta

    store = _real_store()
    _seed_character(store, inventory=[{"item_id": "rope", "qty": 1, "flags": {}}])
    validator = _validator(store)
    report = _commit(validator, [
        Delta(kind="currency", target="player", data={"amount": -5}),
        Delta(kind="hp", target="player", data={"delta": -4}),
        Delta(kind="inventory_add", target="torch", data={"qty": 2}),
        Delta(kind="status_add", target="player", data={"status": "poisoned"}),
    ], turn=1)
    assert not report.rejected
    row = store.find_one("characters")
    stats = json.loads(row["stats"])
    assert stats["currency"] == 20 and stats["hp"] == 6
    assert json.loads(row["status_effects"]) == ["poisoned"]
    inventory = json.loads(row["inventory"])
    assert {"item_id": "torch", "qty": 2, "flags": {}} in inventory
    store.close()


def test_lead_stage_history_round_trips() -> None:
    from engine.models import Delta, Lead

    store = _real_store()
    lead_id = store.insert("leads", to_row(Lead(title="The missing shipment", stage="unheard")))
    validator = _validator(store)
    _commit(validator, [Delta(kind="lead_transition", target=str(lead_id), data={
        "new_stage": "rumored", "justification": "the innkeeper mentioned it"})], turn=1)
    _commit(validator, [Delta(kind="lead_transition", target=f"lead:{lead_id}", data={
        "new_stage": "accepted"})], turn=2)
    row = store.find_one("leads", {"id": lead_id})
    assert row["stage"] == "accepted"
    history = json.loads(row["stage_history"])
    assert [entry["stage"] for entry in history] == ["rumored", "accepted"]
    assert history[0]["trigger"] == "the innkeeper mentioned it"
    store.close()


def test_validate_writes_nothing_to_the_real_store() -> None:
    from engine.models import Delta

    store = _real_store()
    npc_id = _seed_npc(store)
    validator = _validator(store)
    before = {table: len(store.find(table)) for table in ("moods", "relationship_ledger", "world_facts")}
    validator.validate([
        Delta(kind="mood", target=f"npc:{npc_id}", data={"valence_delta": 0.2, "arousal_delta": 0.0}),
        Delta(kind="relationship", target=f"npc:{npc_id}", data={"category": "trust", "delta": 5}),
        Delta(kind="fact", target="", data={"statement": "The bell rings at dawn"}),
    ], turn=1)
    after = {table: len(store.find(table)) for table in ("moods", "relationship_ledger", "world_facts")}
    assert after == before
    store.close()
