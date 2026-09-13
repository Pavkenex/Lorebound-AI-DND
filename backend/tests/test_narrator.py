"""Narrator schema/prompt/service/metering (t_ad5b30b4, t_6dbf2746, t_eff821f1, t_9f1f24aa)."""
import json

from app.modules.ai.metering import CALLS_PER_ACTION, MeterRegistry
from app.modules.ai.providers import StubProvider
from app.modules.ai.roles import build_role_prompt
from app.modules.narrator.prompts import (
    MAX_RETRIEVED_EVENTS,
    PromptContext,
    assemble_prompt,
)
from app.modules.narrator.schemas import Length, NarratorOutput, ack
from app.modules.narrator.service import narrate, render_prose, stream_narration


def test_output_schema_shape():
    out = NarratorOutput(narration="You enter the inn. " * 40)
    assert out.narration and out.npc_dialogue == [] and out.suggested_actions == []
    assert out.proposed_events == [] and out.proposed_lead_changes == []
    assert out.length == Length.STANDARD.value


def test_length_targets_default_100_250():
    from app.modules.narrator.schemas import WORD_TARGETS
    assert WORD_TARGETS["Standard"] == (100, 250)
    ok = NarratorOutput(narration="word " * 150)
    assert ok.within_target()
    short = NarratorOutput(narration="too short")
    assert not short.within_target()
    concise = NarratorOutput(narration="word " * 60, length="Concise")
    assert concise.within_target()


def test_ack_immediate():
    a = ack("Inspect surroundings")
    assert a["type"] == "ack" and "Inspect" in a["message"]


def test_prompt_assembly_uses_all_sources_no_transcript():
    import inspect

    from app.modules.narrator import prompts as pm
    src = inspect.getsource(pm)
    assert "transcript" in src.lower()  # only to forbid it...
    assert "NEVER" in src or "never" in src
    sig = inspect.signature(assemble_prompt)
    assert "transcript" not in sig.parameters  # ...no transcript param by design

    ctx = PromptContext(
        campaign_tone="Grim", location="Lantern Inn", scene="A quiet night.",
        player_character={"name": "Aric"}, npcs=[{"name": "Marla", "note": "innkeeper"}],
        world_facts=["Marla runs the Lantern Inn"], leads=[{"title": "Missing Travelers"}],
        recent_events=[{"kind": "PLAYER_ACTION", "payload": {"text": "hi"}}] * 20,
        player_action="I greet Marla.", mechanical_result={"outcome": "Success"},
        length="Standard",
    )
    bundle = assemble_prompt(ctx, role_system=build_role_prompt("narrator"))
    for needle in ("Grim", "Lantern Inn", "Aric", "Marla", "Missing Travelers",
                   "I greet Marla.", "Success", "NarratorOutput"):
        assert needle in bundle.user, needle
    assert bundle.retrieved_counts["events"] <= MAX_RETRIEVED_EVENTS  # capped retrieval


def test_render_prose_prefers_json_narration_and_trims():
    assert render_prose('{"narration": "Hello there."}') == "Hello there."
    long_text = "word " * 500
    assert len(render_prose(long_text).split()) <= 250


def test_render_prose_tolerates_fences_and_commentary():
    fenced = 'Here is the JSON:\n```json\n{"narration": "The rain keeps its counsel."}\n```\nDone.'
    assert render_prose(fenced) == "The rain keeps its counsel."
    prefixed = 'Sure! {"narration": "The hall empties slowly."} — nothing more.'
    assert render_prose(prefixed) == "The hall empties slowly."


def test_render_prose_never_leaks_json_scaffolding():
    truncated = ('{"narration": "The lantern gutters twice.", "npc_dialogue": '
                 '[{"speaker": "Marla", "line": "Hm."}]')
    assert render_prose(truncated) == "The lantern gutters twice."
    garbage = '{"narration": , "npc_dialogue": [}'
    out = render_prose(garbage)
    assert "npc_dialogue" not in out and "{" not in out and out  # a clean note instead


def test_parse_narrator_payload_normalizes_dialogue_and_suggestions():
    import json as _json

    from app.modules.narrator.service import parse_narrator_payload
    text = _json.dumps({
        "narration": "You set your tankard down.",
        "npc_dialogue": [{"speaker": "Marla", "line": "Private, is it?"},
                         {"npc": "Borin", "line": "Aye."}],
        "suggested_actions": ["Hold her gaze.", {"label": "Leave", "command": "I leave."}],
    })
    payload = parse_narrator_payload(f"Commentary first.\n```json\n{text}\n```")
    assert payload.parsed and payload.narration == "You set your tankard down."
    assert payload.dialogue == [{"npc": "Marla", "line": "Private, is it?"},
                                {"npc": "Borin", "line": "Aye."}]
    assert payload.suggestions == [{"label": "Hold her gaze.", "command": "Hold her gaze."},
                                   {"label": "Leave", "command": "I leave."}]


def test_parse_narrator_payload_salvages_truncated_fragment():
    from app.modules.narrator.service import parse_narrator_payload
    fragment = ('"narration": "The lantern gutters. Marla counts her cups twice.", '
                '"npc_dialogue": [ { "speaker": "Borin", "line": "I did not hear it here." }')
    payload = parse_narrator_payload(fragment)
    assert payload.parsed
    assert payload.narration == "The lantern gutters. Marla counts her cups twice."
    assert payload.dialogue == [{"npc": "Borin", "line": "I did not hear it here."}]


def test_narrate_single_call_metered():
    meter = MeterRegistry()
    prov = StubProvider()
    out, bundle = narrate(PromptContext(player_action="hi"), provider=prov,
                          meter=meter, campaign_id="m1")
    assert isinstance(out, NarratorOutput) and bundle.retrieved_counts is not None
    rep = meter.report("m1")
    assert rep["primary_calls"] == 1 and rep["within_budget"]


def test_metering_budget_norm_one_action_one_call():
    assert CALLS_PER_ACTION == 1
    meter = MeterRegistry()
    m = meter.for_campaign("b1")
    m.record_action()
    meter.record("b1", "narrator", 100, 50)
    rep = meter.report("b1")
    assert rep["primary_per_action"] == 1.0 and rep["within_budget"]
    meter.record("b1", "narrator", 100, 50)
    assert meter.report("b1")["within_budget"] is False  # over budget flagged
    assert meter.report("b1")["by_role"]["narrator"] == 2


def test_streaming_roundtrip():
    out = NarratorOutput(narration=" ".join(["word"] * 55))
    chunks = list(stream_narration(out, chunk_words=20))
    assert len(chunks) == 3 and " ".join(chunks) == out.narration


def test_stub_provider_deterministic():
    p = StubProvider()
    a = p.generate("prompt here", role="narrator")
    b = p.generate("prompt here", role="narrator")
    assert a.text == b.text and a.model == "stub-deterministic"


def test_dialogue_quoted_in_narration_is_not_rendered_twice():
    """Reported doubling: the closing quote also arrived as a speech block.

    The prose keeps the quoted line; the echoed npc_dialogue entry is dropped
    so the player reads each spoken line exactly once.
    """
    prose = (
        "Marla works your shoulders with practiced, unsentimental hands, finding "
        "knots you didn't know you'd earned and pressing them out one by one. When "
        "she finally steps back, your muscles feel like they belong to a man who "
        "hasn't been sleeping on the ground. 'That'll be a few coppers,' she says, "
        "wiping her hands on her apron, 'and you'll want a real bed after that, "
        "not the bench.'"
    )
    echoed = "There. You'll want a real bed after that, not the bench."
    distinct = "Sit. You carry your tension like a pack mule. Borin — the oil, please."
    prov = StubProvider(canned={"narrator": json.dumps({
        "narration": prose,
        "npc_dialogue": [{"npc": "Marla", "line": distinct},
                         {"npc": "Marla", "line": echoed}],
    })})
    out, _ = narrate(PromptContext(player_action="I ask for a shoulder rub."),
                     provider=prov)
    assert out.narration == prose
    assert [d.line for d in out.npc_dialogue] == [distinct]


def test_distinct_dialogue_survives_the_echo_guard():
    prov = StubProvider(canned={"narrator": json.dumps({
        "narration": "The rain keeps its counsel. Marla counts her cups twice.",
        "npc_dialogue": [{"npc": "Marla",
                          "line": "You'll find no answers in my ledger."}],
    })})
    out, _ = narrate(PromptContext(player_action="I ask about the road."),
                     provider=prov)
    assert [d.line for d in out.npc_dialogue] == ["You'll find no answers in my ledger."]


def test_short_lines_are_never_deduped():
    """Exclamations like 'Sit.' or 'Aye.' must survive even if the words
    appear somewhere in the prose — the guard only targets whole sentences."""
    prov = StubProvider(canned={"narrator": json.dumps({
        "narration": "Sit, she says, and the fire settles. Aye, answers Borin.",
        "npc_dialogue": [{"npc": "Marla", "line": "Sit."},
                         {"npc": "Borin", "line": "Aye."}],
    })})
    out, _ = narrate(PromptContext(player_action="I take a seat."), provider=prov)
    assert [d.line for d in out.npc_dialogue] == ["Sit.", "Aye."]


def test_duplicate_dialogue_entries_collapse_to_one():
    line = "The two travelers walked into that rain three nights back."
    prov = StubProvider(canned={"narrator": json.dumps({
        "narration": "She wipes the same cup twice before she answers.",
        "npc_dialogue": [{"npc": "Marla", "line": line},
                         {"npc": "Marla", "line": line}],
    })})
    out, _ = narrate(PromptContext(player_action="I press her."), provider=prov)
    assert [d.line for d in out.npc_dialogue] == [line]
