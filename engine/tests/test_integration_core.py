"""Core-slice integration tests on merged main (I1 card).

Unlike the focused suites (fake store) and R2's ``test_validate_integration.py``
(validator + real store), these tests exercise the landed core as one system:
the real :class:`engine.store.Store` over SQLite, the real ``Validator``
(validate -> commit) and the real mechanics resolver reading live store state.
State goes in through the public APIs, rows come back out of SQLite, and JSON
columns are decoded through ``models.from_row`` exactly as the pipeline will.

Covered checks (I1 card): validator commit writes rows visible via find;
conservation reads real player state; lead transitions update lead rows; JSON
round-trips through the store; store + resolver eligibility read; plus a
resolver -> validator -> store turn slice and commit-time fact re-checking.
"""
from __future__ import annotations

import json

import pytest
from doubles import ScriptedRng

from engine.config import EngineConfig
from engine.models import (
    NPC,
    Character,
    Delta,
    Intent,
    Lead,
    WorldFact,
    from_row,
    to_row,
)
from engine.resolve import resolve_action
from engine.store import Store
from engine.validate import Validator


@pytest.fixture()
def store():
    """A real in-memory SQLite store (schema applied on open)."""
    s = Store(":memory:")
    try:
        yield s
    finally:
        s.close()


@pytest.fixture()
def validator(store):
    return Validator(store, EngineConfig())


# -- seeding / read helpers (raw columns in, decoded models out) -------------- #

def _seed_character(store: Store, **overrides) -> int:
    values = {
        "name": "Player",
        "stats": {"hp": 10, "max_hp": 12, "currency": 25},
        "inventory": [],
        "location_id": "yard",
    }
    values.update(overrides)
    return store.insert("characters", to_row(Character(**values)))


def _seed_npc(store: Store, name: str = "Marla", *, alive: bool = True,
              location_id: str = "", personality: dict | None = None) -> int:
    return store.insert("npcs", to_row(NPC(
        name=name, alive=alive, location_id=location_id,
        personality=personality or {},
    )))


def _seed_lead(store: Store, title: str = "The missing shipment",
               stage: str = "unheard") -> int:
    return store.insert("leads", to_row(Lead(title=title, stage=stage)))


def _character(store: Store) -> Character:
    """Decode the player row the way the rest of the engine does."""
    row = store.find_one("characters", order_by="id")
    assert row is not None
    return from_row(Character, row)


def _commit(validator: Validator, deltas: list[Delta], turn: int):
    return validator.commit(validator.validate(deltas, turn=turn), turn=turn)


# --------------------------------------------------------------------------- #
# 1. validator commit -> rows visible via find
# --------------------------------------------------------------------------- #

def test_validator_commit_rows_are_visible_via_find(store, validator):
    npc_id = _seed_npc(store, personality={"baseline_valence": 0.2})
    npc_key = f"npc:{npc_id}"

    verdicts = validator.validate([
        Delta(kind="mood", target=f"npc:{npc_id}",
              data={"valence_delta": 0.3, "arousal_delta": 0.1, "cause": "a kind word"}),
        Delta(kind="relationship", target=npc_key, data={
            "category": "trust", "delta": 8, "reason": "the player kept their word",
            "decay_class": "fast",
        }),
        Delta(kind="fact", target="", data={
            "statement": "Marla the tanner owes the player a favour",
            "pinned": True, "tags": ["marla"], "source": "narrator",
        }),
    ], turn=4)

    # validate() only reads: nothing may be in the DB before commit.
    assert store.count("moods") == 0
    assert store.count("relationship_ledger") == 0
    assert store.count("world_facts") == 0

    report = validator.commit(verdicts, turn=4)
    assert not report.rejected
    assert len(report.applied) == 3

    moods = store.find("moods", {"npc_id": npc_key})
    assert len(moods) == 1
    assert moods[0]["valence"] == pytest.approx(0.5)  # baseline 0.2 + 0.3
    assert moods[0]["arousal"] == pytest.approx(0.1)
    assert moods[0]["last_updated_turn"] == 4

    ledger = store.find("relationship_ledger", {"npc_id": npc_key})
    assert len(ledger) == 1
    assert ledger[0]["player_id"] == "player"
    assert ledger[0]["turn"] == 4
    assert ledger[0]["delta"] == pytest.approx(8.0)
    assert ledger[0]["category"] == "trust"
    assert ledger[0]["decay_class"] == "fast"
    assert ledger[0]["reason"] == "the player kept their word"

    facts = store.find("world_facts")
    assert len(facts) == 1
    fact = from_row(WorldFact, facts[0])
    assert fact.statement == "Marla the tanner owes the player a favour"
    assert fact.pinned is True
    assert fact.established_turn == 4
    assert fact.tags == ["marla"]


# --------------------------------------------------------------------------- #
# 2. conservation reads the real player state
# --------------------------------------------------------------------------- #

def test_conservation_reads_real_player_state(store, validator):
    _seed_character(
        store,
        stats={"hp": 10, "max_hp": 12, "currency": 25},
        inventory=[{"item_id": "rope", "qty": 2, "flags": {"hemp": True}}],
    )

    overspend = validator.validate(
        [Delta(kind="currency", target="", data={"amount": -30})], turn=1)
    assert overspend[0].kind == "rejected"
    assert "conservation" in overspend[0].note
    assert _character(store).stats["currency"] == 25

    batch_spend = validator.validate([
        Delta(kind="currency", target="", data={"amount": -15}),
        Delta(kind="currency", target="", data={"amount": -15}),
    ], turn=1)
    assert batch_spend[0].kind == "accepted"
    assert batch_spend[1].kind == "rejected"  # 25 - 15 = 10 in the running batch
    assert _character(store).stats["currency"] == 25  # still validate-only

    overremove = validator.validate(
        [Delta(kind="inventory_remove", target="rope", data={"qty": 3})], turn=1)
    assert overremove[0].kind == "rejected"
    assert _character(store).inventory == [
        {"item_id": "rope", "qty": 2, "flags": {"hemp": True}}]

    report = _commit(validator, [
        Delta(kind="currency", target="", data={"amount": -25}),
        Delta(kind="inventory_remove", target="rope", data={"qty": 2}),
    ], turn=1)
    assert not report.rejected
    character = _character(store)
    assert character.stats["currency"] == 0
    assert character.inventory == []  # removal at zero drops the entry


# --------------------------------------------------------------------------- #
# 3. lead transitions update the lead rows
# --------------------------------------------------------------------------- #

def test_lead_transitions_update_lead_rows(store, validator):
    lead_id = _seed_lead(store)

    illegal = _commit(validator, [Delta(kind="lead_transition", target=str(lead_id),
                                        data={"new_stage": "resolved",
                                              "justification": "skip the chain"})], turn=1)
    assert len(illegal.rejected) == 1
    row = store.find_one("leads", {"id": lead_id})
    assert row["stage"] == "unheard"
    assert json.loads(row["stage_history"]) == []

    _commit(validator, [Delta(kind="lead_transition", target=f"lead:{lead_id}",
                              data={"new_stage": "rumored",
                                    "justification": "the innkeeper mentioned it"})], turn=2)
    _commit(validator, [Delta(kind="lead_transition", target=str(lead_id),
                              data={"new_stage": "accepted"})], turn=3)
    _commit(validator, [Delta(kind="lead_transition", target=str(lead_id),
                              data={"new_stage": "in_progress",
                                    "justification": "the player set out"})], turn=4)

    row = store.find_one("leads", {"id": lead_id})
    assert row["stage"] == "in_progress"
    lead = from_row(Lead, row)
    assert [entry["stage"] for entry in lead.stage_history] == [
        "rumored", "accepted", "in_progress"]
    assert lead.stage_history[0] == {
        "stage": "rumored", "turn": 2, "trigger": "the innkeeper mentioned it"}
    assert lead.stage_history[1]["turn"] == 3


# --------------------------------------------------------------------------- #
# 4. JSON columns round-trip through the store
# --------------------------------------------------------------------------- #

def test_json_columns_round_trip_through_the_store(store, validator):
    npc_id = _seed_npc(
        store, name="Marla",
        personality={"traits": ["wary", "proud"], "baseline_valence": 0.2,
                     "speech_pattern": "clipped"},
        location_id="yard",
    )
    _seed_character(
        store,
        stats={"hp": 9, "max_hp": 12, "currency": 17, "dex": 14},
        inventory=[{"item_id": "ladle", "qty": 1, "flags": {"stolen": True}}],
        status_effects=["bruised"],
    )
    _commit(validator, [
        Delta(kind="hp", target="", data={"delta": -4, "cause": "a thrown cup"}),
        Delta(kind="fact", target="", data={
            "statement": "The tanner's ladle went missing", "tags": ["marla", "prop"]}),
    ], turn=1)

    raw = store.find_one("characters")
    assert isinstance(raw["stats"], str)  # stored as JSON text
    assert isinstance(raw["inventory"], str)
    character = from_row(Character, raw)
    assert character.stats["hp"] == 5  # 9 - 4 committed
    assert character.stats["max_hp"] == 12
    assert character.stats["currency"] == 17
    assert character.inventory == [{"item_id": "ladle", "qty": 1, "flags": {"stolen": True}}]
    assert character.status_effects == ["bruised"]

    npc = from_row(NPC, store.find_one("npcs", {"id": npc_id}))
    assert npc.alive is True  # SQLite 0/1 normalizes back to bool
    assert npc.personality["traits"] == ["wary", "proud"]
    assert npc.location_id == "yard"

    fact = from_row(WorldFact, store.find_one("world_facts"))
    assert fact.tags == ["marla", "prop"]
    assert fact.pinned is False


# --------------------------------------------------------------------------- #
# 5. resolver eligibility reads live store state
# --------------------------------------------------------------------------- #

def test_resolver_reads_eligibility_from_the_real_store(store):
    _seed_character(store, location_id="yard")
    dead = _seed_npc(store, name="Grunn", alive=False, location_id="yard")
    away = _seed_npc(store, name="Marla", location_id="mill")
    present = _seed_npc(store, name="Tam", location_id="yard")
    rng = ScriptedRng([15, 15, 15])

    dead_outcome = resolve_action(
        Intent(kind="action", target=f"npc:{dead}", text="strike the corpse"),
        rng=rng, store=store, turn=1)
    assert dead_outcome.kind == "blocked"
    assert dead_outcome.check is None
    assert "dead" in dead_outcome.verdict_line
    assert rng.calls == []  # a blocked action rolls nothing

    away_outcome = resolve_action(
        Intent(kind="action", target=f"npc:{away}", text="strike"),
        rng=rng, store=store, turn=1)
    assert away_outcome.kind == "blocked"
    assert "not present" in away_outcome.verdict_line

    live_outcome = resolve_action(
        Intent(kind="action", target=f"npc:{present}", text="strike"),
        rng=rng, store=store, turn=1)
    assert live_outcome.kind == "attack"
    assert live_outcome.check is not None
    assert live_outcome.check.band == "success"  # 15 + 0 (bare attack) vs dc 12
    assert any("present and alive" in note for note in live_outcome.notes)

    prop_outcome = resolve_action(
        Intent(kind="action", target="the candlestick", text="swing at it"),
        rng=rng, store=store, turn=1)
    assert prop_outcome.kind == "attack"  # unknown target: no gate, may be a prop
    assert any("not a known NPC" in note for note in prop_outcome.notes)


# --------------------------------------------------------------------------- #
# 6. resolver -> validator -> store: one real turn slice
# --------------------------------------------------------------------------- #

def test_resolver_effects_commit_through_the_validator(store, validator):
    _seed_character(store, stats={"hp": 10, "max_hp": 12, "currency": 5},
                    location_id="yard")
    target = _seed_npc(store, name="Tam", location_id="yard")

    def hp_on_a_landed_hit(intent: Intent, outcome) -> list[Delta]:
        if outcome.check is not None and outcome.check.band in ("success", "critical"):
            return [Delta(kind="hp", target="", data={
                "delta": -3, "cause": "Tam's counter"})]
        return []

    outcome = resolve_action(
        Intent(kind="action", target=f"npc:{target}", text="swing at Tam"),
        rng=ScriptedRng([15]), store=store, turn=2,
        effect_rules=[hp_on_a_landed_hit])
    assert outcome.kind == "attack"
    assert outcome.check.band == "success"
    assert len(outcome.effects) == 1  # mechanics never write; they propose

    report = validator.commit(validator.validate(outcome.effects, turn=2), turn=2)
    assert not report.rejected
    assert len(report.applied) == 1
    assert _character(store).stats["hp"] == 7  # 10 - 3, now visible in SQLite


# --------------------------------------------------------------------------- #
# 7. commit re-checks facts against the live store
# --------------------------------------------------------------------------- #

def test_commit_rechecks_facts_written_between_validate_and_commit(store, validator):
    verdicts = validator.validate(
        [Delta(kind="fact", target="", data={"statement": "Marla is alive"})], turn=2)
    assert verdicts[0].kind == "accepted"  # nothing in the store yet

    # A concurrent writer commits canon between validate and commit (race).
    store.insert("world_facts", to_row(WorldFact(
        statement="Marla is dead", pinned=True, established_turn=2, source="narrator")))

    report = validator.commit(verdicts, turn=2)
    assert len(report.rejected) == 1
    assert "commit-time contradiction check" in report.rejected[0].note
    assert [from_row(WorldFact, row).statement for row in store.find("world_facts")] == [
        "Marla is dead"]


def test_near_duplicate_fact_reinforces_instead_of_inserting(store, validator):
    first = _commit(validator, [Delta(kind="fact", target="", data={
        "statement": "The north gate is barred at night"})], turn=1)
    assert len(first.accepted) == 1
    assert store.count("world_facts") == 1

    duplicate = _commit(validator, [Delta(kind="fact", target="", data={
        "statement": "The north gate is barred at night."})], turn=2)
    assert not duplicate.rejected
    assert any("reinforced" in verdict.note for verdict in duplicate.accepted)
    assert store.count("world_facts") == 1  # no second row
