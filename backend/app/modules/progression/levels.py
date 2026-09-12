"""Character level & unlock slots (GDD §18).

Display level is overall experience. It unlocks talent slots, advanced
specializations, Resolve capacity, and narrative opportunities — it never
scales the world's numbers.
"""
from __future__ import annotations

from dataclasses import dataclass

LEVEL_THRESHOLDS: list[int] = [0, 300, 800, 1500, 2500, 3800, 5400, 7300, 9500, 12000]
MAX_LEVEL = len(LEVEL_THRESHOLDS)

#: World power is a constant: level never scales the world's numbers.
WORLD_POWER_MULTIPLIER = 1.0


def level_for_total_xp(total_xp: int) -> int:
    level = 1
    for i, threshold in enumerate(LEVEL_THRESHOLDS, start=1):
        if total_xp >= threshold:
            level = i
    return min(level, MAX_LEVEL)


@dataclass(frozen=True)
class LevelUnlocks:
    level: int
    talent_slots: int
    advanced_specializations: bool
    resolve_capacity: int
    narrative_opportunities: list[str]


def unlocks_for_level(level: int) -> LevelUnlocks:
    level = max(1, min(level, MAX_LEVEL))
    opportunities: list[str] = []
    if level >= 2:
        opportunities.append("Local patrons seek reliable blades")
    if level >= 4:
        opportunities.append("Factions extend invitations")
    if level >= 6:
        opportunities.append("Legends open sealed doors")
    return LevelUnlocks(
        level=level,
        talent_slots=1 + (level - 1) // 2,
        advanced_specializations=level >= 4,
        resolve_capacity=2 + level // 2,
        narrative_opportunities=opportunities,
    )


def world_power(_level: int) -> float:
    """World numbers are identical at every level. Always 1.0."""
    return WORLD_POWER_MULTIPLIER


def assert_world_unscaled() -> bool:
    """Invariant check: world power is level-independent."""
    return all(world_power(lv) == 1.0 for lv in range(1, MAX_LEVEL + 1))
