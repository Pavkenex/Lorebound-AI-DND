"""Narrator schema/prompt/service/metering (t_ad5b30b4, t_6dbf2746, t_eff821f1, t_9f1f24aa)."""
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
