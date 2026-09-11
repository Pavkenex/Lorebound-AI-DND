"""Hybrid content policy: authored vs procedural (GDD §83, §120).

Authored: core region lore, major factions/characters/mysteries, key
locations, world rules. Generated: minor NPCs, rumours, small encounters,
dialogue, room descriptions, side complications, incidental detail.

Do NOT build procedural world generation (§120): requests for it are refused.
"""
from __future__ import annotations

from enum import Enum


class ContentGate(str, Enum):
    AUTHORED = "authored"
    GENERATED = "generated"


AUTHORED_KINDS: frozenset[str] = frozenset({
    "region_lore", "major_faction", "major_character", "major_mystery",
    "key_location", "world_rule",
})

GENERATED_KINDS: frozenset[str] = frozenset({
    "minor_npc", "rumour", "small_encounter", "dialogue",
    "room_description", "side_complication", "incidental_detail",
})


class PolicyRefusal(ValueError):
    """Raised when content policy forbids the request."""


def classify(content_kind: str) -> ContentGate:
    if content_kind in AUTHORED_KINDS:
        return ContentGate.AUTHORED
    if content_kind in GENERATED_KINDS:
        return ContentGate.GENERATED
    raise PolicyRefusal(f"Unknown content kind: {content_kind!r}")


def gate_narrator_text(content_kind: str, source: ContentGate | str) -> bool:
    """Major lore must come from authored canon; generated filler must not
    pose as canon. Returns True if allowed."""
    if isinstance(source, str):
        source = ContentGate(source)
    required = classify(content_kind)
    return source == required


def request_procedural_worldgen(*args: object, **kwargs: object) -> object:
    """Procedural world generation is out of scope (§120) — always refused."""
    raise PolicyRefusal(
        "Procedural world generation is not built (§120). "
        "Use authored canon plus generated incidental detail."
    )
