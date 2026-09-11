"""Output validator: reject, repair, or regenerate (t_8d796df9, GDD §78).

Catches: nonexistent item mentioned, dead character described alive, player
moved without permission (teleport), unapproved reward granted, world-history
contradiction. Each failure mode has a handling path (reject proposal /
auto-repair / regenerate narration) plus a logged event.
"""
from __future__ import annotations

import re
from enum import Enum

from pydantic import BaseModel, Field

from app.core.events import EventKind, GameEvent


class Handling(str, Enum):
    ACCEPT = "accept"
    REPAIR = "repair"
    REJECT_PROPOSAL = "reject_proposal"
    REGENERATE = "regenerate"


class Issue(BaseModel):
    code: str
    detail: str
    handling: Handling


class ValidationReport(BaseModel):
    ok: bool
    issues: list[Issue] = Field(default_factory=list)
    action: Handling = Handling.ACCEPT


class WorldSnapshot(BaseModel):
    """Minimal engine-state view the validator checks narration against."""

    inventory: list[str] = Field(default_factory=list)
    alive: dict[str, bool] = Field(default_factory=dict)
    location: str = ""
    known_locations: list[str] = Field(default_factory=list)
    facts: list[str] = Field(default_factory=list)
    approved_rewards: list[str] = Field(default_factory=list)


_ITEM_RE = re.compile(r"\b(you (?:find|gain|receive|are given|pick up|take)|added to your inventory:?)\b(.{0,120})", re.IGNORECASE)
_ALIVE_RE = re.compile(r"\b([A-Z][a-z]+)\b.{0,40}\b(smiles|laughs|says|nods|waves|greets|attacks|speaks|walks|stands|watches)\b")
_MOVE_RE = re.compile(r"\b(you (?:arrive|appear|find yourself|are now|wake up)|suddenly (?:in|at))\b.{0,40}\b(in|at)\s+([A-Z][a-z]+(?:\s+[A-Z][a-z]+)?)", re.IGNORECASE)
_REWARD_RE = re.compile(r"\b(you (?:gain|earn|receive)|reward:?|\+?\d+\s*(?:gold|xp|experience|hp))\b", re.IGNORECASE)


def validate_narration(narration: str, world: WorldSnapshot,
                       approved_move: str | None = None) -> ValidationReport:
    """Check narration against engine state. Pure function (no logging here;
    the service logs the returned report)."""
    issues: list[Issue] = []
    text = narration or ""

    # 1. Nonexistent item mentioned as gained -> reject/repair.
    m = _ITEM_RE.search(text)
    if m:
        gained = m.group(2)
        if not any(item.lower() in gained.lower() for item in world.inventory + world.approved_rewards):
            issues.append(Issue(code="bad_item",
                                detail=f"mentions gaining unapproved item: {gained.strip()[:80]}",
                                handling=Handling.REPAIR))

    # 2. Dead character described alive -> regenerate.
    for m in _ALIVE_RE.finditer(text):
        name = m.group(1)
        if name in world.alive and not world.alive[name]:
            issues.append(Issue(code="dead_alive",
                                detail=f"{name} is dead but described alive",
                                handling=Handling.REGENERATE))
            break

    # 3. Teleport: moved without permission -> repair.
    m = _MOVE_RE.search(text)
    if m:
        dest = re.sub(r"^(the|a|an)\s+", "", m.group(3).strip(), flags=re.IGNORECASE)
        approved = {s.lower() for s in ([approved_move] if approved_move else [])
                    + [world.location] + list(world.known_locations or []) if s}
        if (
            dest
            and dest.lower() not in approved
            # Only flag if destination is genuinely new, not the current one.
            and world.location
            and dest.lower() not in world.location.lower()
        ):
            issues.append(Issue(code="teleport",
                                detail=f"player moved to {dest} without approval",
                                handling=Handling.REPAIR))

    # 4. Unapproved reward -> reject proposal path.
    if _REWARD_RE.search(text) and not world.approved_rewards:
        issues.append(Issue(code="unapproved_reward",
                            detail="narration grants a reward the engine never approved",
                            handling=Handling.REJECT_PROPOSAL))

    # 5. Contradiction of established facts -> regenerate.
    # Negation ("not"/"never"/"n't") plus most of a fact's content words.
    negated = bool(re.search(r"\b(not|never|no longer|n't)\b", text, re.IGNORECASE))
    if negated:
        low = text.lower()
        for fact in world.facts:
            stems = {w[:4] for w in re.findall(r"[a-z]{4,}", fact.lower())}
            if len(stems) >= 2 and sum(1 for s in stems if s in low) >= len(stems) - 1:
                issues.append(Issue(code="contradiction",
                                    detail=f"contradicts established fact: {fact[:80]}",
                                    handling=Handling.REGENERATE))
                break

    if not issues:
        return ValidationReport(ok=True, action=Handling.ACCEPT)
    # Escalation order: regenerate > repair > reject_proposal.
    rank = {Handling.REGENERATE: 3, Handling.REPAIR: 2, Handling.REJECT_PROPOSAL: 1, Handling.ACCEPT: 0}
    action = max((i.handling for i in issues), key=lambda h: rank[h])
    return ValidationReport(ok=False, issues=issues, action=action)


def repair_narration(narration: str, report: ValidationReport) -> str:
    """Auto-repair: strip sentences triggering repair/reject-class issues."""
    if report.ok:
        return narration
    bad_codes = {i.code for i in report.issues
                 if i.handling in (Handling.REPAIR, Handling.REJECT_PROPOSAL)}
    if not bad_codes:
        return narration
    sentences = re.split(r"(?<=[.!?])\s+", narration)
    kept: list[str] = []
    for s in sentences:
        low = s.lower()
        drop = (
            ("bad_item" in bad_codes and bool(_ITEM_RE.search(s)))
            or ("teleport" in bad_codes and bool(_MOVE_RE.search(s)))
            or ("unapproved_reward" in bad_codes and bool(_REWARD_RE.search(s)))
        )
        _ = low
        if not drop:
            kept.append(s)
    out = " ".join(kept).strip()
    return out or "The moment passes without further incident."


def log_report(report: ValidationReport, campaign_id: str = "default") -> GameEvent | None:
    """Log validation failures as events. Returns the event or None if clean."""
    if report.ok:
        return None
    return GameEvent(kind=EventKind.WORLD_EVENT_OCCURRED, campaign_id=campaign_id,
                     payload={"validator": True, "action": report.action.value,
                              "issues": [i.model_dump() for i in report.issues]})
