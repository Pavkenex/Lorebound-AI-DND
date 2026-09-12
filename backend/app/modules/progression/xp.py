"""Skill XP formula (GDD §13).

Skill XP = Challenge x Performance x Novelty x TrainingModifier.

Each factor is a standalone pure function so it can be tested independently.
``Outcome`` is imported from the rules module when available; a local fallback
keeps this module (and its tests) working standalone.
"""
from __future__ import annotations

from enum import Enum
from typing import Any

try:  # Contract: XP formula honours the shared rules Outcome type.
    from app.modules.rules.degrees import Outcome  # type: ignore
except ImportError:  # Standalone fallback (tests must pass without rules/).

    class Outcome(str, Enum):
        CRITICAL_FAILURE = "critical_failure"
        FAILURE = "failure"
        PARTIAL = "partial"
        SUCCESS = "success"
        CRITICAL_SUCCESS = "critical_success"


def challenge_factor(challenge: int) -> float:
    """Harder tasks yield more learning. Challenge is 1-10.

    Calibrated so a trivial (challenge 1) success with fresh novelty = 8 XP.
    """
    challenge = max(challenge, 1)
    challenge = min(challenge, 10)
    return 6.0 + 2.0 * challenge


_PERFORMANCE_TABLE: dict[str, float] = {
    "critical_failure": 0.0,
    "criticalfailure": 0.0,
    "fumble": 0.0,
    "failure": 0.25,
    "fail": 0.25,
    "partial": 0.6,
    "mixed": 0.6,
    "success": 1.0,
    "critical_success": 1.5,
    "criticalsuccess": 1.5,
    "crit": 1.5,
    "exceptional": 1.5,
}


def _outcome_key(outcome: Any) -> str:
    if isinstance(outcome, Enum):
        return str(outcome.value).lower().replace(" ", "_").replace("-", "_")
    return str(outcome).lower().replace(" ", "_").replace("-", "_")


def performance_factor(outcome: Any) -> float:
    """Exceptional execution increases learning; failure teaches little."""
    key = _outcome_key(outcome)
    if key not in _PERFORMANCE_TABLE:
        raise ValueError(f"Unknown outcome: {outcome!r}")
    return _PERFORMANCE_TABLE[key]


#: Novelty multipliers by repetition count of the same trivial task.
#: 8 XP base -> 8, 5, 3, 2, 1, 0 (see novelty.py tracker).
NOVELTY_DECAY: list[float] = [1.0, 0.625, 0.375, 0.25, 0.125, 0.0]


def novelty_factor(repetitions: int) -> float:
    """How much learning remains after ``repetitions`` repeats (0 = fresh)."""
    if repetitions <= 0:
        return NOVELTY_DECAY[0]
    if repetitions >= len(NOVELTY_DECAY):
        return 0.0
    return NOVELTY_DECAY[repetitions]


def training_modifier(
    instructor: float = 0.0,
    equipment: float = 0.0,
    books: float = 0.0,
    environment: float = 0.0,
    traits: float = 0.0,
) -> float:
    """Teachers, books, equipment, environments and traits shift learning."""
    return max(0.2, 1.0 + instructor + equipment + books + environment + traits)


def compute_skill_xp(
    challenge: int,
    outcome: Any,
    repetitions: int = 0,
    instructor: float = 0.0,
    equipment: float = 0.0,
    books: float = 0.0,
    environment: float = 0.0,
    traits: float = 0.0,
) -> int:
    """Full formula, rounded down to whole XP."""
    xp = (
        challenge_factor(challenge)
        * performance_factor(outcome)
        * novelty_factor(repetitions)
        * training_modifier(instructor, equipment, books, environment, traits)
    )
    return max(0, int(xp))
