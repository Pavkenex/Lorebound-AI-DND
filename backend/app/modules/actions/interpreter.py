"""Action interpreter: player free text -> structured Intent (t_0ed70b13).

Core rule (GDD §23, §108): player text is ONLY an attempted action or
dialogue. It can NEVER establish a world fact. Claims like "I find a
legendary sword" or "the king promised me the kingdom" are recorded as
*asserted claims* and stripped of effect — they grant nothing.
"""
from __future__ import annotations

import re
from enum import Enum
from typing import Any

from pydantic import BaseModel, Field

from app.modules.rules.checks import CheckRequest


class IntentKind(str, Enum):
    DIALOGUE = "dialogue"
    ATTACK = "attack"
    INSPECT = "inspect"
    SNEAK = "sneak"
    MOVE = "move"
    USE_ITEM = "use_item"
    SKILL_USE = "skill_use"
    SOCIAL = "social"
    REST = "rest"
    OTHER = "other"


class Risk(str, Enum):
    NONE = "none"
    LOW = "low"
    MODERATE = "moderate"
    HIGH = "high"


class CheckSpec(BaseModel):
    skill: str
    difficulty: str = "Moderate"
    hidden: bool = False
    social: bool = False


class Intent(BaseModel):
    kind: IntentKind
    summary: str
    raw_text: str
    checks: list[CheckSpec] = Field(default_factory=list)
    risk: Risk = Risk.LOW
    target: str | None = None
    dialogue_text: str | None = None
    #: Claims the player asserted as fact ("I find...", "the king gave me...").
    #: Recorded for the narrator to gently deny; NEVER applied to state.
    asserted_claims: list[str] = Field(default_factory=list)
    world_fact_attempt: bool = False


# Declarative world-fact grabs: first-person acquisition / possession /
# NPC-memory / environment authorship. Any match => world_fact_attempt.
_FACT_PATTERNS: list[re.Pattern] = [
    re.compile(r"\bi\s+(find|discover|grab|take|pick up|loot|gain|get|receive|am given)\b.*\b(legendary|magic|sword|treasure|gold|artifact|crown|kingdom|potion|map)\b", re.IGNORECASE),
    re.compile(r"\bi\s+(have|own|carry|possess)\b.*\b(legendary|magic|sword|kingdom|crown|artifact)\b", re.IGNORECASE),
    re.compile(r"\b(there is|there's|i see|i spot|i notice)\b.*\b(secret|hidden|legendary|treasure|sword|passage|portal)\b.*\b(appears?|here|nearby|in .*(room|pocket|backpack))\b", re.IGNORECASE),
    re.compile(r"\b(king|queen|marla|guard|innkeeper|npc|he|she|they)\s+(remembers?|promised?|gave|gives?|owes?|agreed?|knows?)\b.*\b(me|us)\b", re.IGNORECASE),
    re.compile(r"\bi\s+am\s+(now\s+)?(king|queen|rich|invisible|invincible|level\s*\d+|a\s+\w+ (wizard|lord|god))\b", re.IGNORECASE),
    re.compile(r"\b(suddenly|a (dragon|portal|chest|sword|wizard))\s+(appears?|is here|shows? up)\b", re.IGNORECASE),
    # Acquisition claim without adjacent pronoun ("...and find a legendary sword").
    # Handled via self-source conjunction below, not a bare pattern: searching a
    # chest is a legitimate Investigation attempt (engine decides loot), while
    # finding valuables on one's own person is authorship.
    # Environment authorship ("there is a secret portal here").
    re.compile(r"\bthere is\b.{0,30}\b(secret|hidden|legendary|treasure|sword|passage|portal)\b", re.IGNORECASE),
    re.compile(r"\b(secret|hidden)\b.{0,20}\b(portal|passage|treasure|door|sword)\b", re.IGNORECASE),
    re.compile(r"\bportal\b.{0,15}\bhere\b", re.IGNORECASE),
]

_DIALOGUE_RE = re.compile(r'^\s*["“]|^(say|tell|ask|shout|whisper|reply|answer|greet|talk|speak)\b', re.IGNORECASE)
_SOCIAL_RE = re.compile(r"\b(persuad\w*|convinc\w*|intimidat\w*|decei\w*|decept\w*|charm\w*|threat\w*|brib\w*|negotiat\w*|plead\w*|beg(?:s|ged|ging)?|lie|lies|lied|lying)\b", re.IGNORECASE)
_ATTACK_RE = re.compile(r"\b(attack|strike|stab|slash|shoot|fire|swing|hit|kill|fight|draw (my |the )?sword|cast .* at)\b", re.IGNORECASE)
_SNEAK_RE = re.compile(r"\b(sneak|hide|steal|pickpocket|prowl|creep|lurk|shadow|eavesdrop)\b", re.IGNORECASE)
_INSPECT_RE = re.compile(r"\b(inspect|examine|search|look|investigate|check|scan|study|peer|glance)\b", re.IGNORECASE)
_MOVE_RE = re.compile(r"\b(go|walk|run|travel|head|leave|enter|exit|flee|retreat|approach|follow|climb|open the door)\b", re.IGNORECASE)
_ITEM_RE = re.compile(r"\b(use|drink|eat|equip|unequip|drop|throw|light|read)\b.*\b(potion|torch|rope|scroll|key|lantern|map|item|sword|bow)\b", re.IGNORECASE)
_REST_RE = re.compile(r"\b(rest|sleep|camp|nap|recover|short rest|long rest)\b", re.IGNORECASE)

# Skill keywords -> (skill, difficulty, hidden)
_SKILL_HINTS: list[tuple[re.Pattern, str, str, bool]] = [
    (re.compile(r"\b(trap|disarm|lockpick|locked)\b", re.IGNORECASE), "Thievery", "Difficult", False),
    (re.compile(r"\b(lie|lying|insight|sense motive|bluff)\b", re.IGNORECASE), "Insight", "Moderate", True),
    (re.compile(r"\b(trap|ambush|followed|tracks?|footprints?)\b", re.IGNORECASE), "Perception", "Moderate", True),
    (re.compile(r"\b(recall|remember|know|history|lore|arcana|religion)\b", re.IGNORECASE), "Lore", "Moderate", True),
    (re.compile(r"\b(climb|swim|jump|lift|push|break|force)\b", re.IGNORECASE), "Athletics", "Moderate", False),
    (re.compile(r"\b(persua|charm|negotiat|plead)\b", re.IGNORECASE), "Persuasion", "Moderate", False),
    (re.compile(r"\b(intimidat|threaten)\b", re.IGNORECASE), "Intimidation", "Moderate", False),
    (re.compile(r"\b(decei|bluff|lie to)\b", re.IGNORECASE), "Deception", "Moderate", False),
    (re.compile(r"\b(sneak|stealth|hide|steal|pickpocket)\b", re.IGNORECASE), "Stealth", "Moderate", False),
    (re.compile(r"\b(investigat|search|inspect|examine)\b", re.IGNORECASE), "Investigation", "Moderate", False),
    (re.compile(r"\b(sword|attack|fight|strike|shoot|bow)\b", re.IGNORECASE), "Swordsmanship", "Moderate", False),
]

_TARGET_RE = re.compile(
    r"\b(?:the|a|an|my|to|at|toward|towards)\s+([A-Z][a-z]+(?:\s+[A-Z][a-z]+)?|"
    r"guard|king|queen|innkeeper|marla|door|chest|room|window|table|bar|stranger|merchant|traveler)\b"
)


def _strip_fact_claims(text: str) -> tuple[str, list[str], bool]:
    claims: list[str] = []
    for pat in _FACT_PATTERNS:
        m = pat.search(text)
        if m:
            claims.append(m.group(0).strip())
    # Self-sourced loot: valuables "found" on one's own person (backpack,
    # pockets) is authorship, not discovery. (Searching a chest/room is a
    # legitimate check — the engine decides what, if anything, is there.)
    if re.search(r"\b(backpack|pockets?|pouch|bag|my person|thin air|nowhere|inventory)\b", text, re.IGNORECASE) \
            and re.search(r"\b(find|discover|grab|take|pick up|loot|gain|get|receive)\b.{0,40}"
                          r"\b(legendary|magic|sword|treasure|gold|artifact|crown|kingdom|potion)\b", text, re.IGNORECASE):
        claims.append("self-sourced loot claim")
    return text, claims, bool(claims)


def parse(text: str) -> Intent:
    """Convert player prose into a structured Intent.

    Deterministic and local (no model call): safe default for the pipeline
    and for tests. An LLM interpreter role may *propose* an Intent, but it
    goes through the same world_fact_attempt stripping before use.
    """
    raw = text or ""
    _, claims, fact_attempt = _strip_fact_claims(raw)
    lowered = raw.lower()

    kind = IntentKind.OTHER
    if _DIALOGUE_RE.search(raw) and not _SOCIAL_RE.search(raw):
        kind = IntentKind.DIALOGUE
    elif _SOCIAL_RE.search(raw):
        kind = IntentKind.SOCIAL
    elif _ATTACK_RE.search(raw):
        kind = IntentKind.ATTACK
    elif _SNEAK_RE.search(raw):
        kind = IntentKind.SNEAK
    elif _INSPECT_RE.search(raw):
        kind = IntentKind.INSPECT
    elif _MOVE_RE.search(raw):
        kind = IntentKind.MOVE
    elif _ITEM_RE.search(raw):
        kind = IntentKind.USE_ITEM
    elif _REST_RE.search(raw):
        kind = IntentKind.REST
    elif _DIALOGUE_RE.search(raw):
        kind = IntentKind.DIALOGUE

    checks: list[CheckSpec] = []
    for pat, skill, diff, hidden in _SKILL_HINTS:
        if pat.search(raw):
            checks.append(CheckSpec(skill=skill, difficulty=diff, hidden=hidden,
                                    social=skill in ("Persuasion", "Intimidation", "Deception")))
            break
    # Fallback: non-trivial action kinds imply a check; pure dialogue does not.
    if not checks and kind in (IntentKind.ATTACK, IntentKind.SNEAK, IntentKind.INSPECT,
                               IntentKind.USE_ITEM, IntentKind.MOVE, IntentKind.SKILL_USE):
        default_skill = {
            IntentKind.ATTACK: "Swordsmanship", IntentKind.SNEAK: "Stealth",
            IntentKind.INSPECT: "Investigation", IntentKind.MOVE: "Athletics",
            IntentKind.USE_ITEM: "General", IntentKind.SKILL_USE: "General",
        }[kind]
        checks.append(CheckSpec(skill=default_skill))

    # A world-fact grab grants NOTHING: drop any check that would reward it.
    if fact_attempt:
        checks = [c for c in checks if c.skill not in ("General",)]

    risk = Risk.LOW
    if kind == IntentKind.ATTACK:
        risk = Risk.HIGH
    elif kind in (IntentKind.SNEAK, IntentKind.USE_ITEM) or fact_attempt:
        risk = Risk.MODERATE
    elif kind == IntentKind.DIALOGUE:
        risk = Risk.NONE

    tm = _TARGET_RE.search(raw)
    dialogue = raw.strip().strip("\"“”") if kind in (IntentKind.DIALOGUE, IntentKind.SOCIAL) else None

    summary = {
        IntentKind.DIALOGUE: "Speak",
        IntentKind.SOCIAL: "Social maneuver",
        IntentKind.ATTACK: "Attack",
        IntentKind.SNEAK: "Skullduggery",
        IntentKind.INSPECT: "Inspect surroundings",
        IntentKind.MOVE: "Move",
        IntentKind.USE_ITEM: "Use an item",
        IntentKind.REST: "Rest",
        IntentKind.SKILL_USE: "Use a skill",
        IntentKind.OTHER: "Act",
    }[kind]
    if tm:
        summary += f" — {tm.group(1)}"

    _ = lowered
    return Intent(kind=kind, summary=summary, raw_text=raw, checks=checks,
                  risk=risk, target=tm.group(1) if tm else None,
                  dialogue_text=dialogue, asserted_claims=claims,
                  world_fact_attempt=fact_attempt)


def to_check_requests(intent: Intent, campaign_id: str = "default",
                      character_id: str | None = None,
                      dc_overrides: dict[str, int] | None = None) -> list[CheckRequest]:
    """Expand an Intent's check specs into engine CheckRequests."""
    from app.modules.rules.checks import DC_BANDS
    out: list[CheckRequest] = []
    for spec in intent.checks:
        dc = (dc_overrides or {}).get(spec.skill, DC_BANDS.get(spec.difficulty, 13))
        out.append(CheckRequest(campaign_id=campaign_id, character_id=character_id,
                                skill=spec.skill, difficulty=spec.difficulty, dc=dc,
                                hidden=spec.hidden, social=spec.social))
    return out


def sanitize_for_narrator(intent: Intent) -> dict[str, Any]:
    """Narrator-safe view: attempted action only, claims flagged, never facts."""
    return {"kind": intent.kind.value, "summary": intent.summary,
            "risk": intent.risk.value, "target": intent.target,
            "dialogue_text": intent.dialogue_text,
            "asserted_claims": intent.asserted_claims,
            "world_fact_attempt": intent.world_fact_attempt,
            "checks": [c.model_dump() for c in intent.checks]}
