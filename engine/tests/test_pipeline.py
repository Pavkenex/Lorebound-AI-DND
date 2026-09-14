"""Turn pipeline — intent, Pass A→D wiring, persistence, live/degraded Pass B (R7).

The pipeline is the only place that may write game state, so these tests pin the
whole lifecycle against a real ``Store`` (plus ``FakeStore`` for the scans): what
lands in ``turn_log`` / ``telemetry`` / ``chronicle`` / ``moods``, what the
narrator receives, and which contradictions Pass D refuses to ship.
"""
from __future__ import annotations

import json
import re

import pytest

from engine.config import EngineConfig
from engine.fixtures.demo_world import demo_world, seed_world
from engine.models import (
    AssembledPrompt,
    ChatResponse,
    CommitReport,
    Delta,
    Location,
    MoodState,
    ProposalSet,
    ProviderCaps,
    ProviderConfig,
    ToolCall,
    to_row,
)
from engine.pipeline import (
    LiveNarrator,
    Orchestrator,
    default_ruleset_text,
    narrator_from_adapter,
)
from engine.play import PlaySession, StubNarrator
from engine.providers.jsonproto import (
    PROPOSE_DELTAS_TOOL_NAME,
    build_json_protocol_prompt,
    delta_tool_schema,
)
from engine.store import Store

ENVELOPE = {
    "narration": "The yard keeps its own counsel.",
    "npc_dialogue": [{"npc_id": "npc:1", "name": "Marla Quist", "text": "Later."}],
    "deltas": [
        {"kind": "relationship", "target": "npc:1",
         "data": {"category": "trust", "delta": 5}, "reason": "kept the peace"},
        {"kind": "currency", "target": "", "data": {"amount": -2}, "reason": "bought oats"},
    ],
}


@pytest.fixture()
def store(tmp_path):
    s = Store(tmp_path / "campaign.db")
    seed_world(s, demo_world())
    try:
        yield s
    finally:
        s.close()


def build(store, script=(), **kwargs) -> Orchestrator:
    config = kwargs.pop("config", None) or EngineConfig(
        db_path=str(getattr(store, "_path", None) or getattr(store, "db_path", "lorebound")),
    )
    return Orchestrator(store, config, StubNarrator(script=list(script)), **kwargs)


def kill(store, name: str) -> None:
    row = store.find_one("npcs", {"name": name})
    store.update("npcs", int(row["id"]), {"alive": 0})


# --------------------------------------------------------------------------- #
# ruleset block
# --------------------------------------------------------------------------- #

def test_default_ruleset_text_pins_the_engine_contract() -> None:
    text = default_ruleset_text()
    assert text == default_ruleset_text()
    for clause in ("Code owns the game state", "final", "Second person, present tense",
                   "pinned fact", "propose_state_deltas", "fail-forward"):
        assert clause in text, clause
    # the ruleset is the static, cacheable block context.py injects first
    assert text.count("propose_state_deltas") == 1


# --------------------------------------------------------------------------- #
# intent classification
# --------------------------------------------------------------------------- #

@pytest.mark.parametrize(("text", "kind"), [
    ("/save", "meta"),
    ("\\quit", "meta"),
    ("who is Marla?", "dialogue"),
    ('"I am fine," she says.', "dialogue"),
    ("hello Marla", "dialogue"),
    ("I ask Marla about the shipment", "dialogue"),
    ("I tell Hob the gate is open", "dialogue"),
    ("I search the yard for boot prints", "action"),
    ("I climb the wall", "action"),
    ("I attack the guard", "action"),
    ("I sneak past the gatehouse", "action"),
    ("wait and listen", "action"),
    ("The morning is cold and the salt carts are late.", "exploration"),
    ("I give Marla the token", "exploration"),
    ("", "exploration"),
])
def test_classify_intent_rules(text: str, kind: str) -> None:
    assert Orchestrator(None).classify_intent(text).kind == kind


@pytest.mark.parametrize(("text", "skill"), [
    ("I search the yard for boot prints", "investigation"),
    ("I climb the gatehouse wall", "athletics"),
    ("I attack the guard", "attack"),
    ("I sneak across the yard", "stealth"),
    ("I lie to Hob", "deception"),
    ("I pick the lock", "lockpicking"),
])
def test_classify_intent_skill_hint(text: str, skill: str) -> None:
    assert Orchestrator(None).classify_intent(text).skill == skill


def test_classify_intent_resolves_npc_targets_through_the_store(store) -> None:
    orchestrator = build(store)
    assert orchestrator.classify_intent("I ask Marla about the shipment").target == "npc:1"
    assert orchestrator.classify_intent("hello Marla").target == "npc:1"
    assert orchestrator.classify_intent("I attack npc:2").target == "npc:2"
    assert orchestrator.classify_intent("I greet the tanner").target is None
    # a named prop is not an NPC: resolve.py may still roll, with no eligibility gate
    assert orchestrator.classify_intent("I attack the dog").target == "dog"


def test_classify_intent_finds_targets_without_a_store() -> None:
    assert Orchestrator(None).classify_intent("I attack npc:7").target == "npc:7"


def test_classify_intent_keeps_the_raw_text(store) -> None:
    intent = build(store).classify_intent("  I search the yard  ")
    assert intent.text == "I search the yard"


# --------------------------------------------------------------------------- #
# Pass A-D lifecycle
# --------------------------------------------------------------------------- #

def test_take_turn_writes_turn_log_telemetry_and_chronicle(store) -> None:
    orchestrator = build(store)
    result = orchestrator.take_turn(player_input="I search the yard for boot prints", turn=1)

    log = store.find_one("turn_log", {"turn": 1})
    assert log is not None
    assert log["actor"] == "player"
    assert log["raw_action"] == "I search the yard for boot prints"
    resolution = json.loads(log["mechanical_resolution"])
    assert resolution["kind"] == "check"
    assert resolution["label"] == "investigation check"
    assert resolution["verdict_line"] == result.mechanics.verdict_line
    assert resolution["band"] in {"success", "success_at_cost", "failure",
                                  "critical", "critical_failure"}
    assert resolution["roll"] == result.mechanics.check.roll
    assert log["narration_text"] == result.narration
    assert json.loads(log["state_deltas_applied"]) == []
    assert isinstance(log["created_at"], int) and log["created_at"] > 0

    telemetry = store.find_one("telemetry", {"turn": 1})
    notes = json.loads(telemetry["notes"])
    assert telemetry["provider"] == "stub" and telemetry["model"] == "stub"
    assert telemetry["prompt_tokens"] > 0 and telemetry["completion_tokens"] > 0
    assert json.loads(telemetry["budget_alloc"])["system"] > 0
    assert json.loads(telemetry["dropped"]) == []
    assert notes["intent"] == {"kind": "action", "skill": "investigation", "target": "yard"}
    assert notes["mechanics"]["kind"] == "check"
    assert notes["deltas"] == {"accepted": 0, "clamped": 0, "rejected": 0}
    assert notes["consistency"] == {"problems": 0, "regenerations": 0,
                                    "patched_sentences": 0, "patched_dialogue": 0}
    assert result.telemetry["id"] == telemetry["id"]
    assert result.telemetry["budget_alloc"]["scene"] >= 0

    chronicle = store.find_one("chronicle", {"turn_id": 1})
    assert chronicle["actor"] == "player"
    assert chronicle["action_summary"] == "I search the yard for boot prints"
    assert chronicle["mechanical_result"] == result.mechanics.check.band
    assert chronicle["verbatim_text"] == result.narration


def test_take_turn_runs_pass_a_before_pass_b(store) -> None:
    orchestrator = build(store)
    result = orchestrator.take_turn(player_input="I search the yard", turn=1)
    narrator = orchestrator.narrator
    prompt = narrator.calls[0]["prompt"]
    sections = dict(prompt.sections)

    assert result.mechanics.verdict_line in sections["mechanics"]
    assert f"Outcome: {result.mechanics.kind}" in sections["mechanics"]
    assert "The Salt Gate yard" in sections["scene"]
    assert "Marla Quist" in sections["scene"]
    # the static ruleset rides in the system role, not the user turn
    assert "Code owns the game state" in prompt.system
    assert "Code owns the game state" not in prompt.text
    assert narrator.calls[0]["retry_note"] is None


def test_take_turn_commits_narrator_deltas_through_the_validator(store) -> None:
    script = [ProposalSet(
        narration="Marla warms to you, and a few coins change hands.",
        npc_dialogue=[],
        deltas=[
            Delta(kind="relationship", target="npc:1",
                  data={"category": "trust", "delta": 12}, reason="you kept your word"),
            Delta(kind="currency", target="player", data={"amount": -2},
                  reason="oats"),
        ],
    )]
    result = build(store, script).take_turn(player_input="I greet Marla", turn=1)

    assert len(result.report.accepted) == 2
    assert result.report.rejected == []
    ledger = store.find_one("relationship_ledger", {"npc_id": "npc:1"})
    assert ledger is not None and ledger["delta"] == 12
    assert ledger["player_id"] == "player"
    character = store.find_one("characters", order_by="id")
    assert json.loads(character["stats"])["currency"] == 4
    log = store.find_one("turn_log", {"turn": 1})
    applied = json.loads(log["state_deltas_applied"])
    assert [entry["kind"] for entry in applied] == ["relationship", "currency"]


def test_take_turn_records_rejected_deltas_in_the_report(store) -> None:
    script = [ProposalSet(
        narration="You pay the broker far more than you have.",
        deltas=[Delta(kind="currency", target="player", data={"amount": -500},
                      reason="grand gesture")],
    )]
    result = build(store, script).take_turn(player_input="I pay the broker", turn=1)
    assert len(result.report.rejected) == 1
    assert "conservation" in result.report.rejected[0].note
    assert any("1 proposed delta(s) rejected" in line for line in result.system_lines)
    assert any("rejected" in line for line in result.system_lines)


def test_meta_turn_rolls_no_dice_and_narrates_freely(store) -> None:
    result = build(store).take_turn(player_input="/state", turn=1)
    assert result.mechanics.kind == "none"
    assert result.mechanics.check is None
    assert result.mechanics.label == "meta"
    assert result.report.applied == []


def test_stub_narration_stays_out_of_stats_speak(store) -> None:
    result = build(store).take_turn(player_input="I climb the gatehouse wall", turn=1)
    assert result.narration
    assert not re.search(r"\d", result.narration), result.narration
    assert "Marla Quist" in result.narration  # scene-derived, not mechanics-derived


def test_present_npcs_carry_mood_and_disposition(store) -> None:
    orchestrator = build(store)
    present = orchestrator.present_npcs(turn=1)
    assert [npc["name"] for npc in present] == ["Marla Quist"]
    assert present[0]["id"] == "npc:1"
    assert present[0]["disposition"] == pytest.approx(12.0)
    assert present[0]["mood"]["valence"] == pytest.approx(0.15)
    assert present[0]["personality"]["tone"] == "plain"


def test_mood_decay_is_persisted_for_present_npcs(store) -> None:
    store.insert("moods", to_row(MoodState(
        npc_id="npc:1", valence=1.0, arousal=0.0,
        baseline_valence=0.15, baseline_arousal=-0.15, last_updated_turn=0,
    )))
    build(store).take_turn(player_input="I wait", turn=2)
    row = store.find_one("moods", {"npc_id": "npc:1"})
    # half-life 6 turns: the resting value decays toward the personality baseline
    expected = 0.15 + (1.0 - 0.15) * 0.5 ** (2 / 6)
    assert row["valence"] == pytest.approx(expected, rel=1e-6)
    assert row["last_updated_turn"] == 2
    # an NPC who was never mood-affected gets no row invented for them
    assert store.find_one("moods", {"npc_id": "npc:2"}) is None


def test_scene_carries_location_when_the_slug_is_a_name_or_a_flag(store) -> None:
    orchestrator = build(store)
    row = orchestrator.location_row("yard")
    assert row is not None and row["name"] == "The Salt Gate yard"
    # name-slug and flags are both accepted conventions
    store.insert("locations", to_row(Location(
        name="The Rope Walk", description_static="", connections=[], flags={},
    )))
    assert orchestrator.location_row("the-rope-walk")["name"] == "The Rope Walk"


# --------------------------------------------------------------------------- #
# Pass D: the scans (unit level)
# --------------------------------------------------------------------------- #

def test_consistency_check_is_clean_on_a_clean_turn(store) -> None:
    result = build(store).take_turn(player_input="I wait", turn=1)
    clean, problems = build(store).consistency_check(
        turn=1, narration=result.narration, report=result.report,
    )
    assert clean is True and problems == []


def test_consistency_check_flags_a_dead_npc_speaking_in_the_narration(store) -> None:
    kill(store, "Marla Quist")
    orchestrator = build(store)
    clean, problems = orchestrator.consistency_check(
        turn=1,
        narration='You ask. "Not now," Marla Quist says, and turns away.',
        report=CommitReport(),
    )
    assert clean is False
    assert len(problems) == 1 and "Marla Quist is dead" in problems[0]


def test_consistency_check_flags_a_dead_npc_dialogue_entry(store) -> None:
    kill(store, "Marla Quist")
    orchestrator = build(store)
    clean, problems = orchestrator.consistency_check(
        turn=1, narration="The yard is quiet.",
        report=CommitReport(),
        dialogue=[{"npc_id": "npc:1", "name": "Marla Quist", "text": "Take it."}],
    )
    assert clean is False
    assert "speaks in this turn's dialogue" in problems[0]


def test_consistency_check_leaves_negated_mentions_alone(store) -> None:
    kill(store, "Marla Quist")
    orchestrator = build(store)
    clean, problems = orchestrator.consistency_check(
        turn=1,
        narration="Marla Quist does not answer, and the yard stays quiet. "
                  "She never replies, and nobody calls her name.",
        report=CommitReport(),
    )
    assert (clean, problems) == (True, [])
    # the same attribution in the affirmative IS the problem the guard sits on
    flagged, hits = orchestrator.consistency_check(
        turn=1, narration="Marla Quist answers the summons.", report=CommitReport(),
    )
    assert flagged is False and "Marla Quist is dead" in hits[0]


def test_consistency_check_flags_a_pinned_fact_flip(store) -> None:
    orchestrator = build(store)
    clean, problems = orchestrator.consistency_check(
        turn=1, narration="Marla's brother Dain is alive and well.",
        report=CommitReport(),
    )
    assert clean is False
    assert "pinned fact #1" in problems[0]
    assert "antonym" in problems[0]


def test_consistency_check_flags_restating_a_rejected_spend(store) -> None:
    orchestrator = build(store)
    rejection = orchestrator._validator_of().validate(
        [Delta(kind="currency", target="player", data={"amount": -50})], turn=1,
    )
    report = orchestrator._validator_of().commit(rejection, turn=1)
    assert report.rejected, "the spend must be refused for this test to mean anything"
    clean, problems = orchestrator.consistency_check(
        turn=1, narration="You pay 50 gold coins and the debt is settled.", report=report,
    )
    assert clean is False
    assert "restates a delta the validator rejected" in problems[0]


# --------------------------------------------------------------------------- #
# Pass B: live narrator (native tools + degraded JSON)
# --------------------------------------------------------------------------- #

class FakeAdapter:
    """Duck-typed NarratorAdapter: scripted responses, no wire."""

    name = "fake"

    def __init__(self, responses, *, native: bool = False) -> None:
        self.responses = list(responses)
        self.requests = []
        self._caps = ProviderCaps(native_tools=native)

    def capabilities(self) -> ProviderCaps:
        return self._caps

    def complete(self, request):
        self.requests.append(request)
        return self.responses.pop(0) if self.responses else ChatResponse(text="")


def live(adapter, model="test-model", **kwargs) -> LiveNarrator:
    config = EngineConfig()
    return LiveNarrator(adapter, ProviderConfig(name="fake", model=model), config, **kwargs)


def prompt_of(text: str = "story so far") -> AssembledPrompt:
    return AssembledPrompt(text=text, system="ruleset block", sections=[])


def test_live_narrator_uses_native_tool_calls() -> None:
    adapter = FakeAdapter(
        [ChatResponse(text="The yard keeps its counsel.",
                      tool_calls=[ToolCall(id="c1", name=PROPOSE_DELTAS_TOOL_NAME,
                                           arguments=ENVELOPE)])],
        native=True,
    )
    narrator = live(adapter)
    proposals = narrator.narrate(prompt=prompt_of(), turn=1)

    request = adapter.requests[0]
    assert request.tools == delta_tool_schema()
    assert request.model == "test-model" and request.max_tokens > 0
    # prose stays plain: the JSON protocol block is only for degraded models
    assert request.messages[1].content == "story so far"
    assert proposals.narration == ENVELOPE["narration"]
    assert [delta.kind for delta in proposals.deltas] == ["relationship", "currency"]
    assert narrator.stats()["native_tools"] is True
    assert narrator.provider_name == "fake" and narrator.model_name == "test-model"


def test_live_narrator_degrades_to_json_in_text() -> None:
    adapter = FakeAdapter([ChatResponse(text=json.dumps(ENVELOPE))], native=False)
    narrator = live(adapter)
    proposals = narrator.narrate(prompt=prompt_of(), turn=1)

    request = adapter.requests[0]
    assert request.tools is None
    assert build_json_protocol_prompt("story so far") in request.messages[1].content
    assert proposals.narration == ENVELOPE["narration"]
    assert [delta.kind for delta in proposals.deltas] == ["relationship", "currency"]


def test_live_narrator_regenerates_once_on_unparseable_prose() -> None:
    first_payload = {"narration": "The yard stays quiet.", "deltas": []}
    adapter = FakeAdapter([
        ChatResponse(text="I shall narrate in prose instead."),
        ChatResponse(text=json.dumps(first_payload)),
    ], native=False)
    narrator = live(adapter)
    proposals = narrator.narrate(prompt=prompt_of(), turn=1)

    assert len(adapter.requests) == 2
    assert "I shall narrate in prose instead." not in proposals.narration
    assert proposals.narration == first_payload["narration"]
    assert narrator.stats()["regenerations"] == 1
    note = adapter.requests[1].messages[-1].content
    assert "JSON" in note or "json" in note
    assert any("json:" in line for line in narrator.last_notes)


def test_live_narrator_falls_back_to_prose_when_regeneration_fails() -> None:
    adapter = FakeAdapter([
        ChatResponse(text="Still prose."), ChatResponse(text="Also prose."),
    ], native=False)
    narrator = live(adapter)
    proposals = narrator.narrate(prompt=prompt_of(), turn=1)
    assert len(adapter.requests) == 2
    assert proposals.narration == "Also prose."
    assert proposals.deltas == []
    assert narrator.stats()["parse_failures"] == 2
    assert any("using the reply as prose" in line for line in narrator.last_notes)


def test_live_narrator_keeps_prose_when_native_tools_are_ignored() -> None:
    adapter = FakeAdapter([ChatResponse(text="Prose, no tool call.")], native=True)
    narrator = live(adapter)
    proposals = narrator.narrate(prompt=prompt_of(), turn=1)
    assert proposals.narration == "Prose, no tool call."
    assert proposals.deltas == []
    assert any("narration-only" in line for line in narrator.last_notes)


def test_narrator_from_adapter_without_probing() -> None:
    adapter = FakeAdapter([ChatResponse(text=json.dumps(ENVELOPE))], native=False)
    narrator = narrator_from_adapter(
        adapter, ProviderConfig(name="fake", model="m"), probe=False,
    )
    assert isinstance(narrator, LiveNarrator)
    assert narrator.narrate(prompt=prompt_of(), turn=1).narration == ENVELOPE["narration"]


def test_play_session_builds_a_live_narrator_from_an_adapter(tmp_path) -> None:
    adapter = FakeAdapter([ChatResponse(text=json.dumps(ENVELOPE))], native=False)
    session = PlaySession.start(
        db_path=tmp_path / "live.db", adapter=adapter,
        provider=ProviderConfig(name="fake", model="m"), probe=False,
    )
    try:
        result = session.act("I greet Marla")
        assert result.narration == ENVELOPE["narration"]
        assert result.telemetry["provider"] == "fake"
        assert result.telemetry["model"] == "m"
    finally:
        session.close()
