"""Focused tests for the relationship ledger and the mood tracker (spec §3.5, §3.7).

The two §3.7 failure modes are pinned explicitly: a mood spike must NOT move
the long-term relationship, and an old grievance must decay while the ledger
history that explains it remains intact.
"""
from __future__ import annotations

import math

import pytest

from engine.config import EngineConfig
from engine.memory import MoodTracker, RelationshipLedger
from engine.models import NPC, MoodState, from_row, to_row
from engine.store import Store


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


def _mood(store: Store) -> MoodState:
    row = store.find_one("moods")
    assert row is not None
    return from_row(MoodState, row)


# --------------------------------------------------------------------------- #
# Ledger — append-only, decay classed, reason based
# --------------------------------------------------------------------------- #

def test_default_decay_class_follows_the_magnitude_rule():
    assert RelationshipLedger.default_decay_class(25) == "durable"
    assert RelationshipLedger.default_decay_class(-40) == "durable"
    assert RelationshipLedger.default_decay_class(24.9) == "slow"
    assert RelationshipLedger.default_decay_class(-10) == "slow"
    assert RelationshipLedger.default_decay_class(9.99) == "fast"
    assert RelationshipLedger.default_decay_class(0) == "fast"


def test_append_delta_canonicalises_and_clamps(store, config):
    ledger = RelationshipLedger(store, config)
    npc_id = _seed_npc(store)
    row_id = ledger.append_delta(npc_id=str(npc_id), player_id="player", turn=3, delta=-999,
                                 reason="sold the player out", category="Trust")
    row = store.find_one("relationship_ledger", {"id": row_id})
    assert row["npc_id"] == f"npc:{npc_id}"
    assert row["player_id"] == "player"
    assert row["turn"] == 3
    assert row["delta"] == -config.memory.relationship_event_bound  # -999 clamped to -40
    assert row["decay_class"] == "durable"
    assert row["category"] == "trust"
    assert row["reason"] == "sold the player out"


def test_append_delta_rejects_unknown_category_and_decay_class(store):
    ledger = RelationshipLedger(store)
    npc_id = _seed_npc(store)
    with pytest.raises(ValueError, match="relationship category"):
        ledger.append_delta(npc_id=str(npc_id), player_id="player", turn=1, delta=5,
                            reason="", category="loyalty")
    with pytest.raises(ValueError, match="decay class"):
        ledger.append_delta(npc_id=str(npc_id), player_id="player", turn=1, delta=5,
                            reason="", category="trust", decay_class="permanent")


def test_currents_anchor_at_disposition_base_and_decay_per_class(store, config):
    ledger = RelationshipLedger(store, config)
    npc_id = _seed_npc(store, disposition_base=10.0)
    key = f"npc:{npc_id}"
    ledger.append_delta(npc_id=key, player_id="player", turn=1, delta=-30,
                        reason="stole the ladle", category="trust")
    ledger.append_delta(npc_id=key, player_id="player", turn=1, delta=6,
                        reason="paid for the ale", category="affection")

    at_event = ledger.currents(npc_id=key, player_id="player", turn=1)
    assert at_event["trust"] == pytest.approx(10.0 - 30.0)  # anchor + the full delta
    assert at_event["affection"] == pytest.approx(10.0 + 6.0)
    assert at_event["total"] == pytest.approx(10.0 - 30.0 + 6.0)

    later = ledger.currents(npc_id=key, player_id="player", turn=31)
    assert later["trust"] == pytest.approx(-20.0)  # durable: a betrayal does not fade
    assert later["affection"] == pytest.approx(10.0 + 6.0 * math.exp(-0.08 * 30))  # fast
    assert later["total"] == pytest.approx(10.0 - 30.0 + 6.0 * math.exp(-2.4))
    assert set(later) == {"trust", "affection", "respect", "fear", "debt", "total"}


def test_currents_meters_include_the_disposition_anchor(store, config):
    """validate.py's ±100 clamp reads ``currents[category]`` (its
    disposition-base test pins this): the anchor must be inside the meter."""
    ledger = RelationshipLedger(store, config)
    npc_id = _seed_npc(store, disposition_base=95.0)
    key = f"npc:{npc_id}"

    untouched = ledger.currents(npc_id=key, player_id="player", turn=1)
    assert untouched["affection"] == 95.0  # no ledger rows: the bare anchor
    assert untouched["trust"] == 95.0
    assert untouched["total"] == 95.0

    ledger.append_delta(npc_id=key, player_id="player", turn=1, delta=5,
                        reason="a nod", category="affection")
    current = ledger.currents(npc_id=key, player_id="player", turn=1)
    assert current["affection"] == pytest.approx(100.0)
    assert current["total"] == pytest.approx(100.0)
    assert current["trust"] == 95.0  # other meters carry the anchor only


def test_currents_are_scoped_to_npc_and_player(store, config):
    ledger = RelationshipLedger(store, config)
    marla = _seed_npc(store, name="Marla")
    tam = _seed_npc(store, name="Tam")
    ledger.append_delta(npc_id=f"npc:{marla}", player_id="player", turn=1, delta=-20,
                        reason="the insult", category="respect")
    ledger.append_delta(npc_id=f"npc:{marla}", player_id="someone-else", turn=1, delta=-40,
                        reason="not this player", category="respect")
    ledger.append_delta(npc_id=f"npc:{tam}", player_id="player", turn=1, delta=40,
                        reason="the favour", category="trust")

    assert ledger.currents(npc_id=f"npc:{marla}", player_id="player", turn=1)["total"] == -20.0
    assert ledger.currents(npc_id=f"npc:{tam}", player_id="player", turn=1)["total"] == 40.0
    assert ledger.currents(npc_id=f"npc:{marla}", player_id="someone-else", turn=1)["total"] == -40.0


def test_old_grievance_decays_but_the_relationship_history_remains(store, config):
    """Spec §3.7 failure mode (a): stale fury fades, the audit trail does not."""
    ledger = RelationshipLedger(store, config)
    npc_id = _seed_npc(store, disposition_base=0.0)
    key = f"npc:{npc_id}"
    ledger.append_delta(npc_id=key, player_id="player", turn=1, delta=-8,
                        reason="mocked the ale", category="respect")  # fast: minor slight
    ledger.append_delta(npc_id=key, player_id="player", turn=1, delta=-30,
                        reason="sold the player out", category="trust")  # durable: major

    rows_before = store.find("relationship_ledger", {"npc_id": key}, order_by="id")
    current = ledger.currents(npc_id=key, player_id="player", turn=61)
    assert current["respect"] == pytest.approx(-8 * math.exp(-0.08 * 60))
    assert abs(current["respect"]) < 0.1  # the small grievance has faded away...
    assert current["trust"] == pytest.approx(-30.0)  # ...the betrayal has not
    assert store.find("relationship_ledger", {"npc_id": key}, order_by="id") == rows_before
    assert [row["reason"] for row in rows_before] == ["mocked the ale", "sold the player out"]


def test_describe_is_reason_based_deterministic_and_capped(store, config):
    ledger = RelationshipLedger(store, config)
    npc_id = _seed_npc(store, name="Marla")
    key = f"npc:{npc_id}"
    ledger.append_delta(npc_id=key, player_id="player", turn=1, delta=-30,
                        reason="stole the ladle", category="trust")
    ledger.append_delta(npc_id=key, player_id="player", turn=1, delta=6,
                        reason="paid for the ale", category="affection")
    ledger.append_delta(npc_id=key, player_id="player", turn=1, delta=4,
                        reason="shared the road", category="respect")
    ledger.append_delta(npc_id=key, player_id="player", turn=1, delta=-1,
                        reason="nodded curtly", category="fear")

    line = ledger.describe(npc_id=key, player_id="player", turn=1)
    assert line == (
        "Marla: total -21.0 — trust -30.0 (stole the ladle, -30.0); "
        "affection +6.0 (paid for the ale, +6.0); "
        "respect +4.0 (shared the road, +4.0)"
    )  # capped at the three strongest reasons; "nodded curtly" (fear -1) is out
    assert "nodded curtly" not in line
    assert ledger.describe(npc_id=key, player_id="player", turn=1) == line  # deterministic


def test_describe_names_the_anchor_when_it_is_not_zero(store, config):
    ledger = RelationshipLedger(store, config)
    npc_id = _seed_npc(store, name="Marla", disposition_base=10.0)
    ledger.append_delta(npc_id=f"npc:{npc_id}", player_id="player", turn=1, delta=-5,
                        reason="a slight", category="respect")
    assert ledger.describe(npc_id=f"npc:{npc_id}", player_id="player", turn=1) == (
        "Marla: total +5.0 (anchor +10.0) — respect +5.0 (a slight, -5.0)"
    )


def test_describe_without_history_reports_the_anchor(store, config):
    ledger = RelationshipLedger(store, config)
    npc_id = _seed_npc(store, name="Marla", disposition_base=12.0)
    assert ledger.describe(npc_id=f"npc:{npc_id}", player_id="player", turn=1) == (
        "Marla: total +12.0 — no ledger entries yet"
    )


def test_describe_reports_a_missing_reason_honestly(store, config):
    ledger = RelationshipLedger(store, config)
    npc_id = _seed_npc(store, name="Marla")
    ledger.append_delta(npc_id=f"npc:{npc_id}", player_id="player", turn=1, delta=-30,
                        reason="", category="trust")
    assert ledger.describe(npc_id=f"npc:{npc_id}", player_id="player", turn=1) == (
        "Marla: total -30.0 — trust -30.0 (no reason recorded, -30.0)"
    )


# --------------------------------------------------------------------------- #
# Moods — transient, half-life decay, personality baselines
# --------------------------------------------------------------------------- #

def test_ensure_seeds_baselines_from_personality_and_is_idempotent(store):
    moods = MoodTracker(store)
    npc_id = _seed_npc(store, personality={"baseline_valence": 0.25,
                                           "baseline_arousal": -0.1})
    key = f"npc:{npc_id}"
    moods.ensure(npc_id=key)
    moods.ensure(npc_id=str(npc_id))  # same row, once
    assert store.count("moods") == 1
    state = _mood(store)
    assert (state.valence, state.arousal) == (0.25, -0.1)
    assert (state.baseline_valence, state.baseline_arousal) == (0.25, -0.1)
    assert state.npc_id == key


def test_ensure_accepts_explicit_baselines_and_refreshes_them(store):
    moods = MoodTracker(store)
    npc_id = _seed_npc(store, personality={"baseline_valence": 0.25})
    key = f"npc:{npc_id}"
    moods.ensure(npc_id=key, baseline_valence=0.5, baseline_arousal=0.2)
    state = _mood(store)
    assert (state.baseline_valence, state.baseline_arousal) == (0.5, 0.2)
    assert (state.valence, state.arousal) == (0.5, 0.2)  # fresh rows start at baseline
    moods.ensure(npc_id=key, baseline_valence=0.6, baseline_arousal=0.2)
    state = _mood(store)
    assert (state.baseline_valence, state.baseline_arousal) == (0.6, 0.2)
    assert state.valence == 0.5  # current mood is not re-anchored by an ensure


def test_apply_decays_to_turn_then_spikes_and_persists(store, config):
    moods = MoodTracker(store, config)  # default half-life: 6 turns
    npc_id = _seed_npc(store)
    key = f"npc:{npc_id}"
    moods.ensure(npc_id=key)
    moods.apply(npc_id=key, valence_delta=1.0, arousal_delta=0.5, turn=10, cause="a gift")
    state = _mood(store)
    assert (state.valence, state.arousal) == (1.0, 0.5)
    assert state.last_updated_turn == 10

    moods.apply(npc_id=key, valence_delta=0.0, arousal_delta=0.0, turn=13)  # half a half-life
    state = _mood(store)
    assert state.valence == pytest.approx(0.5 ** 0.5, abs=1e-6)
    assert state.arousal == pytest.approx(0.5 * 0.5 ** 0.5, abs=1e-6)
    assert state.last_updated_turn == 13


def test_apply_clamps_to_the_mood_range(store, config):
    moods = MoodTracker(store, config)
    npc_id = _seed_npc(store)
    key = f"npc:{npc_id}"
    moods.apply(npc_id=key, valence_delta=5.0, arousal_delta=5.0, turn=1)
    assert (_mood(store).valence, _mood(store).arousal) == (1.0, 1.0)
    moods.apply(npc_id=key, valence_delta=-5.0, arousal_delta=-5.0, turn=1)
    assert (_mood(store).valence, _mood(store).arousal) == (-1.0, -1.0)


def test_current_decays_without_writing_and_falls_back_to_personality(store, config):
    moods = MoodTracker(store, config)
    npc_id = _seed_npc(store, personality={"baseline_valence": 0.2, "baseline_arousal": 0.1})
    key = f"npc:{npc_id}"

    # no row yet: a pure read, seeded from personality, nothing written
    view = moods.current(npc_id=key, turn=0)
    assert (view.valence, view.arousal) == (0.2, 0.1)
    assert store.count("moods") == 0

    moods.ensure(npc_id=key)
    moods.apply(npc_id=key, valence_delta=0.8, arousal_delta=0.0, turn=10)
    stored_before = store.find_one("moods")

    view = moods.current(npc_id=key, turn=16)  # exactly one half-life later
    assert view.valence == pytest.approx(0.2 + (1.0 - 0.2) * 0.5)
    assert view.last_updated_turn == 10
    assert store.find_one("moods") == stored_before  # computed, never persisted


def test_mood_spike_does_not_move_the_long_term_relationship(store, config):
    """Spec §3.7 failure mode (b): momentary anger must not overwrite trust."""
    ledger = RelationshipLedger(store, config)
    moods = MoodTracker(store, config)
    npc_id = _seed_npc(store)
    key = f"npc:{npc_id}"
    ledger.append_delta(npc_id=key, player_id="player", turn=1, delta=12,
                        reason="kept the promise", category="trust")

    before = ledger.currents(npc_id=key, player_id="player", turn=5)
    describe_before = ledger.describe(npc_id=key, player_id="player", turn=5)
    moods.apply(npc_id=key, valence_delta=-0.9, arousal_delta=0.8, turn=5,
                cause="the player insulted her cooking")
    after = ledger.currents(npc_id=key, player_id="player", turn=5)

    assert after == before  # the spike moved nothing durable
    assert store.count("relationship_ledger") == 1
    assert ledger.describe(npc_id=key, player_id="player", turn=5) == describe_before

    # the moment is loud now...
    mood = moods.current(npc_id=key, turn=5)
    assert mood.valence == pytest.approx(-0.9)
    assert mood.arousal == pytest.approx(0.8)
    # ...and three half-lives later it is back near baseline, ledger untouched
    assert moods.current(npc_id=key, turn=5 + 18).valence == pytest.approx(-0.9 * 0.5 ** 3)
    residual = ledger.currents(npc_id=key, player_id="player", turn=23)
    assert residual["trust"] == pytest.approx(12 * math.exp(-0.01 * 22), abs=1e-6)  # slow class


def test_mood_half_life_is_configurable(store, config):
    config.memory.mood_half_life_turns = 2.0
    moods = MoodTracker(store, config)
    npc_id = _seed_npc(store)
    key = f"npc:{npc_id}"
    moods.apply(npc_id=key, valence_delta=1.0, arousal_delta=0.0, turn=0)
    assert moods.current(npc_id=key, turn=2).valence == pytest.approx(0.5)
    assert moods.current(npc_id=key, turn=4).valence == pytest.approx(0.25)
