"""Narrator budget + voice: full replies fit, ceiling hits are flagged.

A Standard reply (prose + dialogue + suggestions + proposals as JSON) needs
~900 tokens; the old 600-token default cut replies mid-JSON and the salvage
paths served the fragment — the "mumbling". The budget is now explicit and
a ceiling hit is logged on the bundle instead of reaching the player.
"""
from __future__ import annotations

import json

from app.modules.ai.providers import StubProvider
from app.modules.ai.roles import Role, build_role_prompt
from app.modules.narrator.prompts import PromptContext
from app.modules.narrator.service import NARRATOR_MAX_TOKENS, narrate


def test_narrate_requests_a_full_reply_budget():
    prov = StubProvider()
    narrate(PromptContext(player_action="I look around."), provider=prov)
    assert prov.calls and prov.calls[0]["max_tokens"] == NARRATOR_MAX_TOKENS
    assert NARRATOR_MAX_TOKENS >= 900  # prose + dialogue + schema needs room


def test_narrator_role_prompt_directs_the_voice():
    prompt = build_role_prompt(Role.NARRATOR.value)
    assert "sensory" in prompt  # concrete detail over summary
    assert "NEVER invent" in prompt  # the old guardrails survived


def test_short_reply_marks_no_ceiling_hit():
    prov = StubProvider(canned={"narrator": json.dumps({
        "narration": "Rain needles the shutters.",
        "npc_dialogue": [], "suggested_actions": [],
        "proposed_events": [], "proposed_lead_changes": [],
    })})
    out, bundle = narrate(PromptContext(player_action="I listen."), provider=prov)
    assert out.narration == "Rain needles the shutters."
    assert bundle.ceiling_hit is False


def test_ceiling_hit_flagged_when_completion_fills_the_budget():
    # ~1250 completion tokens: the reply ran into the ceiling and was
    # probably cut mid-JSON — the bundle says so, the player never sees how.
    long_prose = "The hearth throws long shadows. " * 150
    prov = StubProvider(canned={"narrator": json.dumps({
        "narration": long_prose,
        "npc_dialogue": [], "suggested_actions": [],
        "proposed_events": [], "proposed_lead_changes": [],
    })})
    _out, bundle = narrate(PromptContext(player_action="I look around."),
                           provider=prov, campaign_id="budget1")
    assert bundle.ceiling_hit is True
