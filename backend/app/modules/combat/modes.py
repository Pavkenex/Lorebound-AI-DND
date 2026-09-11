"""Incapacitation & campaign modes. Forgiving by default, lethal by choice."""
from __future__ import annotations

from enum import Enum

from app.modules.combat.models import Combatant


class CampaignMode(str, Enum):
    STORY = "story"
    STANDARD = "standard"
    HARDCORE = "hardcore"
    IRON = "iron"  # Iron Chronicle: permadeath


DEFAULT_MODE = CampaignMode.STANDARD.value
DEATH_SAVE_DC = {"story": 5, "standard": 10, "hardcore": 13, "iron": 99}


def resolve_zero_hp(combatant: Combatant, mode: str) -> str:
    """Apply the mode's zero-HP rule. Returns the resulting status."""
    combatant.hp = 0
    if mode == CampaignMode.IRON.value:
        combatant.status = "dead"
        return "dead"
    if mode == CampaignMode.STORY.value:
        combatant.status = "stable"  # auto-stabilized, no saves needed
        return "stable"
    combatant.status = "incapacitated"
    combatant.death_save_fails = 0
    combatant.death_save_successes = 0
    return "incapacitated"


def death_save(combatant: Combatant, mode: str, roll: int) -> str:
    """Record one death save (d20 roll). Returns updated status."""
    if combatant.status != "incapacitated":
        return combatant.status
    dc = DEATH_SAVE_DC.get(mode, 10)
    if roll == 20:
        combatant.status = "stable"
        return "stable"
    if roll == 1:
        combatant.death_save_fails += 2
    elif roll >= dc:
        combatant.death_save_successes += 1
        if combatant.death_save_successes >= 3:
            combatant.status = "stable"
    else:
        combatant.death_save_fails += 1
    if combatant.death_save_fails >= 3:
        combatant.status = "dead"
    return combatant.status


def stabilize(combatant: Combatant) -> bool:
    """Ally aid / medicine stabilizes the incapacitated. Iron dead stay dead."""
    if combatant.status == "incapacitated":
        combatant.status = "stable"
        return True
    return False
