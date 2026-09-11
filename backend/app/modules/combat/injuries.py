"""Lasting injuries: HP is immediate capacity, injuries outlive the fight."""
from __future__ import annotations

from enum import Enum

from app.modules.combat.models import Combatant


class InjuryKind(str, Enum):
    BRUISED_RIBS = "BruisedRibs"
    BROKEN_ARM = "BrokenArm"
    DEEP_CUT = "DeepCut"
    CONCUSSION = "Concussion"


INJURY_EFFECTS: dict[str, str] = {
    InjuryKind.BRUISED_RIBS.value: "-4 stamina regen per round; -1 to melee damage",
    InjuryKind.BROKEN_ARM.value: "-4 to attacks with two-handed/heavy weapons",
    InjuryKind.DEEP_CUT.value: "bleeds 2 HP per round until treated",
    InjuryKind.CONCUSSION.value: "-2 to all checks until healed",
}

# A single hit this large (fraction of max HP), or dropping this low,
# converts pain into a lasting injury.
HEAVY_HIT_FRACTION = 0.4
LOW_HP_FRACTION = 0.3


def should_inflict(hit_damage: int, combatant: Combatant, *, critical: bool = False) -> bool:
    if critical and hit_damage > 0:
        return True
    if hit_damage >= combatant.max_hp * HEAVY_HIT_FRACTION:
        return True
    return combatant.hp - hit_damage <= combatant.max_hp * LOW_HP_FRACTION and hit_damage >= 8


def pick_injury(hit_damage: int, max_hp: int) -> str:
    """Deterministic pick so the same blow always tells the same story."""
    kinds = [k.value for k in InjuryKind]
    return kinds[(hit_damage + max_hp) % len(kinds)]


def inflict(combatant: Combatant, kind: str) -> str:
    if kind not in combatant.injuries:
        combatant.injuries.append(kind)
    return kind


def attack_penalty(combatant: Combatant, heavy: bool = False) -> int:
    penalty = 0
    if InjuryKind.CONCUSSION.value in combatant.injuries:
        penalty -= 2
    if heavy and InjuryKind.BROKEN_ARM.value in combatant.injuries:
        penalty -= 4
    return penalty


def bleed_tick(combatant: Combatant) -> int:
    """Ongoing DeepCut bleed. Returns HP lost."""
    if InjuryKind.DEEP_CUT.value in combatant.injuries and combatant.status == "active":
        combatant.hp = max(0, combatant.hp - 2)
        return 2
    return 0


def treat_injury(combatant: Combatant, kind: str, *, with_medicine: bool) -> bool:
    """Field medicine removes one lasting injury. Without supplies it fails
    for the serious ones (BrokenArm, DeepCut) but can stabilize BruisedRibs."""
    if kind not in combatant.injuries:
        return False
    if not with_medicine and kind in (InjuryKind.BROKEN_ARM.value, InjuryKind.DEEP_CUT.value):
        return False
    combatant.injuries.remove(kind)
    return True
