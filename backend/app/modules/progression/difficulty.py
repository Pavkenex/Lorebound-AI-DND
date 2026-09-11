"""Difficulty presets (GDD §86).

Difficulty changes mechanical forgiveness far more than AI hostility:
hostility is constant across presets.
"""
from __future__ import annotations

from dataclasses import dataclass
from enum import Enum


class DifficultyName(str, Enum):
    STORY = "Story"
    ADVENTURER = "Adventurer"
    VETERAN = "Veteran"
    IRON_CHRONICLE = "Iron Chronicle"


#: AI hostility never moves with difficulty. Fixed by design.
AI_HOSTILITY = 1.0


@dataclass(frozen=True)
class DifficultyPreset:
    name: DifficultyName
    check_bonus: int  # added to the player's checks (forgiveness)
    damage_taken_mult: float  # incoming damage multiplier (forgiveness)
    resource_scarcity: float  # upkeep/cost multiplier
    death_forgiveness: bool  # survive what would otherwise kill
    permadeath: bool
    save_limit: int | None  # None = unlimited saves
    economy_harshness: float


PRESETS: dict[DifficultyName, DifficultyPreset] = {
    DifficultyName.STORY: DifficultyPreset(
        DifficultyName.STORY, check_bonus=3, damage_taken_mult=0.6,
        resource_scarcity=0.7, death_forgiveness=True, permadeath=False,
        save_limit=None, economy_harshness=0.7,
    ),
    DifficultyName.ADVENTURER: DifficultyPreset(
        DifficultyName.ADVENTURER, check_bonus=0, damage_taken_mult=1.0,
        resource_scarcity=1.0, death_forgiveness=False, permadeath=False,
        save_limit=None, economy_harshness=1.0,
    ),
    DifficultyName.VETERAN: DifficultyPreset(
        DifficultyName.VETERAN, check_bonus=-1, damage_taken_mult=1.25,
        resource_scarcity=1.4, death_forgiveness=False, permadeath=False,
        save_limit=None, economy_harshness=1.4,
    ),
    DifficultyName.IRON_CHRONICLE: DifficultyPreset(
        DifficultyName.IRON_CHRONICLE, check_bonus=-2, damage_taken_mult=1.5,
        resource_scarcity=1.8, death_forgiveness=False, permadeath=True,
        save_limit=1, economy_harshness=1.8,
    ),
}


def get_preset(name: DifficultyName | str) -> DifficultyPreset:
    if isinstance(name, str):
        name = DifficultyName(name)
    return PRESETS[name]


def ai_hostility(_name: DifficultyName | str) -> float:
    """Hostility is identical on every preset — difficulty forgives, AI doesn't hate."""
    return AI_HOSTILITY
