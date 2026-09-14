"""Focused tests for world facts: auto-pinning, retrieval and the contradiction
scan (spec §3.6), plus the MemoryBundle wiring.

The contradiction scan must agree with the Validator's policy (R2 owns the
heuristics); one test pins that seam explicitly.
"""
from __future__ import annotations

import pytest

from engine.config import EngineConfig
from engine.memory import (
    FACT_REINFORCE_SIMILARITY,
    Chronicle,
    FactStore,
    MemoryBundle,
    MoodTracker,
    NPCMemoryStore,
    RelationshipLedger,
    SagaDigest,
    auto_pin_worthy,
)
from engine.models import NPC, WorldFact, from_row, to_row
from engine.store import Store
from engine.validate import REINFORCE_SIMILARITY, Validator


@pytest.fixture()
def store():
    s = Store(":memory:")
    try:
        yield s
    finally:
        s.close()


@pytest.fixture()
def config():
    return EngineConfig()


def _seed_npc(store: Store, name: str = "Marla", **overrides) -> int:
    values = {"name": name, "personality": {}, "disposition_base": 0.0}
    values.update(overrides)
    return store.insert("npcs", to_row(NPC(**values)))


# --------------------------------------------------------------------------- #
# auto_pin_worthy
# --------------------------------------------------------------------------- #

@pytest.mark.parametrize(("statement", "kind", "expected"), [
    ("Marla promised to return the silver ladle", None, True),
    ("The player swore an oath to protect the mill", None, True),
    ("Grunn was killed at the ford", None, True),
    ("The tanner is dead", None, True),
    ("Marla betrayed the player at the ford", None, True),
    ("The player's vow binds the whole party", None, True),
    ("The bridge collapsed into the river", None, False),
    ("The north gate is barred at night", None, False),
    ("The grass in the yard is green", None, False),
    ("The moon is made of cheese", "player", True),
    ("The moon is made of cheese", "narrator", False),
    ("Anything at all", "promise", True),
])
def test_auto_pin_worthy_cases(statement, kind, expected):
    assert auto_pin_worthy(statement, kind=kind) is expected


def test_auto_pin_worthy_needs_an_actual_statement():
    assert auto_pin_worthy("   ") is False
    assert auto_pin_worthy(None) is False


# --------------------------------------------------------------------------- #
# add — auto-pin and reinforcer-style de-duplication
# --------------------------------------------------------------------------- #

def test_add_auto_pins_only_worthwhile_statements(store):
    facts = FactStore(store)
    pinned_id = facts.add(statement="Grunn is dead", turn=3, source="narrator")
    plain_id = facts.add(statement="The north gate is barred at night", turn=3, source="narrator")
    player_id = facts.add(statement="The moon is made of cheese", turn=3, source="player")

    rows = {row["id"]: row for row in store.find("world_facts")}
    assert rows[pinned_id]["pinned"] == 1
    assert rows[plain_id]["pinned"] == 0
    assert rows[player_id]["pinned"] == 1  # explicit player canon is pin-worthy
    assert [fact.id for fact in facts.pinned_facts()] == [pinned_id, player_id]


def test_add_respects_an_explicit_pin_decision(store):
    facts = FactStore(store)
    unpinned = facts.add(statement="Grunn is dead", turn=1, pinned=False)
    pinned = facts.add(statement="The grass is green", turn=1, pinned=True)
    assert [fact.id for fact in facts.pinned_facts()] == [pinned]
    assert store.get("world_facts", unpinned)["pinned"] == 0


def test_add_stores_tags_source_and_turn(store):
    facts = FactStore(store)
    fact_id = facts.add(statement="The mill burned in autumn", turn=6, source="narrator",
                        tags=["place", "loss"])
    fact = from_row(WorldFact, store.get("world_facts", fact_id))
    assert (fact.established_turn, fact.source) == (6, "narrator")
    assert fact.tags == ["place", "loss"]
    assert fact.contradicts == []


def test_near_duplicate_fact_reinforces_instead_of_duplicating(store):
    facts = FactStore(store)
    first = facts.add(statement="The north gate is barred at night", turn=1, source="narrator")
    again = facts.add(statement="The north gate is barred at night.", turn=2, source="player")
    assert again == first  # reinforced: the stated fact is returned, not duplicated
    assert store.count("world_facts") == 1
    row = store.get("world_facts", first)
    assert row["established_turn"] == 1  # original provenance is untouched
    assert row["source"] == "narrator"


def test_fact_reinforce_threshold_matches_the_validator_policy():
    assert FACT_REINFORCE_SIMILARITY == REINFORCE_SIMILARITY


def test_add_rejects_blank_statements(store):
    with pytest.raises(ValueError, match="non-empty"):
        FactStore(store).add(statement="   ", turn=1)


# --------------------------------------------------------------------------- #
# retrieve
# --------------------------------------------------------------------------- #

def test_retrieve_returns_relevant_pinned_facts(store, config):
    facts = FactStore(store, config)
    pinned = facts.add(statement="Marla promised to return the silver ladle", turn=2)
    facts.add(statement="The north gate is barred at night", turn=2)
    got = facts.retrieve(scene_context=["the silver ladle"], turn=3)
    assert [fact.id for fact in got] == [pinned]
    assert got[0].pinned is True


def test_retrieve_drops_pinned_facts_the_scene_does_not_reach(store, config):
    facts = FactStore(store, config)
    facts.add(statement="Marla promised to return the silver ladle", turn=2)
    assert facts.retrieve(scene_context=["barrels of salt at the market"], turn=3) == []


def test_retrieve_ranks_unpinned_facts_by_scene_relevance(store, config):
    facts = FactStore(store, config)
    exact = facts.add(statement="The salt mines flooded in spring", turn=1)
    passing = facts.add(statement="Wolves prowl the salt mines at night", turn=1)
    unrelated = facts.add(statement="The mill burned in autumn", turn=1)

    got = facts.retrieve(scene_context=["the salt mines"], turn=2)
    assert [fact.id for fact in got] == [exact, passing]
    assert unrelated not in [fact.id for fact in got]
    assert [fact.id for fact in facts.retrieve(scene_context=["the salt mines"], turn=2,
                                               limit=1)] == [exact]


def test_retrieve_keeps_pinned_ahead_of_the_limit(store, config):
    facts = FactStore(store, config)
    pinned = facts.add(statement="The salt mines are sealed off", turn=1, pinned=True)
    matched = facts.add(statement="The salt mines flooded in spring", turn=1)
    got = facts.retrieve(scene_context=["the salt mines"], turn=2, limit=1)
    assert [fact.id for fact in got] == [pinned]
    assert matched != pinned


def test_retrieve_without_scene_context_returns_only_pinned(store, config):
    facts = FactStore(store, config)
    pinned = facts.add(statement="Marla is dead", turn=1)
    facts.add(statement="The salt mines flooded in spring", turn=1)
    got = facts.retrieve(scene_context=[], turn=2)
    assert [fact.id for fact in got] == [pinned]


# --------------------------------------------------------------------------- #
# contradiction_scan
# --------------------------------------------------------------------------- #

def test_contradiction_scan_flags_antonym_flips(store):
    facts = FactStore(store)
    fact_id = facts.add(statement="Marla is dead", turn=4, pinned=True)
    assert facts.contradiction_scan("Marla is alive") == [fact_id]
    assert facts.contradiction_scan("Marla is alive", exclude_fact_id=fact_id) == []
    assert facts.contradiction_scan("The grass is green in the yard") == []


def test_contradiction_scan_ignores_near_duplicates(store):
    facts = FactStore(store)
    facts.add(statement="The north gate is barred at night", turn=1)
    assert facts.contradiction_scan("The north gate is barred at night.") == []


def test_contradiction_scan_agrees_with_the_validator(store):
    facts = FactStore(store)
    validator = Validator(store)
    facts.add(statement="Marla is dead", turn=2, pinned=True)
    facts.add(statement="The counting house lists 12 debts", turn=2)
    for statement in ("Marla is alive", "The counting house lists 4 debts",
                      "The grass is green", "The north gate is barred at night."):
        assert facts.contradiction_scan(statement) == validator.check_contradiction(statement)


# --------------------------------------------------------------------------- #
# MemoryBundle
# --------------------------------------------------------------------------- #

def test_memory_bundle_wires_every_component_onto_one_store(store, config):
    bundle = MemoryBundle.build(store, config)
    assert isinstance(bundle.npc_memory, NPCMemoryStore)
    assert isinstance(bundle.chronicle, Chronicle)
    assert isinstance(bundle.ledger, RelationshipLedger)
    assert isinstance(bundle.moods, MoodTracker)
    assert isinstance(bundle.saga, SagaDigest)
    assert isinstance(bundle.facts, FactStore)
    for component in (bundle.npc_memory, bundle.chronicle, bundle.ledger, bundle.moods,
                      bundle.saga, bundle.facts):
        assert component.store is store

    # one real write through each component, all landing on the same store
    npc_id = _seed_npc(store)
    key = f"npc:{npc_id}"
    bundle.npc_memory.add(npc_id=key, turn=1, statement="Marla watched the player",
                          type="observed")
    bundle.chronicle.append(turn_id=1, actor="player", action_summary="entered the yard")
    bundle.ledger.append_delta(npc_id=key, player_id="player", turn=1, delta=5,
                               reason="nodded politely", category="trust")
    bundle.moods.ensure(npc_id=key)
    bundle.facts.add(statement="Marla is the tanner", turn=1)

    assert len(bundle.npc_memory.retrieve(npc_id=key, scene_context=[], turn=1)) == 1
    assert bundle.chronicle.tail()[0].action_summary == "entered the yard"
    assert bundle.ledger.currents(npc_id=key, player_id="player", turn=1)["trust"] == 5.0
    assert bundle.moods.current(npc_id=key, turn=1).valence == 0.0
    assert bundle.facts.pinned_facts() == []
    assert bundle.saga.level_text("campaign") is None


def test_memory_bundle_passes_the_summarizer_and_sim_through(store, config):
    seen: list[list[str]] = []

    def summarizer(texts):
        seen.append(list(texts))
        return "digest"

    bundle = MemoryBundle.build(store, config, summarizer=summarizer)
    config.memory.session_summary_turns = 1
    bundle.chronicle.append(turn_id=1, actor="player", action_summary="entered the yard")
    rows = bundle.saga.maybe_summarize(turn=1)
    assert "digest" in rows[0].text
    assert seen == [["T1 player: entered the yard"]]
