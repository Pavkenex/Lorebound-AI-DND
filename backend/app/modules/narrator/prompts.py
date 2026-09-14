"""Dynamic narrator prompt assembly (t_6dbf2746, GDD §25, §103).

Assembled per request from: system rules, campaign tone, current location,
current scene, player character, relevant NPCs, relevant world facts,
relevant leads, the recent chronicle tail, recent events, player action,
mechanical result, output schema. The full conversation transcript is NEVER
appended — only retrieved context slices (the chronicle tail is a bounded
slice of it). This module therefore takes no transcript argument by design.
"""
from __future__ import annotations

from typing import Any

from pydantic import BaseModel, Field

from app.modules.narrator.prefs import ContentPrefs
from app.modules.npc.mood import surfaced_mood

#: Hard cap on retrieved events per prompt (retrieved context only, §103).
MAX_RETRIEVED_EVENTS = 8
#: Safety cap on facts/NPCs to keep prompts small (GDD §105: no unnecessary calls).
MAX_FACTS = 10
MAX_NPCS = 5
#: Memories about the player rendered per NPC in the prompt (strongest first).
MAX_NPC_MEMORIES = 4
#: Recent chronicle entries (narration/dialogue/notices) that ride the prompt.
#: Continuity lives here: without this tail the model re-derives the scene
#: every turn — the player sits down and the NPC invites them to sit again.
#: Sized so a full scene (~a dozen beats) stays in view; the saga digest
#: behind it already covers everything older.
MAX_RETRIEVED_CHRONICLE = 12
#: One chronicle line longer than this is elided head+tail (the closing beat
#: of a narration matters as much as its opening).
CHRONICLE_LINE_LIMIT = 800


class PromptContext(BaseModel):
    system_rules: str = "Resolve fairly. Describe only established facts plus the given mechanical result."
    campaign_tone: str = "Grounded fantasy"
    location: str = "Unknown"
    scene: str = ""
    player_character: dict[str, Any] = Field(default_factory=dict)
    npcs: list[dict[str, Any]] = Field(default_factory=list)
    world_facts: list[str] = Field(default_factory=list)
    leads: list[dict[str, Any]] = Field(default_factory=list)
    #: The tail of the player-visible chronicle (newest last): what the player
    #: has already been told — action echoes, prose, spoken lines, notices.
    chronicle: list[dict[str, Any]] = Field(default_factory=list)
    #: Rolling saga digest (suggestion #4): the shape of the whole tale so
    #: far in ~180 words, refreshed at checkpoint beats. The chronicle tail
    #: is the last beats verbatim; this is the memory of act one at act ten.
    saga: str = ""
    recent_events: list[dict[str, Any]] = Field(default_factory=list)
    player_action: str = ""
    #: The beat this turn resolved (P14): its machine id, the code-owned facts
    #: its prose must convey, and who speaks in it. Empty on the free-text
    #: pipeline, which has no pre-established outcome — there the *world* is
    #: the promise and the [Mechanical result] is the verdict.
    beat_event: str = ""
    beat_facts: list[str] = Field(default_factory=list)
    beat_speakers: list[str] = Field(default_factory=list)
    beat_direction: str = ""
    mechanical_result: dict[str, Any] = Field(default_factory=dict)
    output_schema: str = (
        "Return ONLY the JSON object NarratorOutput: {narration, npc_dialogue[], "
        "suggested_actions[], proposed_events[], proposed_lead_changes[]} — "
        "raw JSON, no code fences, no commentary before or after. "
        "npc_dialogue entries: {\"npc\": \"Name\", \"line\": \"…\"}. "
        "Every spoken line must appear exactly once in the whole response — "
        "either woven into the narration or listed in npc_dialogue, never both. "
        "suggested_actions entries: {\"label\": \"…\", \"command\": \"…\"}."
    )
    length: str = "Standard"


class PromptBundle(BaseModel):
    system: str
    user: str
    retrieved_counts: dict[str, int] = Field(default_factory=dict)
    #: True when the completion ran into the token ceiling — the reply was
    #: probably cut mid-JSON and served from salvage. Logged, never shown.
    ceiling_hit: bool = False


def _elide(text: str, limit: int = CHRONICLE_LINE_LIMIT) -> str:
    """Whitespace-normalized text, elided head+tail past ``limit`` chars."""
    text = " ".join(text.split())
    if len(text) <= limit:
        return text
    head = text[: limit // 2].rsplit(" ", 1)[0]
    tail = text[-(limit // 2):].split(" ", 1)[-1]
    return f"{head} … {tail}"


def _chronicle_entry(entry: dict[str, Any]) -> str:
    """One chronicle line: dialogue keeps its speaker, everything else is text."""
    text = _elide(str(entry.get("text") or ""))
    speaker = str(entry.get("speaker") or "").strip()
    if str(entry.get("kind") or "") == "dialogue" and speaker:
        return f'- {speaker}: "{text}"'
    return f"- {text}"


def assemble_prompt(
    ctx: PromptContext,
    role_system: str = "",
    prefs: ContentPrefs | None = None,
) -> PromptBundle:
    """Build the narrator prompt from retrieved context slices only."""
    npcs = ctx.npcs[:MAX_NPCS]
    facts = ctx.world_facts[:MAX_FACTS]
    events = ctx.recent_events[-MAX_RETRIEVED_EVENTS:]
    chronicle = ctx.chronicle[-MAX_RETRIEVED_CHRONICLE:]
    # Content settings gate how a mood surfaces in the prompt (§4): a gated
    # word the settings do not allow must never reach the model either.
    nsfw = bool(prefs and prefs.nsfw)

    def _npc_entry(n: dict[str, Any]) -> str:
        line = f"- {n.get('name', '?')}: {n.get('note', '')}"
        mood = str(n.get("mood") or "")
        level = float(n.get("mood_intensity") or 0.0)
        if mood and level > 0:
            line += f" | mood: {surfaced_mood(mood, nsfw=nsfw)} ({level:.1f})"
        remembered = [str(m) for m in (n.get("remembers") or [])][:MAX_NPC_MEMORIES]
        if remembered:
            line += " | remembers about the player: " + "; ".join(remembered)
        disposition = str(n.get("disposition") or "").strip()
        if disposition:
            line += f" | disposition: {disposition}"
        return line

    npc_block = "\n".join(_npc_entry(n) for n in npcs) or "- (none present)"
    fact_block = "\n".join(f"- {f}" for f in facts) or "- (no established facts)"
    lead_block = "\n".join(f"- {l.get('title', '?')}: {l.get('status', '')}" for l in ctx.leads) or "- (no active leads)"
    # The beat block (P14): the engine resolved this turn into a known outcome
    # before any prose existed. The facts are code-owned — the prose must carry
    # every one of them, in the model's own words, and invent nothing else.
    beat_block = ""
    if ctx.beat_event or ctx.beat_facts:
        lines = [f"event: {ctx.beat_event}"] if ctx.beat_event else []
        if ctx.beat_facts:
            lines.append(
                "established by the engine — write this beat so that every one of "
                "these facts is conveyed, in your own words and fully; invent "
                "nothing beyond them:"
            )
            lines += [f"- {f}" for f in ctx.beat_facts]
        if ctx.beat_speakers:
            lines.append(
                "speakers (voice them in their own character; each spoken line "
                "renders exactly once): " + ", ".join(ctx.beat_speakers)
            )
        if ctx.beat_direction:
            lines.append(f"direction: {ctx.beat_direction}")
        beat_block = "[Beat]\n" + "\n".join(lines) + "\n\n"
    chronicle_block = ("\n".join(_chronicle_entry(e) for e in chronicle)
                       or "- (the chronicle opens here)")
    saga_block = " ".join(str(ctx.saga or "").split()) or "- (the saga opens here)"
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
        f"[Story so far (rolling recap, oldest truth first)]\n{saga_block}\n\n"
        f"[Recent chronicle (retrieved, newest last)]\n{chronicle_block}\n\n"
        f"[Recent events (retrieved, newest last)]\n{event_block}\n\n"
        f"[Player action]\n{ctx.player_action}\n\n"
        f"{beat_block}"
        f"[Mechanical result]\n{ctx.mechanical_result}\n\n"
        f"[Length]\n{ctx.length}: "
        + {"Concise": "40-80 words.", "Standard": "100-250 words.",
           "Detailed": "250-450 words."}.get(ctx.length, "100-250 words.")
        + f"\n\n[Output schema]\n{ctx.output_schema}"
    )
    return PromptBundle(system=system, user=user, retrieved_counts={
        "npcs": len(npcs), "facts": len(facts), "events": len(events),
        "leads": len(ctx.leads), "chronicle": len(chronicle),
    })
