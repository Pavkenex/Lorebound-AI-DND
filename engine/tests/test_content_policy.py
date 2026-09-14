"""Content policy on the engine path (phase-2 P8, docs/INTEGRATION_PLAN.md §11).

The player's content boundaries ride every narrator prompt as their own system
line, appended AFTER the ruleset. The three properties this module pins:

* the cacheable ruleset prefix stays byte-identical (spec §4 priority #1);
* the directive is never truncated by the system ceiling and never dropped
  under budget pressure — it is policy, not context;
* an empty/absent policy changes nothing: the assembled prompt is byte-for-byte
  the pre-P8 one.

The directive string itself is opaque to the engine (the app renders it with
``narrator.prefs.ContentPrefs.describe_for_prompt()``); the fixtures below are
verbatim copies of that renderer's two shapes so a drift in either wording
still fails loudly in the backend suite.
"""
from __future__ import annotations

import json

import pytest
from test_context_doubles import (
    ChronicleDouble,
    FactDouble,
    MemoryDouble,
    NPCMemoryDouble,
    SagaDouble,
    chronicle_entry,
    npc_entry,
    npc_read,
    pinned_fact,
    saga_row,
    scene_dict,
)

from engine.config import EngineConfig
from engine.context import ContextAssembler
from engine.fixtures.demo_world import demo_world, seed_world
from engine.models import (
    ChatResponse,
    Intent,
    ProviderCaps,
    ProviderConfig,
    ToolCall,
)
from engine.pipeline import LiveNarrator, Orchestrator, default_ruleset_text
from engine.play import PlaySession, StubNarrator
from engine.providers.jsonproto import PROPOSE_DELTAS_TOOL_NAME
from engine.store import Store

RULESET = "Rules: narrate the outcome you are given."

#: The production ruleset as the assembler delivers it (``_system_text`` strips).
PROD_RULESET = default_ruleset_text().strip()

#: ``ContentPrefs().describe_for_prompt()`` — the default boundaries directive.
POLICY = (
    "Content boundaries — violence: standard fantasy violence; horror: "
    "standard fantasy horror; romance: standard romantic themes; language: "
    "occasional strong language permitted; no explicit sexual content."
)

#: ``ContentPrefs(nsfw=True).describe_for_prompt()`` — the opted-in directive.
NSFW_POLICY = (
    "Content boundaries — uncensored mode (the player has opted in): all "
    "content limits are lifted. Explicit sexual content between adults is "
    "permitted and fully described, graphic violence and strong language are "
    "permitted, and nothing fades to black. Hard exclusion that always stands: "
    "never sexual content involving minors."
)

NPC = "npc:1"


# --------------------------------------------------------------------------- #
# Harness
# --------------------------------------------------------------------------- #


def _assemble(scene: dict, *, window: int = 32000, memory: MemoryDouble | None = None):
    return ContextAssembler(None, EngineConfig(), components=memory or MemoryDouble()).assemble(
        turn=6,
        scene=scene,
        mechanical=None,
        intent=Intent(kind="action", text="I look around the yard"),
        context_window=window,
    )


def _fingerprint(prompt) -> str:
    """Everything the prompt carries, as one comparable blob."""
    return json.dumps(
        {
            "text": prompt.text,
            "system": prompt.system,
            "sections": prompt.sections,
            "accounting": prompt.accounting,
        },
        sort_keys=True,
        default=str,
    )


def _dropped(prompt) -> list[str]:
    return [entry["section"] for entry in prompt.accounting["dropped"]]


def _pressure(window: int) -> MemoryDouble:
    """Rich content with one entry per droppable section (> window room)."""
    return MemoryDouble(
        npc_memory=NPCMemoryDouble({NPC: [
            (npc_entry(statement="the player promised to find the shipment", entry_id=1), 0.9),
            (npc_entry(statement="a stale grievance about the fence", entry_id=2), 0.4),
            (npc_entry(statement="an unimportant observation", entry_id=3), 0.1),
        ]}),
        chronicle=ChronicleDouble([
            chronicle_entry(turn_id=4, action_summary="the player searched the yard"),
            chronicle_entry(turn_id=5, action_summary="the player found a locked crate"),
        ]),
        saga=SagaDouble([saga_row(text="The campaign opened in the yard.")]),
        facts=FactDouble([pinned_fact(statement="The player promised to find the shipment")]),
    )


@pytest.fixture()
def store(tmp_path):
    s = Store(tmp_path / "campaign.db")
    seed_world(s, demo_world())
    try:
        yield s
    finally:
        s.close()


class _CapturingAdapter:
    """Live adapter double: records every request, answers with a delta call."""

    name = "openai"

    def __init__(self) -> None:
        self.requests: list = []
        self.caps = ProviderCaps(native_tools=True)
        self.api_key = None

    def capabilities(self) -> ProviderCaps:
        return self.caps

    def secrets(self) -> tuple[str, ...]:
        return ()

    def complete(self, request) -> ChatResponse:
        self.requests.append(request)
        return ChatResponse(
            text="",
            tool_calls=[
                ToolCall(
                    name=PROPOSE_DELTAS_TOOL_NAME,
                    arguments={
                        "narration": "The yard keeps its own counsel.",
                        "npc_dialogue": [],
                        "deltas": [],
                    },
                )
            ],
        )


# --------------------------------------------------------------------------- #
# Assembly: its own system line, the ruleset prefix untouched
# --------------------------------------------------------------------------- #


def test_the_policy_is_its_own_system_line_right_after_the_ruleset() -> None:
    prompt = _assemble(scene_dict(ruleset=RULESET, content_policy=POLICY))
    # byte-level shape: ruleset, blank line, directive
    assert prompt.system == f"{RULESET}\n\n{POLICY}"
    assert prompt.system.startswith(RULESET)  # cacheable prefix, unchanged
    assert POLICY in prompt.system  # verbatim
    assert POLICY not in prompt.text  # a system line, not a dynamic section


def test_the_nsfw_directive_reaches_the_prompt_verbatim() -> None:
    prompt = _assemble(scene_dict(ruleset=RULESET, content_policy=NSFW_POLICY))
    assert NSFW_POLICY in prompt.system
    assert "never sexual content involving minors" in prompt.system


def test_no_policy_leaves_every_prompt_byte_identical() -> None:
    baseline = _fingerprint(_assemble(scene_dict(ruleset=RULESET)))
    # golden: the system line is exactly the ruleset
    assert _assemble(scene_dict(ruleset=RULESET)).system == RULESET

    for junk in ("", "   ", 42, {"nope": True}, ["x"]):
        scene = scene_dict(ruleset=RULESET)
        scene["content_policy"] = junk  # empty / whitespace / wrong type
        assert baseline == _fingerprint(_assemble(scene)), junk


def test_accounting_counts_the_policy_apart_from_the_cacheable_system_block() -> None:
    assembler = ContextAssembler(None, EngineConfig(), components=MemoryDouble())
    scene = scene_dict(ruleset=RULESET, content_policy=POLICY)
    prompt = assembler.assemble(
        turn=6, scene=scene, mechanical=None,
        intent=Intent(kind="action", text="I look around the yard"),
        context_window=32000,
    )
    sections = prompt.accounting["sections"]
    assert sections["content_policy"] == assembler.estimate_tokens(POLICY) > 0
    assert sections["system"] == assembler.estimate_tokens(RULESET)  # ruleset only
    assert prompt.accounting["budget"]["used"] == sum(sections.values())
    assert "content_policy" not in _dropped(prompt)
    assert prompt.accounting["cacheable"] == ["system"]


# --------------------------------------------------------------------------- #
# Budget pressure: never truncated, never dropped
# --------------------------------------------------------------------------- #


def test_the_policy_survives_a_squeezed_context_budget() -> None:
    prompt = _assemble(
        scene_dict(ruleset=RULESET, npcs=[npc_read(npc_id=NPC, name="Marla")],
                   content_policy=POLICY),
        window=120,
        memory=_pressure(120),
    )
    assert POLICY in prompt.system  # dropped sections do not touch policy
    assert "scene" in _dropped(prompt)  # the squeeze was real
    assert "content_policy" not in _dropped(prompt)


def test_the_system_ceiling_truncates_the_ruleset_but_never_the_policy() -> None:
    prompt = _assemble(
        scene_dict(ruleset="House rule: roll high. " * 200, content_policy=POLICY),
        window=1000,
    )
    head, _, tail = prompt.system.rpartition("\n\n")
    assert tail == POLICY  # the policy tail is intact...
    assert head.startswith("House rule:") and head.endswith("…")  # ...truncated ruleset
    assert "system" in _dropped(prompt) and "content_policy" not in _dropped(prompt)


def test_a_large_policy_is_carried_in_full_even_under_pressure() -> None:
    # Policy is small in practice; sized here to dominate the system allocation
    # to prove it is not part of the truncatable system block.
    policy = ("Content boundaries — " + ("explicit adult content permitted. " * 40)).strip()
    prompt = _assemble(
        scene_dict(ruleset=RULESET, npcs=[npc_read(npc_id=NPC, name="Marla")],
                   content_policy=policy),
        window=400,
        memory=_pressure(400),
    )
    assert prompt.system.startswith(RULESET)
    assert prompt.system.endswith(policy)


# --------------------------------------------------------------------------- #
# Threading: orchestrator -> session -> the model call
# --------------------------------------------------------------------------- #


def test_the_orchestrator_threads_the_policy_into_the_narrator_prompt(store) -> None:
    narrator = StubNarrator()
    orchestrator = Orchestrator(
        store, EngineConfig(), narrator,
        content_policy=POLICY,
    )
    orchestrator.take_turn(player_input="I look around the yard", turn=1)
    prompt = narrator.calls[-1]["prompt"]
    assert prompt.system == f"{PROD_RULESET}\n\n{POLICY}"
    # ...including on the regeneration round, which reuses the same prompt.
    assert all(POLICY in call["prompt"].system for call in narrator.calls)


def test_the_orchestrator_without_a_policy_assembles_the_plain_prompt(store) -> None:
    narrator = StubNarrator()
    Orchestrator(store, EngineConfig(), narrator).take_turn(
        player_input="I look around the yard", turn=1,
    )
    assert narrator.calls[-1]["prompt"].system == PROD_RULESET


def test_the_live_narrator_sends_the_policy_in_the_system_message(store) -> None:
    adapter = _CapturingAdapter()
    narrator = LiveNarrator(adapter, ProviderConfig(model="m"), EngineConfig())
    orchestrator = Orchestrator(store, EngineConfig(), narrator, content_policy=POLICY)
    orchestrator.take_turn(player_input="I look around the yard", turn=1)

    assert len(adapter.requests) == 1
    messages = adapter.requests[0].messages
    assert [message.role for message in messages] == ["system", "user"]
    assert messages[0].content.startswith(default_ruleset_text())
    assert messages[0].content.endswith(POLICY)


def test_play_session_start_carries_the_policy(tmp_path) -> None:
    session = PlaySession.start(db_path=tmp_path / "policy.db", content_policy=POLICY)
    try:
        session.act("I look around the yard")
        prompt = session.narrator.calls[-1]["prompt"]
        assert prompt.system == f"{PROD_RULESET}\n\n{POLICY}"
    finally:
        session.close()

    plain = PlaySession.start(db_path=tmp_path / "plain.db")
    try:
        plain.act("I look around the yard")
        assert plain.narrator.calls[-1]["prompt"].system == PROD_RULESET
    finally:
        plain.close()
