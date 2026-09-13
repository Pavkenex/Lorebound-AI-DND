"""Dynamic narrator prompt assembly (t_6dbf2746, GDD §25, §103).

Assembled per request from: system rules, campaign tone, current location,
current scene, player character, relevant NPCs, relevant world facts,
relevant leads, recent events, player action, mechanical result, output
schema. The full conversation transcript is NEVER appended — only retrieved
context. This module therefore takes no transcript argument by design.
"""
from __future__ import annotations

from typing import Any

from pydantic import BaseModel, Field

from app.modules.narrator.prefs import ContentPrefs

#: Hard cap on retrieved events per prompt (retrieved context only, §103).
MAX_RETRIEVED_EVENTS = 8
#: Safety cap on facts/NPCs to keep prompts small (GDD §105: no unnecessary calls).
MAX_FACTS = 10
MAX_NPCS = 5


class PromptContext(BaseModel):
    system_rules: str = "Resolve fairly. Describe only established facts plus the given mechanical result."
    campaign_tone: str = "Grounded fantasy"
    location: str = "Unknown"
    scene: str = ""
    player_character: dict[str, Any] = Field(default_factory=dict)
    npcs: list[dict[str, Any]] = Field(default_factory=list)
    world_facts: list[str] = Field(default_factory=list)
    leads: list[dict[str, Any]] = Field(default_factory=list)
    recent_events: list[dict[str, Any]] = Field(default_factory=list)
    player_action: str = ""
    mechanical_result: dict[str, Any] = Field(default_factory=dict)
    output_schema: str = (
        "Return ONLY the JSON object NarratorOutput: {narration, npc_dialogue[], "
        "suggested_actions[], proposed_events[], proposed_lead_changes[]} — "
        "raw JSON, no code fences, no commentary before or after. "
        "npc_dialogue entries: {\"npc\": \"Name\", \"line\": \"…\"}. "
        "suggested_actions entries: {\"label\": \"…\", \"command\": \"…\"}."
    )
    length: str = "Standard"


class PromptBundle(BaseModel):
    system: str
    user: str
    retrieved_counts: dict[str, int] = Field(default_factory=dict)


def assemble_prompt(
    ctx: PromptContext,
    role_system: str = "",
    prefs: ContentPrefs | None = None,
) -> PromptBundle:
    """Build the narrator prompt from retrieved context slices only."""
    npcs = ctx.npcs[:MAX_NPCS]
    facts = ctx.world_facts[:MAX_FACTS]
    events = ctx.recent_events[-MAX_RETRIEVED_EVENTS:]

    npc_block = "\n".join(f"- {n.get('name', '?')}: {n.get('note', '')}" for n in npcs) or "- (none present)"
    fact_block = "\n".join(f"- {f}" for f in facts) or "- (no established facts)"
    lead_block = "\n".join(f"- {l.get('title', '?')}: {l.get('status', '')}" for l in ctx.leads) or "- (no active leads)"
    event_block = "\n".join(f"- {e.get('kind', '?')}: {e.get('payload', e)}" for e in events) or "- (no recent events)"
    pc = ctx.player_character
    pc_block = f"{pc.get('name', 'the hero')} — {pc.get('description', 'an adventurer')}" if pc else "an adventurer"

    system = role_system or "You are the Narrator."
    boundaries = (prefs or ContentPrefs()).describe_for_prompt()
    user = (
        f"[System rules]\n{ctx.system_rules}\n\n"
        f"[Content boundaries]\n{boundaries}\n\n"
        f"[Campaign tone]\n{ctx.campaign_tone}\n\n"
        f"[Location]\n{ctx.location}\n\n"
        f"[Scene]\n{ctx.scene}\n\n"
        f"[Player character]\n{pc_block}\n\n"
        f"[NPCs present]\n{npc_block}\n\n"
        f"[Established world facts]\n{fact_block}\n\n"
        f"[Active leads]\n{lead_block}\n\n"
        f"[Recent events (retrieved, newest last)]\n{event_block}\n\n"
        f"[Player action]\n{ctx.player_action}\n\n"
        f"[Mechanical result]\n{ctx.mechanical_result}\n\n"
        f"[Length]\n{ctx.length}: "
        + {"Concise": "40-80 words.", "Standard": "100-250 words.",
           "Detailed": "250-450 words."}.get(ctx.length, "100-250 words.")
        + f"\n\n[Output schema]\n{ctx.output_schema}"
    )
    return PromptBundle(system=system, user=user, retrieved_counts={
        "npcs": len(npcs), "facts": len(facts), "events": len(events),
        "leads": len(ctx.leads),
    })
