"""Stamina economy: spend on the big moves, regen gradually, spam punishes itself.

Costs: heavy attacks, sprinting, techniques, and blocks. Regen ticks each round.
Repeating the same primary action escalates its cost (x1.0, x1.5, x2.0, ...),
so spamming the optimal button drains the pool and leaves the fighter exhausted.
"""
from __future__ import annotations

from dataclasses import dataclass

from app.modules.combat.models import Combatant
from app.modules.combat.turns import ActionKind

BASE_COSTS: dict[str, int] = {
    ActionKind.ATTACK_LIGHT.value: 5,
    ActionKind.ATTACK_HEAVY.value: 25,
    ActionKind.MOVE.value: 0,
    ActionKind.SPRINT.value: 20,
    ActionKind.TECHNIQUE.value: 20,
    ActionKind.BLOCK.value: 15,
    ActionKind.ENV_INTERACT.value: 10,
    ActionKind.MINOR.value: 0,
    ActionKind.REACTION.value: 5,
    ActionKind.PASS.value: 0,
}

REGEN_PER_ROUND = 10
REPEAT_SURCHARGE = 0.5  # +50% per consecutive repeat of the same primary


@dataclass
class SpendResult:
    ok: bool  # True if the pool covered the (surcharged) cost
    cost: int
    weakened: bool = False  # True when the action goes through exhausted


def primary_cost(kind: str, repeat_count: int) -> int:
    base = BASE_COSTS.get(kind, 5)
    return round(base * (1.0 + REPEAT_SURCHARGE * repeat_count))


def track_primary(combatant: Combatant, kind: str) -> int:
    """Update repeat bookkeeping; return the repeat count applied to this use."""
    if combatant.last_primary_kind == kind:
        combatant.repeat_count += 1
    else:
        combatant.last_primary_kind = kind
        combatant.repeat_count = 0
    return combatant.repeat_count


def spend_stamina(combatant: Combatant, kind: str) -> SpendResult:
    """Charge a primary-action cost. Shortfalls go through weakened, not free."""
    repeats = track_primary(combatant, kind)
    cost = primary_cost(kind, repeats)
    if combatant.stamina >= cost:
        combatant.stamina -= cost
        combatant.exhausted = False
        return SpendResult(ok=True, cost=cost)
    # Not enough in the tank: burn what's left, flag exhaustion, weaken output.
    spent = combatant.stamina
    combatant.stamina = 0
    combatant.exhausted = True
    return SpendResult(ok=False, cost=spent, weakened=True)


def spend_flat(combatant: Combatant, amount: int) -> bool:
    """Flat charge for movement/block style costs. Returns False if short."""
    if combatant.stamina >= amount:
        combatant.stamina -= amount
        return True
    combatant.stamina = 0
    combatant.exhausted = True
    return False


def regenerate(combatant: Combatant) -> int:
    """Gradual per-round recovery. Bruised ribs slow it down (see injuries)."""
    amount = REGEN_PER_ROUND
    if "BruisedRibs" in combatant.injuries:
        amount = max(0, amount - 4)
    before = combatant.stamina
    combatant.stamina = min(combatant.max_stamina, combatant.stamina + amount)
    if combatant.stamina >= combatant.max_stamina // 2:
        combatant.exhausted = False
    return combatant.stamina - before
