"""Focused tests for the state validator's rules (R2 deliverable #2: validate.py).

Every rule gets deny cases + boundary cases; ``validate`` must write nothing.
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
from engine.models import Delta, Verdict, VerdictKind
from engine.validate import LEAD_TRANSITIONS, Validator


def _setup(config: EngineConfig | None = None, **kwargs) -> tuple[FakeStore, Validator]:
    store = FakeStore()
    return store, Validator(store, config or EngineConfig(), **kwargs)


def _kinds(verdicts: list[Verdict]) -> list[str]:
    return [verdict.kind for verdict in verdicts]


def _one(verdicts: list[Verdict]) -> Verdict:
    assert len(verdicts) == 1
    return verdicts[0]


def _ledger(store: FakeStore, npc_id: str, category: str, delta: float, turn: int = 1) -> None:
    store.insert("relationship_ledger", {
        "npc_id": npc_id, "player_id": "player", "turn": turn, "delta": delta,
        "reason": "earlier turn", "category": category, "decay_class": "slow",
    })


# --------------------------------------------------------------------------- #
# Shape rules
# --------------------------------------------------------------------------- #

def test_unknown_kind_is_rejected() -> None:
    _store, validator = _setup()
    verdict = _one(validator.validate([Delta(kind="teleport", target="player")], turn=1))
    assert verdict.kind == VerdictKind.REJECTED.value
    assert "unknown delta kind" in verdict.note and "teleport" in verdict.note


def test_missing_required_key_is_rejected() -> None:
    _store, validator = _setup()
    verdict = _one(validator.validate(
        [Delta(kind="mood", target="npc:1", data={"valence_delta": 0.1})], turn=1
    ))
    assert verdict.kind == VerdictKind.REJECTED.value
    assert "arousal_delta" in verdict.note


def test_every_verdict_carries_a_note_that_explains() -> None:
    store, validator = _setup()
    insert_npc(store, name="Marla", row_id=1)
    deltas = [
        Delta(kind="mood", target="npc:1", data={"valence_delta": 0.2, "arousal_delta": 0.1}),
        Delta(kind="mood", target="npc:99", data={"valence_delta": 0.2, "arousal_delta": 0.1}),
        Delta(kind="mood", target="npc:1", data={"valence_delta": 5.0, "arousal_delta": 0.0}),
    ]
    verdicts = validator.validate(deltas, turn=3)
    assert _kinds(verdicts) == [
        VerdictKind.ACCEPTED.value, VerdictKind.REJECTED.value, VerdictKind.CLAMPED.value
    ]
    assert all(verdict.note for verdict in verdicts)
    assert "unknown NPC" in verdicts[1].note
    assert "clamped" in verdicts[2].note


def test_mapping_deltas_are_normalized() -> None:
    store, validator = _setup()
    insert_npc(store, name="Marla", row_id=1)
    verdict = _one(validator.validate(
        [{"kind": "mood", "target": "npc:1", "data": {"valence_delta": 0.1, "arousal_delta": 0.0}}],
        turn=1,
    ))
    assert verdict.kind == VerdictKind.ACCEPTED.value
    assert isinstance(verdict.delta, Delta)
    assert verdict.delta.kind == "mood"


def test_non_delta_payload_is_rejected_not_raised() -> None:
    _store, validator = _setup()
    verdict = _one(validator.validate([object()], turn=1))  # type: ignore[list-item]
    assert verdict.kind == VerdictKind.REJECTED.value


def test_validate_writes_nothing() -> None:
    store, validator = _setup()
    insert_npc(store, name="Marla", row_id=1)
    insert_character(store)
    insert_lead(store, stage="unheard", row_id=1)
    before = store.snapshot()
    writes_before = store.write_count
    validator.validate([
        Delta(kind="mood", target="npc:1", data={"valence_delta": 0.3, "arousal_delta": -0.2}),
        Delta(kind="relationship", target="npc:1", data={"category": "trust", "delta": 10}),
        Delta(kind="currency", target="player", data={"amount": -5}),
        Delta(kind="fact", target="", data={"statement": "The bell towers ring at dawn"}),
        Delta(kind="lead_transition", target="1", data={"new_stage": "rumored"}),
    ], turn=2)
    assert store.snapshot() == before
    assert store.write_count == writes_before


# --------------------------------------------------------------------------- #
# Relationship
# --------------------------------------------------------------------------- #

def test_relationship_accepts_within_bound() -> None:
    store, validator = _setup()
    insert_npc(store, name="Marla", row_id=1)
    for value in (40, -40, 0.5):
        verdict = _one(validator.validate(
            [Delta(kind="relationship", target="npc:1", data={"category": "trust", "delta": value})],
            turn=1,
        ))
        assert verdict.kind == VerdictKind.ACCEPTED.value, verdict.note


@pytest.mark.parametrize(("proposed", "applied"), [(41, 40.0), (-41, -40.0), (1000, 40.0)])
def test_relationship_per_event_bound_clamps(proposed: float, applied: float) -> None:
    store, validator = _setup()
    insert_npc(store, name="Marla", row_id=1)
    verdict = _one(validator.validate(
        [Delta(kind="relationship", target="npc:1", data={"category": "trust", "delta": proposed})],
        turn=1,
    ))
    assert verdict.kind == VerdictKind.CLAMPED.value
    assert verdict.clamped_to == applied
    assert "per-event bound" in verdict.note


def test_relationship_total_clamp_uses_store_state() -> None:
    store, validator = _setup()
    insert_npc(store, name="Marla", row_id=1)
    _ledger(store, "npc:1", "trust", 90.0)
    verdict = _one(validator.validate(
        [Delta(kind="relationship", target="npc:1", data={"category": "trust", "delta": 20})],
        turn=2,
    ))
    assert verdict.kind == VerdictKind.CLAMPED.value
    assert verdict.clamped_to == 10.0
    assert "±100" in verdict.note


def test_relationship_already_over_the_range_cannot_grow_further() -> None:
    store, validator = _setup()
    insert_npc(store, name="Marla", row_id=1)
    _ledger(store, "npc:1", "fear", 150.0)
    verdict = _one(validator.validate(
        [Delta(kind="relationship", target="npc:1", data={"category": "fear", "delta": 5})],
        turn=2,
    ))
    assert verdict.kind == VerdictKind.CLAMPED.value
    assert verdict.clamped_to == 0.0


def test_relationship_already_under_the_range_cannot_sink_further() -> None:
    store, validator = _setup()
    insert_npc(store, name="Marla", row_id=1)
    _ledger(store, "npc:1", "debt", -150.0)
    verdict = _one(validator.validate(
        [Delta(kind="relationship", target="npc:1", data={"category": "debt", "delta": -5})],
        turn=2,
    ))
    assert verdict.kind == VerdictKind.CLAMPED.value
    assert verdict.clamped_to == 0.0


def test_relationship_disposition_base_counts_as_current_value() -> None:
    store, validator = _setup()
    insert_npc(store, name="Marla", row_id=1, disposition_base=95.0)
    verdict = _one(validator.validate(
        [Delta(kind="relationship", target="npc:1", data={"category": "affection", "delta": 20})],
        turn=1,
    ))
    assert verdict.kind == VerdictKind.CLAMPED.value
    assert verdict.clamped_to == 5.0


@pytest.mark.parametrize("delta", [
    Delta(kind="relationship", target="npc:1", data={"category": "loyalty", "delta": 5}),
    Delta(kind="relationship", target="npc:999", data={"category": "trust", "delta": 5}),
    Delta(kind="relationship", target="", data={"category": "trust", "delta": 5}),
    Delta(kind="relationship", target="npc:1", data={"category": "trust", "delta": "lots"}),
])
def test_relationship_denials(delta: Delta) -> None:
    store, validator = _setup()
    insert_npc(store, name="Marla", row_id=1)
    verdict = _one(validator.validate([delta], turn=1))
    assert verdict.kind == VerdictKind.REJECTED.value
    assert verdict.note


def test_relationship_dead_npc_denied() -> None:
    store, validator = _setup()
    insert_npc(store, name="Marla", row_id=1, alive=False)
    verdict = _one(validator.validate(
        [Delta(kind="relationship", target="npc:1", data={"category": "trust", "delta": 5})], turn=1
    ))
    assert verdict.kind == VerdictKind.REJECTED.value
    assert "dead" in verdict.note


# --------------------------------------------------------------------------- #
# Mood
# --------------------------------------------------------------------------- #

def test_mood_accepts_within_range_and_clamps_per_event() -> None:
    store, validator = _setup()
    insert_npc(store, name="Marla", row_id=1)
    accepted = _one(validator.validate(
        [Delta(kind="mood", target="npc:1", data={"valence_delta": -0.4, "arousal_delta": 0.6})], turn=1
    ))
    assert accepted.kind == VerdictKind.ACCEPTED.value
    clamped = _one(validator.validate(
        [Delta(kind="mood", target="npc:1", data={"valence_delta": 1.5, "arousal_delta": -1.5})], turn=1
    ))
    assert clamped.kind == VerdictKind.CLAMPED.value
    assert clamped.clamped_to == {"valence_delta": 1.0, "arousal_delta": -1.0}
    assert "[-1, 1]" in clamped.note


def test_mood_cumulative_clamp_against_stored_state() -> None:
    store, validator = _setup()
    insert_npc(store, name="Marla", row_id=1)
    insert_mood(store, npc_id="npc:1", valence=0.5, arousal=-0.9)
    verdict = _one(validator.validate(
        [Delta(kind="mood", target="npc:1", data={"valence_delta": 0.6, "arousal_delta": -0.6})], turn=2
    ))
    assert verdict.kind == VerdictKind.CLAMPED.value
    assert verdict.clamped_to == {"valence_delta": 0.5, "arousal_delta": -0.1}


def test_mood_multiple_deltas_in_one_batch_accumulate() -> None:
    store, validator = _setup()
    insert_npc(store, name="Marla", row_id=1)
    verdicts = validator.validate([
        Delta(kind="mood", target="npc:1", data={"valence_delta": 0.6, "arousal_delta": 0.0}),
        Delta(kind="mood", target="npc:1", data={"valence_delta": 0.6, "arousal_delta": 0.0}),
    ], turn=1)
    assert _kinds(verdicts) == [VerdictKind.ACCEPTED.value, VerdictKind.CLAMPED.value]
    assert verdicts[1].clamped_to == {"valence_delta": 0.4, "arousal_delta": 0.0}


def test_mood_boundary_values_are_not_clamped() -> None:
    store, validator = _setup()
    insert_npc(store, name="Marla", row_id=1)
    verdict = _one(validator.validate(
        [Delta(kind="mood", target="npc:1", data={"valence_delta": 1.0, "arousal_delta": -1.0})], turn=1
    ))
    assert verdict.kind == VerdictKind.ACCEPTED.value


@pytest.mark.parametrize("delta", [
    Delta(kind="mood", target="npc:1", data={"valence_delta": "angry", "arousal_delta": 0.1}),
    Delta(kind="mood", target="npc:404", data={"valence_delta": 0.1, "arousal_delta": 0.1}),
    Delta(kind="mood", target="", data={"valence_delta": 0.1, "arousal_delta": 0.1}),
])
def test_mood_denials(delta: Delta) -> None:
    store, validator = _setup()
    insert_npc(store, name="Marla", row_id=1)
    verdict = _one(validator.validate([delta], turn=1))
    assert verdict.kind == VerdictKind.REJECTED.value


def test_mood_on_dead_npc_denied() -> None:
    store, validator = _setup()
    insert_npc(store, name="Marla", row_id=1, alive=False)
    verdict = _one(validator.validate(
        [Delta(kind="mood", target="npc:1", data={"valence_delta": 0.2, "arousal_delta": 0.2})], turn=1
    ))
    assert verdict.kind == VerdictKind.REJECTED.value
    assert "dead" in verdict.note


# --------------------------------------------------------------------------- #
# Currency
# --------------------------------------------------------------------------- #

def test_currency_spend_within_balance_is_accepted() -> None:
    store, validator = _setup()
    insert_character(store, stats={"hp": 5, "max_hp": 5, "currency": 25})
    verdict = _one(validator.validate([Delta(kind="currency", target="player", data={"amount": -10})], turn=1))
    assert verdict.kind == VerdictKind.ACCEPTED.value
    assert "25 -> 15" in verdict.note


def test_currency_spend_equal_to_balance_is_accepted() -> None:
    store, validator = _setup()
    insert_character(store, stats={"currency": 10})
    verdict = _one(validator.validate([Delta(kind="currency", target="player", data={"amount": -10})], turn=1))
    assert verdict.kind == VerdictKind.ACCEPTED.value


def test_currency_overdraft_is_rejected_and_balance_unchanged() -> None:
    store, validator = _setup()
    insert_character(store, stats={"currency": 10})
    verdict = _one(validator.validate([Delta(kind="currency", target="player", data={"amount": -11})], turn=1))
    assert verdict.kind == VerdictKind.REJECTED.value
    assert "conservation" in verdict.note
    assert store.find_one("characters")["stats"] == '{"currency": 10}'


def test_currency_sequential_spends_in_one_batch() -> None:
    store, validator = _setup()
    insert_character(store, stats={"currency": 25})
    verdicts = validator.validate([
        Delta(kind="currency", target="player", data={"amount": -10}),
        Delta(kind="currency", target="player", data={"amount": -20}),
    ], turn=1)
    assert _kinds(verdicts) == [VerdictKind.ACCEPTED.value, VerdictKind.REJECTED.value]
    assert "balance of 15" in verdicts[1].note


def test_currency_income_is_accepted() -> None:
    store, validator = _setup()
    insert_character(store, stats={"currency": 5})
    verdict = _one(validator.validate([Delta(kind="currency", target="", data={"amount": 15})], turn=1))
    assert verdict.kind == VerdictKind.ACCEPTED.value
    assert "5 -> 20" in verdict.note


def test_currency_gold_alias_is_read() -> None:
    store, validator = _setup()
    insert_character(store, stats={"hp": 5, "gold": 7})
    verdict = _one(validator.validate([Delta(kind="currency", target="player", data={"amount": -7})], turn=1))
    assert verdict.kind == VerdictKind.ACCEPTED.value
    assert "gold" in verdict.note


@pytest.mark.parametrize("amount", ["-10", 1.5, None, True])
def test_currency_non_integer_amount_is_rejected(amount) -> None:
    store, validator = _setup()
    insert_character(store, stats={"currency": 25})
    verdict = _one(validator.validate([Delta(kind="currency", target="player", data={"amount": amount})], turn=1))
    assert verdict.kind == VerdictKind.REJECTED.value


def test_currency_without_character_row_is_rejected() -> None:
    _store, validator = _setup()
    verdict = _one(validator.validate([Delta(kind="currency", target="player", data={"amount": 1})], turn=1))
    assert verdict.kind == VerdictKind.REJECTED.value
    assert "no character row" in verdict.note


# --------------------------------------------------------------------------- #
# Inventory
# --------------------------------------------------------------------------- #

def test_inventory_add_merges_and_creates() -> None:
    store, validator = _setup()
    insert_character(store, inventory=[{"item_id": "rope", "qty": 1, "flags": {}}])
    verdicts = validator.validate([
        Delta(kind="inventory_add", target="rope", data={"qty": 2}),
        Delta(kind="inventory_add", target="torch", data={"qty": 3, "flags": {"lit": True}}),
    ], turn=1)
    assert _kinds(verdicts) == [VerdictKind.ACCEPTED.value, VerdictKind.ACCEPTED.value]
    assert "1 -> 3" in verdicts[0].note
    assert "gained 3 x torch" in verdicts[1].note


@pytest.mark.parametrize("qty", [0, -1, 1.5, "two", None])
def test_inventory_add_bad_qty_is_rejected(qty) -> None:
    store, validator = _setup()
    insert_character(store)
    verdict = _one(validator.validate([Delta(kind="inventory_add", target="rope", data={"qty": qty})], turn=1))
    assert verdict.kind == VerdictKind.REJECTED.value


def test_inventory_add_without_item_target_is_rejected() -> None:
    store, validator = _setup()
    insert_character(store)
    verdict = _one(validator.validate([Delta(kind="inventory_add", target="", data={"qty": 1})], turn=1))
    assert verdict.kind == VerdictKind.REJECTED.value


def test_inventory_remove_exact_qty_is_accepted() -> None:
    store, validator = _setup()
    insert_character(store, inventory=[{"item_id": "rope", "qty": 3, "flags": {}}])
    verdict = _one(validator.validate([Delta(kind="inventory_remove", target="rope", data={"qty": 3})], turn=1))
    assert verdict.kind == VerdictKind.ACCEPTED.value
    assert "3 -> 0" in verdict.note


def test_inventory_remove_more_than_held_is_rejected() -> None:
    store, validator = _setup()
    insert_character(store, inventory=[{"item_id": "rope", "qty": 3, "flags": {}}])
    verdict = _one(validator.validate([Delta(kind="inventory_remove", target="rope", data={"qty": 4})], turn=1))
    assert verdict.kind == VerdictKind.REJECTED.value
    assert "holds 3" in verdict.note and "never partial" in verdict.note


def test_inventory_remove_missing_item_is_rejected() -> None:
    store, validator = _setup()
    insert_character(store)
    verdict = _one(validator.validate([Delta(kind="inventory_remove", target="rope", data={"qty": 1})], turn=1))
    assert verdict.kind == VerdictKind.REJECTED.value


def test_inventory_removes_in_one_batch_cannot_overdraw() -> None:
    store, validator = _setup()
    insert_character(store, inventory=[{"item_id": "rope", "qty": 3, "flags": {}}])
    verdicts = validator.validate([
        Delta(kind="inventory_remove", target="rope", data={"qty": 2}),
        Delta(kind="inventory_remove", target="rope", data={"qty": 2}),
    ], turn=1)
    assert _kinds(verdicts) == [VerdictKind.ACCEPTED.value, VerdictKind.REJECTED.value]
    assert "holds 1" in verdicts[1].note


# --------------------------------------------------------------------------- #
# HP
# --------------------------------------------------------------------------- #

def test_hp_damage_floor_clamps_at_zero() -> None:
    store, validator = _setup()
    insert_character(store, stats={"hp": 3, "max_hp": 12})
    verdict = _one(validator.validate(
        [Delta(kind="hp", target="player", data={"delta": -10, "cause": "ambush"})], turn=1
    ))
    assert verdict.kind == VerdictKind.CLAMPED.value
    assert verdict.clamped_to == -3.0
    assert "0..12" in verdict.note


def test_hp_exact_floor_is_accepted_not_clamped() -> None:
    store, validator = _setup()
    insert_character(store, stats={"hp": 3, "max_hp": 12})
    verdict = _one(validator.validate([Delta(kind="hp", target="player", data={"delta": -3})], turn=1))
    assert verdict.kind == VerdictKind.ACCEPTED.value


def test_hp_heal_clamps_to_max_hp() -> None:
    store, validator = _setup()
    insert_character(store, stats={"hp": 10, "max_hp": 12})
    verdict = _one(validator.validate([Delta(kind="hp", target="player", data={"delta": 5})], turn=1))
    assert verdict.kind == VerdictKind.CLAMPED.value
    assert verdict.clamped_to == 2.0


def test_hp_without_max_hp_has_no_ceiling() -> None:
    store, validator = _setup()
    insert_character(store, stats={"hp": 5})
    verdict = _one(validator.validate([Delta(kind="hp", target="player", data={"delta": 3})], turn=1))
    assert verdict.kind == VerdictKind.ACCEPTED.value


def test_hp_without_a_hp_stat_is_rejected() -> None:
    store, validator = _setup()
    insert_character(store, stats={"max_hp": 12})
    verdict = _one(validator.validate([Delta(kind="hp", target="player", data={"delta": -2})], turn=1))
    assert verdict.kind == VerdictKind.REJECTED.value
    assert "no 'hp' stat" in verdict.note


# --------------------------------------------------------------------------- #
# Stats
# --------------------------------------------------------------------------- #

def test_stat_clamps_to_bounds() -> None:
    store, validator = _setup()
    insert_character(store, stats={"hp": 1, "might": 28, "wits": -9})
    verdicts = validator.validate([
        Delta(kind="stat", target="player", data={"stat": "might", "delta": 5}),
        Delta(kind="stat", target="player", data={"stat": "wits", "delta": -5}),
    ], turn=1)
    assert _kinds(verdicts) == [VerdictKind.CLAMPED.value, VerdictKind.CLAMPED.value]
    assert verdicts[0].clamped_to == 2.0
    assert verdicts[1].clamped_to == -1.0
    assert "[-10, 30]" in verdicts[0].note


def test_stat_bounds_are_overridable() -> None:
    store, validator = _setup(stat_bounds=(0, 10))
    insert_character(store, stats={"hp": 1, "might": 8})
    verdict = _one(validator.validate(
        [Delta(kind="stat", target="player", data={"stat": "might", "delta": 5})], turn=1
    ))
    assert verdict.kind == VerdictKind.CLAMPED.value
    assert verdict.clamped_to == 2.0


def test_stat_new_key_starts_from_zero() -> None:
    store, validator = _setup()
    insert_character(store, stats={"hp": 1})
    verdict = _one(validator.validate(
        [Delta(kind="stat", target="player", data={"stat": "luck", "delta": 2})], turn=1
    ))
    assert verdict.kind == VerdictKind.ACCEPTED.value
    assert "0 -> 2" in verdict.note


@pytest.mark.parametrize("delta", [
    Delta(kind="stat", target="player", data={"stat": "", "delta": 1}),
    Delta(kind="stat", target="player", data={"stat": "luck", "delta": "much"}),
])
def test_stat_denials(delta: Delta) -> None:
    store, validator = _setup()
    insert_character(store, stats={"hp": 1})
    verdict = _one(validator.validate([delta], turn=1))
    assert verdict.kind == VerdictKind.REJECTED.value


# --------------------------------------------------------------------------- #
# Status effects
# --------------------------------------------------------------------------- #

def test_status_add_and_remove() -> None:
    store, validator = _setup()
    insert_character(store, status_effects=["blinded"])
    verdicts = validator.validate([
        Delta(kind="status_add", target="player", data={"status": "poisoned"}),
        Delta(kind="status_add", target="player", data={"status": "poisoned"}),
        Delta(kind="status_remove", target="player", data={"status": "blinded"}),
        Delta(kind="status_remove", target="player", data={"status": "blinded"}),
    ], turn=1)
    assert _kinds(verdicts) == [
        VerdictKind.ACCEPTED.value, VerdictKind.ACCEPTED.value,
        VerdictKind.ACCEPTED.value, VerdictKind.REJECTED.value,
    ]
    assert "already has" in verdicts[1].note
    assert "nothing to remove" in verdicts[3].note


def test_status_blank_name_is_rejected() -> None:
    store, validator = _setup()
    insert_character(store)
    for kind in ("status_add", "status_remove"):
        verdict = _one(validator.validate([Delta(kind=kind, target="player", data={"status": " "})], turn=1))
        assert verdict.kind == VerdictKind.REJECTED.value


# --------------------------------------------------------------------------- #
# Facts
# --------------------------------------------------------------------------- #

def test_fact_empty_statement_is_rejected() -> None:
    _store, validator = _setup()
    verdict = _one(validator.validate([Delta(kind="fact", target="", data={"statement": " "})], turn=1))
    assert verdict.kind == VerdictKind.REJECTED.value


def test_fact_rate_limit_allows_exactly_three() -> None:
    _store, validator = _setup()
    deltas = [
        Delta(kind="fact", target="", data={"statement": statement})
        for statement in (
            "The raven sigil marks the north door",
            "The mill pond freezes in winter",
            "Captain Vale keeps a ledger of old debts",
            "Old tunnels run beneath the chapel",
        )
    ]
    verdicts = validator.validate(deltas, turn=1)
    assert _kinds(verdicts) == [
        VerdictKind.ACCEPTED.value, VerdictKind.ACCEPTED.value,
        VerdictKind.ACCEPTED.value, VerdictKind.REJECTED.value,
    ]
    assert "rate limit" in verdicts[3].note


def test_fact_rate_limit_counts_facts_already_committed_this_turn() -> None:
    store, validator = _setup()
    for index in range(3):
        insert_fact(store, statement=f"Old fact {index} about the harbor", established_turn=7)
    verdict = _one(validator.validate(
        [Delta(kind="fact", target="", data={"statement": "A brand new fact about the harbor"})], turn=7
    ))
    assert verdict.kind == VerdictKind.REJECTED.value
    assert "already committed this turn" in verdict.note


def test_fact_rate_limit_can_be_configured() -> None:
    store, validator = _setup(EngineConfig())
    validator.config.memory.fact_rate_limit_per_turn = 1
    verdicts = validator.validate([
        Delta(kind="fact", target="", data={"statement": "First fact about the eastern road"}),
        Delta(kind="fact", target="", data={"statement": "Second fact about the western road"}),
    ], turn=1)
    assert _kinds(verdicts) == [VerdictKind.ACCEPTED.value, VerdictKind.REJECTED.value]


def test_fact_from_dead_npc_source_is_rejected() -> None:
    store, validator = _setup()
    insert_npc(store, name="Marla", row_id=3, alive=False)
    by_id = _one(validator.validate(
        [Delta(kind="fact", target="", data={"statement": "The vault is empty", "source": "npc:3"})], turn=1
    ))
    by_name = _one(validator.validate(
        [Delta(kind="fact", target="", data={"statement": "The vault is sealed", "source": "Marla"})], turn=1
    ))
    assert by_id.kind == VerdictKind.REJECTED.value
    assert by_name.kind == VerdictKind.REJECTED.value
    assert "dead NPC" in by_id.note


def test_fact_revival_by_fiat_is_rejected() -> None:
    store, validator = _setup()
    insert_npc(store, name="Marla", row_id=3, alive=False)
    verdict = _one(validator.validate(
        [Delta(kind="fact", target="", data={"statement": "Marla is alive and well in Ravenford"})], turn=1
    ))
    assert verdict.kind == VerdictKind.REJECTED.value
    assert "revival" in verdict.note


def test_fact_revival_by_fiat_needs_a_dead_npc() -> None:
    store, validator = _setup()
    insert_npc(store, name="Marla", row_id=3, alive=True)
    verdict = _one(validator.validate(
        [Delta(kind="fact", target="", data={"statement": "Marla is alive and well in Ravenford"})], turn=1
    ))
    assert verdict.kind == VerdictKind.ACCEPTED.value


def test_fact_contradicting_a_pinned_fact_is_rejected_with_ids() -> None:
    store, validator = _setup()
    insert_fact(store, statement="Marla is dead", pinned=True, row_id=5)
    verdict = _one(validator.validate(
        [Delta(kind="fact", target="", data={"statement": "Marla is alive"})], turn=1
    ))
    assert verdict.kind == VerdictKind.REJECTED.value
    assert "#5" in verdict.note
    assert "antonym" in verdict.note


def test_fact_contradicting_an_unpinned_fact_is_rejected() -> None:
    store, validator = _setup()
    insert_fact(store, statement="The north gate is locked after dark", pinned=False, row_id=2)
    verdict = _one(validator.validate(
        [Delta(kind="fact", target="", data={"statement": "The north gate is unlocked after dark"})], turn=1
    ))
    assert verdict.kind == VerdictKind.REJECTED.value
    assert "#2" in verdict.note


def test_fact_numeric_contradiction_is_rejected() -> None:
    store, validator = _setup()
    insert_fact(store, statement="Marla has 3 guards at the gate", pinned=False, row_id=1)
    verdict = _one(validator.validate(
        [Delta(kind="fact", target="", data={"statement": "Marla has 5 guards at the gate"})], turn=1
    ))
    assert verdict.kind == VerdictKind.REJECTED.value
    assert "numeric conflict" in verdict.note


def test_fact_batch_self_contradiction_is_rejected() -> None:
    _store, validator = _setup()
    verdicts = validator.validate([
        Delta(kind="fact", target="", data={"statement": "The east bridge is destroyed"}),
        Delta(kind="fact", target="", data={"statement": "The east bridge is intact"}),
    ], turn=1)
    assert _kinds(verdicts) == [VerdictKind.ACCEPTED.value, VerdictKind.REJECTED.value]
    assert "same turn" in verdicts[1].note


def test_fact_near_duplicate_reinforces_instead_of_duplicating() -> None:
    store, validator = _setup()
    insert_fact(store, statement="The silver bell rings only at dawn", row_id=4)
    verdict = _one(validator.validate(
        [Delta(kind="fact", target="", data={"statement": "The silver bell rings only at dawn"})], turn=1
    ))
    assert verdict.kind == VerdictKind.ACCEPTED.value
    assert "reinforce" in verdict.note and "#4" in verdict.note


def test_fact_duplicates_do_not_consume_the_rate_limit() -> None:
    store, validator = _setup()
    insert_fact(store, statement="The silver bell rings only at dawn", row_id=4)
    verdicts = validator.validate([
        Delta(kind="fact", target="", data={"statement": "The silver bell rings only at dawn"}),
        Delta(kind="fact", target="", data={"statement": "A completely new fact about the mill pond"}),
    ], turn=1)
    assert _kinds(verdicts) == [VerdictKind.ACCEPTED.value, VerdictKind.ACCEPTED.value]


def test_fact_unrelated_statement_is_accepted() -> None:
    store, validator = _setup()
    insert_fact(store, statement="Marla is dead", pinned=True, row_id=5)
    verdict = _one(validator.validate(
        [Delta(kind="fact", target="", data={"statement": "The mill pond freezes in winter"})], turn=1
    ))
    assert verdict.kind == VerdictKind.ACCEPTED.value


def test_fact_pinned_flag_is_reported() -> None:
    _store, validator = _setup()
    verdict = _one(validator.validate(
        [Delta(kind="fact", target="", data={"statement": "The bell rings at dawn", "pinned": True})], turn=1
    ))
    assert "pinned=True" in verdict.note


# --------------------------------------------------------------------------- #
# Lead transitions
# --------------------------------------------------------------------------- #

def test_legal_lead_transition_is_accepted() -> None:
    store, validator = _setup()
    insert_lead(store, stage="unheard", row_id=1)
    verdict = _one(validator.validate(
        [Delta(kind="lead_transition", target="1", data={
            "new_stage": "rumored", "justification": "the innkeeper mentioned it"})], turn=1
    ))
    assert verdict.kind == VerdictKind.ACCEPTED.value
    assert "unheard' -> 'rumored" in verdict.note
    assert "innkeeper" in verdict.note


def test_lead_skip_is_rejected() -> None:
    store, validator = _setup()
    insert_lead(store, stage="unheard", row_id=1)
    verdict = _one(validator.validate(
        [Delta(kind="lead_transition", target="1", data={"new_stage": "resolved"})], turn=1
    ))
    assert verdict.kind == VerdictKind.REJECTED.value
    assert "illegal lead transition" in verdict.note
    assert "rumored" in verdict.note  # tells the model what IS allowed


def test_lead_terminal_stage_is_closed() -> None:
    store, validator = _setup()
    insert_lead(store, stage="resolved", row_id=1)
    verdict = _one(validator.validate(
        [Delta(kind="lead_transition", target="1", data={"new_stage": "in_progress"})], turn=1
    ))
    assert verdict.kind == VerdictKind.REJECTED.value
    assert "terminal" in verdict.note


def test_lead_same_stage_is_rejected() -> None:
    store, validator = _setup()
    insert_lead(store, stage="accepted", row_id=1)
    verdict = _one(validator.validate(
        [Delta(kind="lead_transition", target="1", data={"new_stage": "accepted"})], turn=1
    ))
    assert verdict.kind == VerdictKind.REJECTED.value
    assert "already" in verdict.note


def test_lead_unknown_stage_and_missing_lead_are_rejected() -> None:
    store, validator = _setup()
    insert_lead(store, stage="unheard", row_id=1)
    unknown = _one(validator.validate(
        [Delta(kind="lead_transition", target="1", data={"new_stage": "solved"})], turn=1
    ))
    missing = _one(validator.validate(
        [Delta(kind="lead_transition", target="9", data={"new_stage": "rumored"})], turn=1
    ))
    assert unknown.kind == VerdictKind.REJECTED.value
    assert "unknown lead stage" in unknown.note
    assert missing.kind == VerdictKind.REJECTED.value
    assert "unknown lead" in missing.note


def test_lead_two_step_chain_in_one_batch() -> None:
    store, validator = _setup()
    insert_lead(store, stage="unheard", row_id=1)
    verdicts = validator.validate([
        Delta(kind="lead_transition", target="1", data={"new_stage": "rumored"}),
        Delta(kind="lead_transition", target="1", data={"new_stage": "accepted"}),
        Delta(kind="lead_transition", target="1", data={"new_stage": "resolved"}),
    ], turn=1)
    assert _kinds(verdicts) == [
        VerdictKind.ACCEPTED.value, VerdictKind.ACCEPTED.value, VerdictKind.REJECTED.value
    ]


@pytest.mark.parametrize("current", sorted(LEAD_TRANSITIONS))
@pytest.mark.parametrize("new_stage", sorted(LEAD_TRANSITIONS))
def test_lead_transition_machine_is_exhaustively_enforced(current: str, new_stage: str) -> None:
    store, validator = _setup()
    insert_lead(store, stage=current, row_id=1)
    verdict = _one(validator.validate(
        [Delta(kind="lead_transition", target="1", data={"new_stage": new_stage})], turn=1
    ))
    should_pass = new_stage in LEAD_TRANSITIONS[current] and new_stage != current
    assert (verdict.kind == VerdictKind.ACCEPTED.value) is should_pass, (
        f"{current} -> {new_stage}: {verdict.kind}: {verdict.note}"
    )
