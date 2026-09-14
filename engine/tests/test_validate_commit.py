"""Commit-surface tests for the validator (R2 deliverable #2: validate.py).

``commit`` is the only writer; it re-checks against the store as it goes, so a
state that changed between ``validate`` and ``commit`` cannot be written blind.
"""
from __future__ import annotations

import pytest
from doubles import (
    FakeStore,
    insert_character,
    insert_fact,
    insert_lead,
    insert_mood,
    insert_npc,
)

from engine.config import EngineConfig
from engine.models import Delta, VerdictKind
from engine.validate import Validator


def _setup(**kwargs) -> tuple[FakeStore, Validator]:
    store = FakeStore()
    return store, Validator(store, EngineConfig(), **kwargs)


def _commit(validator: Validator, deltas: list[Delta], turn: int):
    return validator.commit(validator.validate(deltas, turn=turn), turn=turn)


def _character(store: FakeStore) -> dict:
    row = store.find_one("characters")
    assert row is not None
    return row


def _stats(row: dict) -> dict:
    import json

    return json.loads(row["stats"]) if isinstance(row["stats"], str) else row["stats"]


def _inventory(row: dict) -> list:
    import json

    return json.loads(row["inventory"]) if isinstance(row["inventory"], str) else row["inventory"]


# --------------------------------------------------------------------------- #
# Routing
# --------------------------------------------------------------------------- #

def test_commit_routes_verdicts_into_the_report() -> None:
    store, validator = _setup()
    insert_npc(store, name="Marla", row_id=1)
    report = _commit(validator, [
        Delta(kind="mood", target="npc:1", data={"valence_delta": 0.2, "arousal_delta": 0.0}),
        Delta(kind="mood", target="npc:1", data={"valence_delta": 5.0, "arousal_delta": 0.0}),
        Delta(kind="mood", target="npc:404", data={"valence_delta": 0.2, "arousal_delta": 0.0}),
    ], turn=1)
    assert len(report.accepted) == 1
    assert len(report.clamped) == 1
    assert len(report.rejected) == 1
    assert report.rejected[0].note


def test_rejected_deltas_are_never_written() -> None:
    store, validator = _setup()
    insert_character(store, stats={"currency": 10})
    report = _commit(validator, [
        Delta(kind="currency", target="player", data={"amount": -30}),
        Delta(kind="currency", target="player", data={"amount": -4}),
    ], turn=1)
    assert len(report.rejected) == 1
    assert len(report.accepted) == 1
    assert _stats(_character(store))["currency"] == 6


def test_commit_of_no_verdicts_writes_nothing() -> None:
    store, validator = _setup()
    before = store.snapshot()
    report = validator.commit([], turn=1)
    assert report.accepted == report.clamped == report.rejected == []
    assert store.snapshot() == before


def test_commit_requires_a_store() -> None:
    validator = Validator(None, EngineConfig())
    with pytest.raises(RuntimeError):
        validator.commit([], turn=1)


def test_commit_wraps_everything_in_one_transaction() -> None:
    store, validator = _setup()
    insert_character(store, stats={"currency": 10})
    _commit(validator, [Delta(kind="currency", target="player", data={"amount": -1})], turn=1)
    assert store.transaction_count == 1


def test_commit_rolls_back_every_write_when_a_handler_fails() -> None:
    store, validator = _setup()
    insert_character(store, stats={"currency": 10, "hp": 5, "max_hp": 5})
    verdicts = validator.validate([
        Delta(kind="currency", target="player", data={"amount": -4}),
        Delta(kind="hp", target="player", data={"delta": -2}),
    ], turn=1)

    def _boom(*_args, **_kwargs):
        raise RuntimeError("handler exploded")

    validator._apply_hp = _boom  # type: ignore[method-assign]
    with pytest.raises(RuntimeError):
        validator.commit(verdicts, turn=1)
    stats = _stats(_character(store))
    assert stats["currency"] == 10 and stats["hp"] == 5


# --------------------------------------------------------------------------- #
# Mood
# --------------------------------------------------------------------------- #

def test_mood_commit_creates_row_with_personality_baselines() -> None:
    store, validator = _setup()
    insert_npc(store, name="Marla", row_id=1,
               personality={"baseline_valence": 0.4, "baseline_arousal": -0.2})
    _commit(validator, [
        Delta(kind="mood", target="npc:1", data={"valence_delta": 0.3, "arousal_delta": 0.1}),
    ], turn=5)
    row = store.find_one("moods", {"npc_id": "npc:1"})
    assert row is not None
    assert row["valence"] == pytest.approx(0.7)
    assert row["arousal"] == pytest.approx(-0.1)
    assert row["baseline_valence"] == pytest.approx(0.4)
    assert row["baseline_arousal"] == pytest.approx(-0.2)
    assert row["last_updated_turn"] == 5


def test_mood_commit_updates_existing_row_and_keeps_baseline() -> None:
    store, validator = _setup()
    insert_npc(store, name="Marla", row_id=1)
    insert_mood(store, npc_id="npc:1", valence=0.5, arousal=0.0,
                baseline_valence=0.1, baseline_arousal=0.2, last_updated_turn=2)
    _commit(validator, [
        Delta(kind="mood", target="npc:1", data={"valence_delta": -0.2, "arousal_delta": 0.4}),
    ], turn=6)
    rows = store.find("moods", {"npc_id": "npc:1"})
    assert len(rows) == 1  # upsert, no duplicate mood rows
    assert rows[0]["valence"] == pytest.approx(0.3)
    assert rows[0]["arousal"] == pytest.approx(0.4)
    assert rows[0]["baseline_valence"] == pytest.approx(0.1)
    assert rows[0]["last_updated_turn"] == 6


def test_mood_commit_applies_the_clamped_value() -> None:
    store, validator = _setup()
    insert_npc(store, name="Marla", row_id=1)
    insert_mood(store, npc_id="npc:1", valence=0.9, arousal=0.0)
    report = _commit(validator, [
        Delta(kind="mood", target="npc:1", data={"valence_delta": 0.5, "arousal_delta": 0.0}),
    ], turn=2)
    assert len(report.clamped) == 1
    assert store.find_one("moods", {"npc_id": "npc:1"})["valence"] == pytest.approx(1.0)


def test_mood_commit_uses_one_canonical_key_for_name_targets() -> None:
    store, validator = _setup()
    insert_npc(store, name="Marla", row_id=3)
    _commit(validator, [
        Delta(kind="mood", target="Marla", data={"valence_delta": 0.2, "arousal_delta": 0.0}),
    ], turn=1)
    assert store.find_one("moods", {"npc_id": "npc:3"}) is not None
    assert store.find("moods", {"npc_id": "Marla"}) == []


# --------------------------------------------------------------------------- #
# Relationship ledger
# --------------------------------------------------------------------------- #

def test_relationship_commit_appends_ledger_row() -> None:
    store, validator = _setup()
    insert_npc(store, name="Marla", row_id=3)
    _commit(validator, [
        Delta(kind="relationship", target="Marla", reason="paid off her debt", data={
            "category": "Trust", "delta": 41, "decay_class": "Durable",
        }),
    ], turn=9)
    row = store.find_one("relationship_ledger", {"npc_id": "npc:3"})
    assert row is not None
    assert row["turn"] == 9
    assert row["delta"] == pytest.approx(40.0)  # clamped, not the proposed 41
    assert row["category"] == "trust"  # normalized
    assert row["decay_class"] == "durable"
    assert row["player_id"] == "player"
    assert row["reason"] == "paid off her debt"


def test_relationship_commit_defaults_decay_class() -> None:
    store, validator = _setup()
    insert_npc(store, name="Marla", row_id=3)
    _commit(validator, [
        Delta(kind="relationship", target="npc:3", data={
            "category": "trust", "delta": 5, "decay_class": "geologic"}),
    ], turn=1)
    assert store.find_one("relationship_ledger", {"npc_id": "npc:3"})["decay_class"] == "slow"


# --------------------------------------------------------------------------- #
# Facts
# --------------------------------------------------------------------------- #

def test_fact_commit_inserts_the_row() -> None:
    store, validator = _setup()
    _commit(validator, [
        Delta(kind="fact", target="", reason="the innkeeper said so", data={
            "statement": "The north gate is barred after dark",
            "pinned": True, "tags": ["gate", "curfew"], "source": "npc:3",
        }),
    ], turn=7)
    row = store.find_one("world_facts")
    assert row is not None
    assert row["statement"] == "The north gate is barred after dark"
    assert row["pinned"] == 1
    assert row["established_turn"] == 7
    assert row["source"] == "npc:3"
    assert row["tags"] == '["gate", "curfew"]'


def test_fact_commit_rechecks_contradictions_against_live_store() -> None:
    store, validator = _setup()
    deltas = [Delta(kind="fact", target="", data={"statement": "Marla is alive"})]
    verdicts = validator.validate(deltas, turn=3)
    assert verdicts[0].kind == VerdictKind.ACCEPTED.value
    # Something else writes the contradicting fact between validate and commit.
    insert_fact(store, statement="Marla is dead", pinned=True, row_id=1)
    report = validator.commit(verdicts, turn=3)
    assert len(report.rejected) == 1
    assert "#1" in report.rejected[0].note
    assert len(store.find("world_facts")) == 1  # nothing new was written


def test_fact_commit_reinforces_a_near_duplicate_instead_of_duplicating() -> None:
    store, validator = _setup()
    insert_fact(store, statement="The silver bell rings only at dawn", row_id=4)
    report = _commit(validator, [
        Delta(kind="fact", target="", data={"statement": "The silver bell rings only at dawn"}),
    ], turn=2)
    assert len(report.accepted) == 1
    assert "reinforce" in report.accepted[0].note
    assert len(store.find("world_facts")) == 1


def test_fact_commit_catches_batch_self_contradictions() -> None:
    store, validator = _setup()
    verdicts = validator.validate([
        Delta(kind="fact", target="", data={"statement": "The east bridge is destroyed"}),
        Delta(kind="fact", target="", data={"statement": "The east bridge is intact"}),
    ], turn=1)
    report = validator.commit(verdicts, turn=1)
    assert len(report.accepted) == 1
    assert len(report.rejected) == 1
    assert len(store.find("world_facts")) == 1


def test_fact_commit_is_idempotent_for_the_same_statement() -> None:
    store, validator = _setup()
    delta = Delta(kind="fact", target="", data={"statement": "The mill pond freezes in winter"})
    _commit(validator, [delta], turn=1)
    _commit(validator, [delta], turn=2)
    assert len(store.find("world_facts")) == 1


# --------------------------------------------------------------------------- #
# Leads
# --------------------------------------------------------------------------- #

def test_lead_commit_updates_stage_and_appends_history() -> None:
    store, validator = _setup()
    insert_lead(store, stage="unheard", row_id=1)
    _commit(validator, [
        Delta(kind="lead_transition", target="1", data={
            "new_stage": "rumored", "justification": "the innkeeper mentioned it"}),
    ], turn=4)
    row = store.find_one("leads", {"id": 1})
    assert row is not None
    assert row["stage"] == "rumored"
    assert row["stage_history"] == (
        '[{"stage": "rumored", "turn": 4, "trigger": "the innkeeper mentioned it"}]'
    )


def test_lead_commit_accumulates_history_across_turns() -> None:
    store, validator = _setup()
    insert_lead(store, stage="unheard", row_id=1)
    _commit(validator, [Delta(kind="lead_transition", target="1", data={"new_stage": "rumored"})], turn=1)
    _commit(validator, [Delta(kind="lead_transition", target="1", data={"new_stage": "accepted"})], turn=2)
    row = store.find_one("leads", {"id": 1})
    assert row is not None
    assert row["stage"] == "accepted"
    history = __import__("json").loads(row["stage_history"])
    assert [entry["stage"] for entry in history] == ["rumored", "accepted"]
    assert history[0]["turn"] == 1


def test_lead_commit_rejects_when_the_lead_vanished() -> None:
    store, validator = _setup()
    insert_lead(store, stage="unheard", row_id=1)
    verdicts = validator.validate(
        [Delta(kind="lead_transition", target="1", data={"new_stage": "rumored"})], turn=1
    )
    store.delete("leads", 1)
    report = validator.commit(verdicts, turn=1)
    assert len(report.rejected) == 1
    assert "disappeared" in report.rejected[0].note


# --------------------------------------------------------------------------- #
# Character sheet
# --------------------------------------------------------------------------- #

def test_currency_commit_writes_the_new_balance() -> None:
    store, validator = _setup()
    insert_character(store, stats={"hp": 5, "max_hp": 5, "currency": 25})
    _commit(validator, [Delta(kind="currency", target="player", data={"amount": -10})], turn=1)
    assert _stats(_character(store))["currency"] == 15


def test_currency_commit_keeps_the_gold_alias() -> None:
    store, validator = _setup()
    insert_character(store, stats={"hp": 5, "gold": 7})
    _commit(validator, [Delta(kind="currency", target="player", data={"amount": -3})], turn=1)
    stats = _stats(_character(store))
    assert stats["gold"] == 4 and "currency" not in stats


def test_all_character_deltas_in_one_batch_are_applied_together() -> None:
    store, validator = _setup()
    insert_character(store, stats={"hp": 10, "max_hp": 12, "currency": 25, "might": 10},
                     inventory=[{"item_id": "rope", "qty": 1, "flags": {}}],
                     status_effects=["blinded"])
    report = _commit(validator, [
        Delta(kind="currency", target="player", data={"amount": -5}),
        Delta(kind="hp", target="player", data={"delta": -4}),
        Delta(kind="stat", target="player", data={"stat": "might", "delta": 2}),
        Delta(kind="status_add", target="player", data={"status": "poisoned"}),
        Delta(kind="status_remove", target="player", data={"status": "blinded"}),
        Delta(kind="inventory_add", target="torch", data={"qty": 2, "flags": {"lit": True}}),
        Delta(kind="inventory_remove", target="rope", data={"qty": 1}),
    ], turn=3)
    assert len(report.accepted) == 7 and not report.rejected
    stats = _stats(_character(store))
    assert stats == {"hp": 6, "max_hp": 12, "currency": 20, "might": 12}
    assert _inventory(_character(store)) == [{"item_id": "torch", "qty": 2, "flags": {"lit": True}}]
    row = _character(store)
    assert row["status_effects"] == '["poisoned"]'


def test_inventory_remove_to_zero_drops_the_entry() -> None:
    store, validator = _setup()
    insert_character(store, inventory=[{"item_id": "rope", "qty": 2, "flags": {}}])
    _commit(validator, [Delta(kind="inventory_remove", target="rope", data={"qty": 2})], turn=1)
    assert _inventory(_character(store)) == []


def test_inventory_add_merges_quantity_and_flags() -> None:
    store, validator = _setup()
    insert_character(store, inventory=[{"item_id": "torch", "qty": 1, "flags": {}}])
    _commit(validator, [
        Delta(kind="inventory_add", target="torch", data={"qty": 2, "flags": {"lit": True}}),
    ], turn=1)
    assert _inventory(_character(store)) == [{"item_id": "torch", "qty": 3, "flags": {"lit": True}}]


def test_hp_and_stat_commit_apply_clamped_values() -> None:
    store, validator = _setup()
    insert_character(store, stats={"hp": 4, "max_hp": 12, "might": 29})
    report = _commit(validator, [
        Delta(kind="hp", target="player", data={"delta": -10}),
        Delta(kind="stat", target="player", data={"stat": "might", "delta": 5}),
    ], turn=1)
    assert len(report.clamped) == 2
    stats = _stats(_character(store))
    assert stats["hp"] == 0 and stats["might"] == 30


def test_commit_rejects_when_the_character_vanished() -> None:
    store, validator = _setup()
    insert_character(store, stats={"currency": 10}, row_id=1)
    verdicts = validator.validate([Delta(kind="currency", target="player", data={"amount": -1})], turn=1)
    store.delete("characters", 1)
    report = validator.commit(verdicts, turn=1)
    assert len(report.rejected) == 1
    assert "not a character at commit time" in report.rejected[0].note
