"""Focused tests for NPC memory + salience and the chronicle tail (spec §3.1, §3.2).

Everything runs against the real ``engine.store.Store`` (SQLite); scoring math
is pinned against the spec formula, and decay is exercised over *simulated
turns* — no sleeps, no RNG, no network.
"""
from __future__ import annotations

import math

import pytest

from engine.config import EngineConfig
from engine.memory import NEAR_MATCH_SIMILARITY, Chronicle, NPCMemoryStore
from engine.models import NPC, ChronicleEntry, NPCMemoryEntry, from_row, to_row
from engine.similarity import LexicalSimilarity
from engine.store import Store


@pytest.fixture()
def store():
    """A real in-memory SQLite store (schema applied on open)."""
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


def _entry(store: Store, row_id: int) -> NPCMemoryEntry:
    row = store.get("npc_memory", row_id)
    assert row is not None
    return from_row(NPCMemoryEntry, row)


# --------------------------------------------------------------------------- #
# add / decay rates
# --------------------------------------------------------------------------- #

def test_add_derives_decay_rate_from_config_by_type(store, config):
    mem = NPCMemoryStore(store, config)
    key = f"npc:{_seed_npc(store)}"

    factual = mem.add(npc_id=key, turn=1, statement="The north gate is barred at night",
                      type="factual")
    promise = mem.add(npc_id=key, turn=1, statement="Marla promised to return the ladle",
                      type="promise")
    observed = mem.add(npc_id=key, turn=1, statement="Marla watched the player leave")
    explicit = mem.add(npc_id=key, turn=1, statement="Marla frowned at the price",
                       type="grievance", decay_rate=0.5)

    assert _entry(store, factual).decay_rate == config.salience.decay_rates["factual"] == 0.0
    assert _entry(store, promise).decay_rate == config.salience.decay_rates["promise"]
    assert _entry(store, observed).decay_rate == config.salience.decay_rates["observed"]
    assert _entry(store, explicit).decay_rate == 0.5
    assert mem.decay_rate_for("never-heard-of-it") == 0.15


def test_add_rejects_unknown_type_and_blank_statement(store):
    mem = NPCMemoryStore(store)
    key = f"npc:{_seed_npc(store)}"
    with pytest.raises(ValueError, match="unknown memory type"):
        mem.add(npc_id=key, turn=1, statement="Marla gossiped", type="gossip")
    with pytest.raises(ValueError, match="non-empty"):
        mem.add(npc_id=key, turn=1, statement="   ")


def test_add_clamps_sentiment_and_stores_the_typed_row(store):
    mem = NPCMemoryStore(store)
    key = f"npc:{_seed_npc(store)}"
    row_id = mem.add(npc_id=key, turn=7, statement="Marla seethed", type="grievance",
                     sentiment=-5.0)
    entry = _entry(store, row_id)
    assert entry.sentiment == -1.0
    assert entry.type == "grievance"
    assert entry.turn_established == 7
    assert entry.reinforced_count == 0
    assert entry.npc_id == key


def test_npc_ids_are_canonicalised_to_npc_colon_id(store):
    mem = NPCMemoryStore(store)
    npc_id = _seed_npc(store, name="Marla")
    mem.add(npc_id=str(npc_id), turn=1, statement="Marla saw the player")
    mem.add(npc_id=f"npc:{npc_id}", turn=2, statement="Marla saw the player again")
    mem.add(npc_id="Marla", turn=3, statement="Marla saw the player once more")
    assert {row["npc_id"] for row in store.find("npc_memory")} == {f"npc:{npc_id}"}
    assert len(mem.retrieve(npc_id="Marla", scene_context=[], turn=3)) == 3


# --------------------------------------------------------------------------- #
# scoring — the spec §3.1 formula
# --------------------------------------------------------------------------- #

def test_score_matches_the_spec_formula_exactly(store, config):
    mem = NPCMemoryStore(store, config)
    key = f"npc:{_seed_npc(store)}"
    row_id = mem.add(npc_id=key, turn=4, statement="the missing shipment went to the mill",
                     type="grievance", sentiment=-0.8)
    store.update("npc_memory", row_id, {"reinforced_count": 3})
    entry = _entry(store, row_id)

    scene = ["the missing shipment from the mill"]
    age = 6  # scored at turn 10
    relevance = LexicalSimilarity().score(entry.statement, scene[0])
    expected = (
        config.salience.w_recency * math.exp(-age / config.salience.recency_half_life_turns)
        + config.salience.w_sentiment * 0.8
        + config.salience.w_relevance * relevance
        + config.salience.w_reinforced * math.log1p(3)
        - config.salience.w_decay * (1 - math.exp(-entry.decay_rate * age))
    )
    assert mem.score(entry, scene_context=scene, turn=10) == pytest.approx(expected, abs=1e-6)


def test_factual_entries_carry_no_decay_penalty_however_old(store, config):
    mem = NPCMemoryStore(store, config)
    key = f"npc:{_seed_npc(store)}"
    row_id = mem.add(npc_id=key, turn=0, statement="The mill burned down in the spring",
                     type="factual", sentiment=0.0)
    entry = _entry(store, row_id)
    for turn in (0, 5, 20, 60):
        expected = config.salience.w_recency * math.exp(-turn / config.salience.recency_half_life_turns)
        assert mem.score(entry, scene_context=["an unrelated quiet market"], turn=turn) == (
            pytest.approx(expected, abs=1e-6)
        )


def test_decay_over_simulated_turns(store, config):
    """An emotional entry fades as turns pass; a factual one only loses recency."""
    mem = NPCMemoryStore(store, config)
    key = f"npc:{_seed_npc(store)}"
    fact = _entry(store, mem.add(npc_id=key, turn=0, statement="The mill burned in the spring",
                                 type="factual", sentiment=0.0))
    grievance = _entry(store, mem.add(npc_id=key, turn=0,
                                      statement="The player mocked Marla at the fair",
                                      type="grievance", sentiment=-1.0))
    scene = ["a quiet market"]

    scores = {
        turn: (
            mem.score(fact, scene_context=scene, turn=turn),
            mem.score(grievance, scene_context=scene, turn=turn),
        )
        for turn in (0, 5, 20, 60)
    }
    grievance_scores = [scores[turn][1] for turn in (0, 5, 20, 60)]
    assert grievance_scores == sorted(grievance_scores, reverse=True)
    assert scores[0][1] > scores[0][0]  # fresh + felt: the insult is loudest at the time
    assert scores[20][0] > scores[20][1]  # stale grievance ranks below the plain fact
    assert scores[60][0] > scores[60][1]
    assert scores[60][1] < -0.5  # decayed hard, not merely de-ranked
    retrieved = mem.retrieve(npc_id=key, scene_context=scene, turn=60)
    assert [entry.id for entry, _score in retrieved] == [fact.id, grievance.id]


def test_salience_ordering_recent_promise_vs_stale_grievance_vs_fact(store, config):
    mem = NPCMemoryStore(store, config)
    key = f"npc:{_seed_npc(store)}"
    fact = mem.add(npc_id=key, turn=1, statement="The missing shipment went to the mill",
                   type="factual")
    stale_grievance = mem.add(npc_id=key, turn=5, statement="The player mocked Marla's ale",
                              type="grievance", sentiment=-0.9)
    recent_promise = mem.add(npc_id=key, turn=34,
                             statement="Marla promised to return the silver ladle",
                             type="promise", sentiment=0.5)

    scene = ["the missing shipment from the mill"]
    scored = {
        row_id: mem.score(_entry(store, row_id), scene_context=scene, turn=38)
        for row_id in (fact, stale_grievance, recent_promise)
    }
    assert scored[recent_promise] > scored[fact] > scored[stale_grievance]
    assert scored[stale_grievance] < 0.0
    retrieved = mem.retrieve(npc_id=key, scene_context=scene, turn=38)
    assert [entry.id for entry, _score in retrieved] == [
        recent_promise, fact, stale_grievance]


def test_scene_relevance_reorders_retrieval(store, config):
    mem = NPCMemoryStore(store, config)
    key = f"npc:{_seed_npc(store)}"
    shipment = mem.add(npc_id=key, turn=10, statement="The shipment went to the mill",
                       type="observed")
    bell = mem.add(npc_id=key, turn=10, statement="The silver bell rang at dusk",
                   type="observed")

    mill_first = mem.retrieve(npc_id=key, scene_context=["the mill by the river"], turn=10)
    bell_first = mem.retrieve(npc_id=key, scene_context=["the silver bell"], turn=10)
    assert [entry.id for entry, _score in mill_first] == [shipment, bell]
    assert [entry.id for entry, _score in bell_first] == [bell, shipment]


def test_sentiment_lifts_an_entry_of_equal_age(store, config):
    mem = NPCMemoryStore(store, config)
    key = f"npc:{_seed_npc(store)}"
    plain = mem.add(npc_id=key, turn=2, statement="Marla counted the casks", sentiment=0.0)
    felt = mem.add(npc_id=key, turn=2, statement="Marla wept over the casks", sentiment=-0.9)
    scene = ["the casks in the yard"]
    scored = {
        row_id: mem.score(_entry(store, row_id), scene_context=scene, turn=3)
        for row_id in (plain, felt)
    }
    assert scored[felt] > scored[plain]


def test_reinforcement_raises_salience(store, config):
    mem = NPCMemoryStore(store, config)
    key = f"npc:{_seed_npc(store)}"
    reinforced = mem.add(npc_id=key, turn=1,
                         statement="Marla heard the wolves howl at the north gate")
    control = mem.add(npc_id=key, turn=1,
                      statement="Marla heard the wolves howl at the south gate")
    assert mem.reinforce(npc_id=key,
                         statement="Marla heard the wolves howl at the north gate", turn=2) == 1
    scene = ["the wolves at the gate"]
    assert mem.score(_entry(store, reinforced), scene_context=scene, turn=3) > (
        mem.score(_entry(store, control), scene_context=scene, turn=3)
    )


# --------------------------------------------------------------------------- #
# retrieval — per-NPC scope, top-K, bookkeeping
# --------------------------------------------------------------------------- #

def test_retrieve_is_top_k_per_npc(store, config):
    mem = NPCMemoryStore(store, config)
    marla = _seed_npc(store, name="Marla")
    tam = _seed_npc(store, name="Tam")
    marla_rows = [
        mem.add(npc_id=f"npc:{marla}", turn=turn, statement=f"Marla noticed event {turn}")
        for turn in range(1, 9)
    ]
    tam_rows = [
        mem.add(npc_id=f"npc:{tam}", turn=turn, statement=f"Tam noticed event {turn}")
        for turn in range(1, 9)
    ]

    top = mem.retrieve(npc_id=f"npc:{marla}", scene_context=["event 8"], turn=9)
    assert len(top) == config.memory.npc_top_k == 6
    assert {entry.npc_id for entry, _score in top} == {f"npc:{marla}"}
    assert {entry.id for entry, _score in top}.isdisjoint(tam_rows)

    other = mem.retrieve(npc_id=f"npc:{tam}", scene_context=["event 8"], turn=9)
    assert {entry.npc_id for entry, _score in other} == {f"npc:{tam}"}
    assert {entry.id for entry, _score in other}.isdisjoint(marla_rows)

    assert len(mem.retrieve(npc_id=f"npc:{marla}", scene_context=[], turn=9, k=2)) == 2
    assert mem.retrieve(npc_id=f"npc:{marla}", scene_context=[], turn=9, k=0) == []


def test_retrieve_updates_last_referenced_turn_only_for_returned_entries(store, config):
    mem = NPCMemoryStore(store, config)
    npc_id = _seed_npc(store)
    rows = [
        mem.add(npc_id=f"npc:{npc_id}", turn=turn, statement=f"Marla noticed event {turn}")
        for turn in range(1, 9)
    ]
    top = mem.retrieve(npc_id=f"npc:{npc_id}", scene_context=["event 8"], turn=40)
    returned = {entry.id for entry, _score in top}
    for row_id in rows:
        expected = 40 if row_id in returned else 0
        assert _entry(store, row_id).last_referenced_turn == expected


def test_retrieve_is_deterministic_and_ties_break_by_recency(store, config):
    mem = NPCMemoryStore(store, config)
    npc_id = _seed_npc(store)
    older = mem.add(npc_id=f"npc:{npc_id}", turn=1, statement="identical observation")
    newer = mem.add(npc_id=f"npc:{npc_id}", turn=5, statement="identical observation")
    first = mem.retrieve(npc_id=f"npc:{npc_id}", scene_context=[], turn=6)
    second = mem.retrieve(npc_id=f"npc:{npc_id}", scene_context=[], turn=6)
    assert [entry.id for entry, _score in first] == [newer, older]
    assert [score for _entry_, score in first] == [score for _entry_, score in second]


# --------------------------------------------------------------------------- #
# reinforce
# --------------------------------------------------------------------------- #

def test_reinforce_bumps_near_matches_only(store, config):
    mem = NPCMemoryStore(store, config)
    key = f"npc:{_seed_npc(store)}"
    target = mem.add(npc_id=key, turn=1, statement="Marla promised to pay the debt")
    other = mem.add(npc_id=key, turn=1, statement="Marla watched the player leave")
    assert LexicalSimilarity().score(
        "Marla promised to pay the debt", "Marla promised to pay the debt tomorrow",
    ) >= NEAR_MATCH_SIMILARITY

    assert mem.reinforce(npc_id=key, statement="Marla promised to pay the debt tomorrow",
                         turn=4) == 1
    assert mem.reinforce(npc_id=key, statement="a completely unrelated observation", turn=4) == 0

    bumped = _entry(store, target)
    assert bumped.reinforced_count == 1
    assert bumped.last_referenced_turn == 4
    untouched = _entry(store, other)
    assert untouched.reinforced_count == 0
    assert untouched.last_referenced_turn == 0


# --------------------------------------------------------------------------- #
# chronicle
# --------------------------------------------------------------------------- #

def test_chronicle_append_keeps_the_structured_fields(store, config):
    chronicle = Chronicle(store, config)
    row_id = chronicle.append(
        turn_id=7, actor="player", action_summary="swung at Tam",
        mechanical_result="attack 12 vs dc 12 — success",
        consequence_oneliner="Tam reeled", verbatim_text='"I swing," you say.',
    )
    entry = from_row(ChronicleEntry, store.get("chronicle", row_id))
    assert (entry.turn_id, entry.actor) == (7, "player")
    assert entry.action_summary == "swung at Tam"
    assert entry.mechanical_result == "attack 12 vs dc 12 — success"
    assert entry.consequence_oneliner == "Tam reeled"
    assert entry.verbatim_text == '"I swing," you say.'


def test_chronicle_tail_is_the_newest_window_newest_last(store, config):
    chronicle = Chronicle(store, config)
    for turn in range(1, 16):
        chronicle.append(turn_id=turn, actor="player", action_summary=f"action {turn}")

    tail = chronicle.tail()
    assert len(tail) == config.memory.chronicle_window == 12
    assert [entry.turn_id for entry in tail] == list(range(4, 16))
    assert chronicle.tail(n=3)[0].action_summary == "action 13"
    assert [entry.turn_id for entry in chronicle.tail(n=3)] == [13, 14, 15]
    assert chronicle.tail(n=0) == []
    assert [entry.turn_id for entry in chronicle.tail(n=100)] == list(range(1, 16))


def test_chronicle_tail_of_an_empty_store_is_empty(store, config):
    assert Chronicle(store, config).tail() == []


def test_verbatim_window_keeps_only_the_most_recent_entries(store, config):
    chronicle = Chronicle(store, config)
    for turn in range(1, 6):
        chronicle.append(turn_id=turn, actor="narrator", action_summary=f"beat {turn}",
                         verbatim_text=f"verbatim {turn}")

    rows = sorted(store.find("chronicle"), key=lambda row: row["id"])
    assert config.memory.chronicle_verbatim == 3
    assert [row["verbatim_text"] for row in rows] == [
        None, None, "verbatim 3", "verbatim 4", "verbatim 5"]


def test_verbatim_window_never_deletes_the_structured_history(store, config):
    chronicle = Chronicle(store, config)
    for turn in range(1, 13):
        chronicle.append(turn_id=turn, actor="narrator", action_summary=f"beat {turn}",
                         verbatim_text=f"verbatim {turn}")
    assert store.count("chronicle") == 12  # structure is unbounded...
    kept = [row for row in store.find("chronicle") if row["verbatim_text"] is not None]
    assert len(kept) == config.memory.chronicle_verbatim  # ...only prose is bounded
    assert [row["turn_id"] for row in kept] == [10, 11, 12]


def test_enforce_verbatim_window_honours_config_and_direct_writes(store, config):
    config.memory.chronicle_verbatim = 2
    chronicle = Chronicle(store, config)
    for turn in range(1, 5):
        # a caller that bypasses append (e.g. a migration) still gets enforced
        store.insert("chronicle", {"turn_id": turn, "actor": "narrator",
                                   "action_summary": f"beat {turn}",
                                   "verbatim_text": f"raw {turn}"})
    chronicle.enforce_verbatim_window()
    rows = sorted(store.find("chronicle"), key=lambda row: row["id"])
    assert [row["verbatim_text"] for row in rows] == [None, None, "raw 3", "raw 4"]


def test_nulled_verbatim_never_comes_back(store, config):
    chronicle = Chronicle(store, config)
    for turn in range(1, 6):
        chronicle.append(turn_id=turn, actor="narrator", action_summary=f"beat {turn}",
                         verbatim_text=f"verbatim {turn}")
    chronicle.enforce_verbatim_window()
    rows = sorted(store.find("chronicle"), key=lambda row: row["id"])
    assert [row["verbatim_text"] for row in rows[:2]] == [None, None]
    assert [entry.verbatim_text for entry in chronicle.tail(n=2)] == ["verbatim 4", "verbatim 5"]
