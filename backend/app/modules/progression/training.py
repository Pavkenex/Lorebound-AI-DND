"""Deliberate practice, instructors, training ceilings (GDD §15)."""
from __future__ import annotations

import random
from dataclasses import dataclass

from app.modules.progression.skills import MasteryTier, SkillProgress, tier_for_xp


@dataclass(frozen=True)
class Instructor:
    name: str
    ceiling: MasteryTier  # highest tier this instructor can teach into
    bonus: float = 0.0  # training-modifier bonus while below the ceiling


#: Canonical instructors: a village guard teaches to Competent;
#: a famous swordmaster unlocks Expert-tier techniques.
INSTRUCTORS: dict[str, Instructor] = {
    "village guard": Instructor("village guard", MasteryTier.COMPETENT, bonus=0.1),
    "swordmaster": Instructor("swordmaster", MasteryTier.EXPERT, bonus=0.4),
    "master-at-arms": Instructor("master-at-arms", MasteryTier.EXPERT, bonus=0.3),
    "self-taught": Instructor("self-taught", MasteryTier.SKILLED, bonus=0.0),
}

MIN_PRACTICE_HOURS = 1
MAX_PRACTICE_HOURS = 4
FATIGUE_PER_HOUR = 10


@dataclass
class PracticeResult:
    skill: str
    hours: int
    xp_gained: int
    fatigue_gained: int
    injury: bool
    minutes_elapsed: int
    capped: bool
    note: str


def _tier_at_or_above(tier: MasteryTier, ceiling: MasteryTier, order: list[MasteryTier]) -> bool:
    return order.index(tier) >= order.index(ceiling)


def practice(
    progress: SkillProgress,
    hours: int,
    instructor: Instructor | str | None = None,
    equipment_bonus: float = 0.0,
    location_bonus: float = 0.0,
    current_fatigue: int = 0,
    roll: float | None = None,
    clock: object | None = None,
) -> PracticeResult:
    """Run a deliberate practice session ('Practice Swordsmanship — 2 hours').

    Yields skill progress, fatigue, a small injury chance, and elapsed world
    time (advanced on the authoritative engine clock only).
    """
    if not (MIN_PRACTICE_HOURS <= hours <= MAX_PRACTICE_HOURS):
        raise ValueError(f"Practice must be {MIN_PRACTICE_HOURS}-{MAX_PRACTICE_HOURS} hours")
    if isinstance(instructor, str):
        try:
            instructor = INSTRUCTORS[instructor]
        except KeyError:
            raise ValueError(f"Unknown instructor: {instructor!r}") from None

    order = [t for t in MasteryTier]
    current_tier = tier_for_xp(progress.xp)
    if instructor is not None and _tier_at_or_above(current_tier, instructor.ceiling, order):
        return PracticeResult(
            skill=progress.name,
            hours=hours,
            xp_gained=0,
            fatigue_gained=hours * FATIGUE_PER_HOUR,
            injury=False,
            minutes_elapsed=hours * 60,
            capped=True,
            note=f"{instructor.name} cannot teach beyond {instructor.ceiling.value}.",
        )

    bonus = (instructor.bonus if instructor else 0.0) + equipment_bonus + location_bonus
    # Base practice XP scales with hours; challenge 3 (drills), partial outcome.
    from app.modules.progression.xp import compute_skill_xp

    xp_gained = compute_skill_xp(3, "partial", instructor=bonus) * hours
    progress.add_xp(xp_gained, f"Practice ({hours}h) +{xp_gained}")

    fatigue_gained = hours * FATIGUE_PER_HOUR
    total_fatigue = current_fatigue + fatigue_gained
    injury_p = 0.02 * hours + total_fatigue / 2000.0
    r = roll if roll is not None else random.random()
    injury = r < injury_p

    minutes = hours * 60
    if clock is not None:
        advance = getattr(clock, "advance_minutes", None)
        if advance is not None:
            advance(minutes, actor="engine", reason=f"training {progress.name}")

    return PracticeResult(
        skill=progress.name,
        hours=hours,
        xp_gained=xp_gained,
        fatigue_gained=fatigue_gained,
        injury=injury,
        minutes_elapsed=minutes,
        capped=False,
        note="Session complete.",
    )
