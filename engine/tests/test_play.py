"""PlaySession + StubNarrator + shipped fixtures (R7).

The stub narrator is the keyless path: it must be deterministic, honest about
mechanics (no invented numbers) and never a source of deltas. PlaySession must
be resumable — a second process on the same DB continues the same campaign.
"""
from __future__ import annotations

import json
from pathlib import Path

import pytest

from engine.config import EngineConfig
from engine.fixtures import DEMO_WORLD, FIXTURE_MODELS, demo_world, load_world, seed_world
from engine.models import ProposalSet, TurnResult
from engine.play import PlaySession, StubNarrator
from engine.resolve import SeededRng
from engine.store import Store

WORLD = {
    "world": [{"seed": "test-world", "day": 2, "hour": 9, "minute": 30,
               "active_scene_id": "hut"}],
    "locations": [{"name": "A Windy Hut", "description_static": "Planks and salt wind.",
                   "connections": [], "flags": {"slug": "hut"}}],
    "characters": [{"name": "Rell", "stats": {"hp": 9, "max_hp": 11, "currency": 3},
                    "inventory": [{"item_id": "rope", "qty": 1, "flags": {}}],
                    "location_id": "hut", "status_effects": [], "known_facts": [],
                    "journal": []}],
    "npcs": [{"name": "Old Sedge", "personality": {"traits": ["tired"], "tone": "low",
                                                    "speech_pattern": "slow",
                                                    "baseline_valence": -0.2,
                                                    "baseline_arousal": -0.3},
              "disposition_base": 5, "alive": True, "location_id": "hut"}],
    "leads": [{"title": "The tide clock", "stage": "unheard", "stage_history": [],
               "known_by": [], "related_npc_ids": [], "related_fact_ids": []}],
    "world_facts": [{"statement": "The hut's floor is older than the town.",
                     "pinned": True, "established_turn": 0, "source": "npc:1",
                     "tags": ["local"]}],
}


@pytest.fixture()
def session(tmp_path):
    s = PlaySession.start(db_path=tmp_path / "play.db")
    try:
        yield s
    finally:
        s.close()


# --------------------------------------------------------------------------- #
# fixtures
# --------------------------------------------------------------------------- #

def test_demo_world_is_a_real_campaign() -> None:
    world = demo_world()
    assert len(world["locations"]) == 4
    assert len(world["npcs"]) == 5
    assert len(world["leads"]) == 3
    assert len(world["world_facts"]) == 5
    assert [fact for fact in world["world_facts"] if fact["pinned"]].__len__() == 1
    assert len(world["characters"]) == 1

    player = world["characters"][0]
    assert player["stats"]["hp"] > 0 and player["stats"]["currency"] > 0
    assert player["inventory"], "the demo player must start with items"

    slugs = [loc["flags"]["slug"] for loc in world["locations"]]
    assert len(slugs) == len(set(slugs)) == 4
    for npc in world["npcs"]:
        personality = npc["personality"]
        for key in ("traits", "tone", "speech_pattern",
                    "baseline_valence", "baseline_arousal"):
            assert key in personality, (npc["name"], key)
        assert -1.0 <= personality["baseline_valence"] <= 1.0
        assert npc["location_id"] in slugs

    assert set(world) <= set(FIXTURE_MODELS)
    # the prompt's pinned-fact block only bites on facts with a state to flip
    pinned = [fact for fact in world["world_facts"] if fact["pinned"]][0]
    assert "dead" in pinned["statement"]


def test_demo_world_is_a_deep_copy_per_call() -> None:
    first = demo_world()
    first["npcs"][0]["name"] = "MUTATED"
    assert demo_world()["npcs"][0]["name"] != "MUTATED"
    assert DEMO_WORLD["npcs"][0]["name"] != "MUTATED"


def test_fixture_models_match_the_eval_harness() -> None:
    harness = pytest.importorskip("evals.harness")
    assert harness._FIXTURE_MODELS == FIXTURE_MODELS


def test_seed_world_rejects_unknown_tables_and_keys(tmp_path) -> None:
    store = Store(tmp_path / "a.db")
    try:
        with pytest.raises(ValueError, match="unknown table"):
            seed_world(store, {"ghosts": [{}]})
        with pytest.raises(ValueError, match="unknown field"):
            seed_world(store, {"npcs": [{"name": "X", "disposition": 3}]})
        with pytest.raises(ValueError, match="unknown field"):
            seed_world(store, {"moods": [{"npc_id": "npc:1", "mood": 0.5}]})
        # every known table (incl. telemetry) is writable through the encoder
        counts = seed_world(store, {"moods": [{
            "npc_id": "npc:1", "valence": 0.0, "arousal": 0.0,
            "baseline_valence": 0.0, "baseline_arousal": 0.0, "last_updated_turn": 0,
        }]})
        assert counts == {"moods": 1}
    finally:
        store.close()


def test_seed_world_returns_counts_and_tolerates_refs(tmp_path) -> None:
    world = {
        "locations": [{"name": "The Yard", "description_static": "dirt",
                       "connections": [], "flags": {}}],
        "npcs": [{"name": "Nia", "personality": {}, "location_id": "the-yard",
                  "ref": "nia"}],
    }
    store = Store(tmp_path / "b.db")
    try:
        counts = seed_world(store, world)
        assert counts == {"locations": 1, "npcs": 1}
        assert store.count("npcs") == 1
    finally:
        store.close()


def test_load_world_resolves_demo_json_python_and_mappings(tmp_path) -> None:
    assert load_world() == demo_world()
    assert load_world("demo") == demo_world()

    as_json = tmp_path / "world.json"
    as_json.write_text(json.dumps(WORLD))
    assert load_world(str(as_json)) == WORLD

    as_py = tmp_path / "world.py"
    as_py.write_text("WORLD = " + repr(WORLD) + "\n")
    assert load_world(str(as_py)) == WORLD

    assert load_world(WORLD) == WORLD
    with pytest.raises(ValueError, match="not found"):
        load_world(str(tmp_path / "missing.json"))


# --------------------------------------------------------------------------- #
# StubNarrator
# --------------------------------------------------------------------------- #

def test_stub_narrator_is_deterministic_and_keyless(session) -> None:
    first = session.act("I search the yard for boot prints")
    second = session.act("I search the yard for boot prints")

    assert isinstance(first, TurnResult)
    assert not first.system_lines
    assert first.npc_dialogue == []
    assert "stub" in first.telemetry["provider"]

    replay = PlaySession.start(
        db_path=Path(session.store._path).with_name("replay.db"),
    )
    try:
        replayed = replay.act("I search the yard for boot prints")
    finally:
        replay.close()
    # same world seed + same turn => same dice and the same deterministic prose
    assert replayed.narration == first.narration
    assert replayed.mechanics.verdict_line == first.mechanics.verdict_line
    # the shared RNG advances per turn, so later turns roll fresh dice
    assert second.turn == 2
    assert second.mechanics.verdict_line != first.mechanics.verdict_line or \
        second.mechanics.check.roll != first.mechanics.check.roll


def test_stub_narrator_renders_the_scene_not_the_dice(session) -> None:
    narration = session.act("I wait").narration
    assert "Marla Quist" in narration
    assert "The Salt Gate yard" in narration
    assert not any(char.isdigit() for char in narration)


def test_stub_narrator_consumes_a_scripted_envelope(session) -> None:
    session.narrator.script.append(ProposalSet(
        narration="You lift the latch and the gate gives.",
        npc_dialogue=[{"npc_id": "npc:2", "name": "Hob Fen", "text": "Toll's paid."}],
    ))
    result = session.act("I try the gate latch")
    assert result.narration == "You lift the latch and the gate gives."
    assert result.npc_dialogue[0]["text"] == "Toll's paid."
    assert session.narrator.calls  # the script was consumed, not ignored
    assert session.narrator.script == []


def test_stub_narrator_accepts_mapping_scripts_through_jsonproto() -> None:
    stub = StubNarrator(script=[{
        "narration": "The clerk shrugs.",
        "npc_dialogue": [{"name": "Hob Fen", "text": "Rules are rules."}],
        "deltas": [{"kind": "mood", "target": "npc:2",
                    "data": {"valence_delta": 0.1}, "reason": "you were polite"},
                   {"kind": "sorcery", "target": "npc:2", "data": {},
                    "reason": "not a real kind"}],
    }])
    proposals = stub.narrate(prompt=None, turn=1)
    assert [delta.kind for delta in proposals.deltas] == ["mood"]
    assert any("sorcery" in note for note in stub.last_notes)


def test_play_session_resumes_the_same_campaign(tmp_path) -> None:
    db = tmp_path / "resume.db"
    first = PlaySession.start(db_path=db)
    first.act("I wait")
    first.act("I wait")
    assert first.turn == 3, "session.turn is the next turn to play"
    first.close()

    second = PlaySession.start(db_path=db)
    try:
        assert second.turn == 3, "a reopened campaign must not restart the clock"
        result = second.act("I wait")
        assert result.turn == 3
        assert second.store.count("world") == 1, "reopening must not re-seed the world"
    finally:
        second.close()


def test_state_view_snapshots_the_player(session) -> None:
    session.act("I wait")
    view = session.state_view()

    assert view["turn"] == 1 and view["next_turn"] == 2
    assert view["location"]["id"] == "yard"
    assert view["location"]["name"] == "The Salt Gate yard"
    assert view["hp"] == 11 and view["max_hp"] == 11 and view["currency"] == 6
    assert [item["item_id"] for item in view["inventory"]] == ["coil of rope", "gate token"]
    assert [npc["name"] for npc in view["present_npcs"]] == ["Marla Quist"]
    assert view["present_npcs"][0]["disposition"] == 12.0
    assert view["leads"] == [
        {"id": 1, "title": "The missing salt shipment", "stage": "accepted"},
        {"id": 2, "title": "The gatehouse ledger", "stage": "rumored"},
        {"id": 3, "title": "What the well keeps", "stage": "unheard"},
    ]
    assert view["pinned_facts"] == ["Marla's brother Dain is dead — drowned at the "
                                    "Salt Gate three winters ago."]
    # the snapshot is JSON-encodable for the CLI
    json.dumps(view)


def test_session_plays_a_custom_world(tmp_path) -> None:
    session = PlaySession.start(db_path=tmp_path / "custom.db", world=WORLD)
    try:
        assert session.store.find_one("world", order_by="id")["seed"] == "test-world"
        result = session.act("I look around the hut")
        assert result.narration
        view = session.state_view()
        assert view["location"]["name"] == "A Windy Hut"
        assert [npc["name"] for npc in view["present_npcs"]] == ["Old Sedge"]
        assert view["leads"][0]["stage"] == "unheard"
    finally:
        session.close()


def test_session_honors_an_explicit_rng_seed(tmp_path) -> None:
    a = PlaySession.start(db_path=tmp_path / "a.db", world=WORLD, rng=SeededRng(7))
    b = PlaySession.start(db_path=tmp_path / "b.db", world=WORLD, rng=SeededRng(7))
    try:
        assert a.act("I climb the wall").mechanics.verdict_line == \
            b.act("I climb the wall").mechanics.verdict_line
    finally:
        a.close()
        b.close()


def test_ten_turn_stub_session_is_deterministic(tmp_path) -> None:
    """The card's 10+ turn offline run: same world, same inputs, same fiction."""
    inputs = [
        "I search the yard for boot prints",
        "I ask Marla about the shipment",
        "I climb the gatehouse wall",
        "I walk to the salt market",
        "hello Tam",
        "I sneak past the broker",
        "I wait and listen",
        "I lie to Hob about the toll",
        "I pick the gatehouse lock",
        "The morning is cold and the carts are late.",
        "I attack Grunn",
    ]
    first = PlaySession.start(db_path=tmp_path / "a.db")
    second = PlaySession.start(db_path=tmp_path / "b.db")
    try:
        run_a = [first.act(text).narration for text in inputs]
        run_b = [second.act(text).narration for text in inputs]

        assert run_a == run_b
        assert len(run_a) == 11
        assert all(narration.strip() for narration in run_a)
        assert first.turn == 12

        # one of each durable row per turn: the whole run is reconstructible
        assert first.store.count("turn_log") == 11
        assert first.store.count("telemetry") == 11
        assert first.store.count("chronicle") == 11

        # the stub invents no state: no deltas were applied and no rows appeared
        assert first.store.count("relationship_ledger") == 0
        assert first.store.count("moods") == 0

        view = first.state_view()
        assert view["turn"] == 11 and view["next_turn"] == 12
        assert view["location"]["id"] == "yard"
    finally:
        first.close()
        second.close()


def test_ten_turn_session_records_a_replayable_transcript(tmp_path) -> None:
    session = PlaySession.start(db_path=tmp_path / "t.db")
    try:
        for text in ("I search the yard", "I wait", "I greet Marla"):
            session.act(text)
        rows = session.store.find("turn_log", order_by="turn")
        assert [(row["turn"], row["raw_action"]) for row in rows] == [
            (1, "I search the yard"), (2, "I wait"), (3, "I greet Marla"),
        ]
        for row in rows:
            assert row["narration_text"].strip()
            resolution = json.loads(row["mechanical_resolution"])
            assert resolution["kind"] in {"check", "none"}
    finally:
        session.close()


def test_session_can_skip_seeding_for_tests(tmp_path) -> None:
    session = PlaySession.start(db_path=tmp_path / "empty.db", world={}, auto_seed=False)
    try:
        assert session.store.count("world") == 0
    finally:
        session.close()


def test_play_session_close_is_idempotent(session) -> None:
    session.close()
    session.close()


def test_engine_config_defaults_are_used_when_none_given(tmp_path) -> None:
    session = PlaySession.start(db_path=tmp_path / "cfg.db", config=EngineConfig())
    try:
        assert session.config.player_id == "player"
    finally:
        session.close()
