"""Rolling saga digest (§25 continuity, suggestion #4).

The narrator prompt carries the last beats verbatim (the chronicle tail), but
nothing remembers the shape of the whole tale — by act ten, act one is gone.
The digest is the fix: a short deterministic recap, refreshed at checkpoint
beats (scene transitions, lead/clue/route progress) and on a fallback
cadence, carried in every narrator prompt as [Story so far].

Deliberately model-free: it is composed from structured state only (no extra
model call, no token spend) and campaign-agnostic (no hardcoded names — it
reads the live roster, leads and memories, so it works for any campaign and
any cast).
"""
from __future__ import annotations

from typing import Any

from app.modules.memory.npc_memory import display_name
from app.modules.play.state import attitude_band
from app.modules.play.view import location_name

#: Hard cap on the digest — about 180 words.
SAGA_MAX_CHARS = 1100
#: Idle beats refresh on this cadence so a long linger never goes stale.
SAGA_EVERY_ACTIONS = 12
#: Memories below this salience never make the saga.
SAGA_MIN_SALIENCE = 3


def _humanize(token: str) -> str:
    return str(token or "").replace("-", " ").replace("_", " ").strip()


def build_saga_digest(state: Any, *, location_label: str | None = None) -> str:
    """Compose the rolling recap from structured state. Pure, no model call."""
    pc = getattr(state, "pc", None) or {}
    name = str(pc.get("name") or "the hero")
    label = location_label or location_name(state)
    day = int(getattr(state, "day", 1) or 1)
    hour = int(getattr(state, "hour", 0) or 0)
    minute = int(getattr(state, "minute", 0) or 0)
    parts = [f"{name} came down the road to {label}; it is day {day}, {hour:02d}:{minute:02d}."]

    quest: list[str] = []
    if getattr(state, "completed", False):
        quest.append("The matter is closed.")
    else:
        if getattr(state, "travelers_freed", False):
            quest.append("The missing travelers walk free.")
        solution = getattr(state, "solution_path", None)
        if solution:
            quest.append(f"A way to end it lies open ({_humanize(str(solution))}).")
        clues = list(getattr(state, "clues", None) or [])
        if clues:
            shown = ", ".join(_humanize(c) for c in clues[:4])
            more = f", and {len(clues) - 4} more" if len(clues) > 4 else ""
            quest.append(f"Signs gathered: {shown}{more}.")
        if (
            str(getattr(state, "lead_stage", "unheard")) != "unheard"
            and not clues
            and not solution
        ):
            quest.append("Word of trouble is in the air.")
    parts.extend(quest)

    try:
        attitudes = dict(getattr(state, "attitudes", None) or {})
    except (TypeError, ValueError):
        attitudes = {}
    felt: list[tuple[str, int]] = []
    for slug, value in attitudes.items():
        try:
            ivalue = int(value or 0)
        except (TypeError, ValueError):
            continue
        if ivalue:
            felt.append((str(slug), ivalue))
    felt.sort(key=lambda kv: (-abs(kv[1]), kv[0]))
    for slug, ivalue in felt[:3]:
        parts.append(f"{display_name(slug)} holds you {attitude_band(ivalue)} ({ivalue}).")

    log = list(getattr(state, "npc_memory_log", None) or [])
    indexed: list[tuple[int, dict[str, Any]]] = []
    for idx, memory in enumerate(log):
        if not isinstance(memory, dict):
            continue
        try:
            salience = int(memory.get("salience", 0) or 0)
        except (TypeError, ValueError):
            salience = 0
        text = str(memory.get("text") or "").strip()
        if salience >= SAGA_MIN_SALIENCE and text:
            indexed.append((idx, memory))
    indexed.sort(key=lambda pair: (-int(pair[1].get("salience", 0) or 0), -pair[0]))
    remembered: list[str] = []
    for _, memory in indexed[:3]:
        text = " ".join(str(memory.get("text") or "").split())
        remembered.append(text[:140])
    if remembered:
        parts.append("Not forgotten: " + "; ".join(remembered) + ".")

    text = " ".join(part for part in parts if part)
    if len(text) > SAGA_MAX_CHARS:
        text = text[:SAGA_MAX_CHARS].rsplit(" ", 1)[0] + " …"
    return text


def saga_due(state: Any) -> bool:
    """True when the digest is missing past the opening beats or gone stale."""
    try:
        taken = int(getattr(state, "actions_taken", 0) or 0)
        at = int(getattr(state, "saga_at", 0) or 0)
    except (TypeError, ValueError):
        return True
    if not str(getattr(state, "saga", "") or "") and taken >= 2:
        return True
    return (taken - at) >= SAGA_EVERY_ACTIONS


def refresh_saga(state: Any, *, location_label: str | None = None, force: bool = False) -> bool:
    """Rebuild the digest when forced (checkpoint beat) or due. Returns changed."""
    if not force and not saga_due(state):
        return False
    built = build_saga_digest(state, location_label=location_label)
    try:
        taken = int(getattr(state, "actions_taken", 0) or 0)
    except (TypeError, ValueError):
        taken = 0
    changed = built != str(getattr(state, "saga", "") or "")
    state.saga = built
    state.saga_at = taken
    return changed
