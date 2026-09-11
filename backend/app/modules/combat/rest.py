"""Short vs long rest. Small/quick vs big/needs-safety + world clock."""
from __future__ import annotations

from pydantic import BaseModel

from app.modules.combat.models import Combatant

SHORT_REST_HOURS = 1
LONG_REST_HOURS = 8
# Long rests in danger at or above this level risk interruption.
DANGER_THRESHOLD = 5


class RestResult(BaseModel):
    kind: str  # short | long
    hours_passed: int
    hp_restored: dict[str, int] = {}
    stamina_restored: dict[str, int] = {}
    injuries_healed: dict[str, list[str]] = {}
    interrupted: bool = False
    event: str = ""


def short_rest(party: list[Combatant]) -> RestResult:
    """Small recovery, limited time. Stabilizes bleeding, heals a little."""
    result = RestResult(kind="short", hours_passed=SHORT_REST_HOURS)
    for c in party:
        if c.status == "dead":
            continue
        hp_gain = min(c.max_hp - c.hp, max(1, c.max_hp // 4))
        c.hp += hp_gain
        result.hp_restored[c.id] = hp_gain
        before = c.stamina
        c.stamina = min(c.max_stamina, c.stamina + c.max_stamina // 2)
        result.stamina_restored[c.id] = c.stamina - before
        healed: list[str] = []
        if "DeepCut" in c.injuries:
            # A short rest stabilizes bleeding only if someone tends it;
            # here it stops the tick but the injury remains.
            healed = []
        result.injuries_healed[c.id] = healed
    result.event = "The party catches its breath."
    return result


def long_rest(party: list[Combatant], *, safe: bool, danger: int = 0) -> RestResult:
    """Larger recovery that needs safety and advances the clock. Danger risks
    interruption: the rest is cut short with only partial recovery."""
    result = RestResult(kind="long", hours_passed=LONG_REST_HOURS)
    if not safe or danger >= DANGER_THRESHOLD:
        result.interrupted = True
        result.hours_passed = 2
        for c in party:
            if c.status == "dead":
                continue
            hp_gain = min(c.max_hp - c.hp, max(1, c.max_hp // 10))
            c.hp += hp_gain
            result.hp_restored[c.id] = hp_gain
            result.stamina_restored[c.id] = 0
            result.injuries_healed[c.id] = []
        result.event = (
            "The rest is broken — danger finds the camp. "
            "Only a fraction of strength returns."
        )
        return result
    for c in party:
        if c.status == "dead":
            continue
        hp_gain = c.max_hp - c.hp
        c.hp = c.max_hp
        result.hp_restored[c.id] = hp_gain
        gain = c.max_stamina - c.stamina
        c.stamina = c.max_stamina
        result.stamina_restored[c.id] = gain
        healed: list[str] = []
        if c.injuries:
            healed = [c.injuries.pop(0)]
        if c.status == "stable":
            c.status = "active"
            c.hp = max(c.hp, c.max_hp // 2)
        result.injuries_healed[c.id] = healed
    result.event = "A full night's rest. Wounds close; the world clock moves on."
    return result
