"""Mechanics resolver — Pass A (spec §5, §6). Pure code; no LLM.

Contract (R2 card):
- ``roll_expression`` parses ``NdM(+K|-K)(adv|dis)`` expressions, rolls via the
  injected RngSource, returns DiceResult (kept rolls, dropped rolls, total).
- ``resolve_check`` rolls d20 + skill modifier (from the skill's default table
  or an explicit override), applies the locked outcome bands from
  ARCHITECTURE.md ("Decisions"): nat-20 => CRITICAL (unconditional), nat-1 =>
  CRITICAL_FAILURE, margin 0-2 for non-crits => SUCCESS_AT_COST, below =>
  FAILURE, else SUCCESS. verdict_line is a one-line factual statement the
  narrator must honor.
- ``resolve_action`` maps an Intent to the mechanical work the turn implies
  (attack roll / skill check / flat check / nothing) and returns a
  MechanicalOutcome. It MAY read the store for eligibility (e.g., target
  present) and MAY attach suggested effect Deltas; it never commits anything.
- Determinism: everything takes an injected ``RngSource``; tests use seeded
  RNG. No global randomness.
"""
from __future__ import annotations

import random
from typing import Protocol

from .models import CheckRequest, CheckResult, DiceResult, Intent, MechanicalOutcome

# Default skill -> attribute modifier table used when the store has no explicit
# value (R2 may extend; keep values in [-2..+5]).
DEFAULT_SKILL_MODIFIERS: dict[str, int] = {
    "athletics": 2, "stealth": 2, "persuasion": 2, "intimidation": 1,
    "insight": 2, "perception": 2, "investigation": 2, "survival": 1,
    "arcana": 1, "medicine": 1, "deception": 2, "lockpicking": 2,
}


class RngSource(Protocol):
    def randint(self, a: int, b: int) -> int:  # inclusive
        ...


class SeededRng:
    """Deterministic RNG adapter — the only concrete RNG the engine ships."""

    def __init__(self, seed: int | str | None = None) -> None:
        self._r = random.Random(seed)

    def randint(self, a: int, b: int) -> int:
        return self._r.randint(a, b)


def roll_expression(expr: str, rng: RngSource) -> DiceResult:
    raise NotImplementedError("R2 card implements roll_expression")


def resolve_check(req: CheckRequest, *, rng: RngSource) -> CheckResult:
    raise NotImplementedError("R2 card implements resolve_check")


def resolve_action(intent: Intent, *, rng: RngSource, store=None, turn: int = 0) -> MechanicalOutcome:
    """Resolve whatever mechanics ``intent`` implies. ``store`` is optional so
    the resolver stays usable (and testable) without a DB."""
    raise NotImplementedError("R2 card implements resolve_action")
