"""Shared combat primitives: range bands, cover, combatants, combat state.

NL-action compatible: positions are free text, ranges are abstract bands, and
free-text turns are classified against this state (see turns.py / environment.py).
"""
from __future__ import annotations

import uuid
from enum import Enum
from typing import Any

from pydantic import BaseModel, Field


class RangeBand(str, Enum):
    ENGAGED = "Engaged"
    NEAR = "Near"
    FAR = "Far"
    DISTANT = "Distant"

    def order(self) -> int:
        return [RangeBand.ENGAGED, RangeBand.NEAR, RangeBand.FAR, RangeBand.DISTANT].index(self)

    def closer(self) -> RangeBand:
        bands = [RangeBand.ENGAGED, RangeBand.NEAR, RangeBand.FAR, RangeBand.DISTANT]
        return bands[max(0, self.order() - 1)]

    def farther(self) -> RangeBand:
        bands = [RangeBand.ENGAGED, RangeBand.NEAR, RangeBand.FAR, RangeBand.DISTANT]
        return bands[min(3, self.order() + 1)]


class Cover(str, Enum):
    NONE = "none"
    HALF = "half"
    FULL = "full"

    def ranged_bonus(self) -> int:
        return {Cover.NONE: 0, Cover.HALF: 2, Cover.FULL: 5}[self]


MELEE_BANDS = frozenset({RangeBand.ENGAGED})
RANGED_BANDS = frozenset({RangeBand.NEAR, RangeBand.FAR})
# Distant targets cannot be hit at all until someone closes the distance.


class Weapon(BaseModel):
    name: str = "Shortsword"
    damage: int = 6
    heavy: bool = False
    # Farthest band this weapon can reach (melee weapons reach Engaged only).
    reach: RangeBand = RangeBand.ENGAGED
    ranged: bool = False


class Armour(BaseModel):
    name: str = "Cloth"
    reduction: int = 0


class EnemyIntent(BaseModel):
    enemy_id: str
    kind: str = "attack"  # attack | aim | cast | charge | flee | guard
    target_id: str | None = None

    def describe(self, enemy_name: str, target_name: str | None) -> str:
        verbs = {
            "attack": "attacking",
            "aim": "aiming at",
            "cast": "casting at",
            "charge": "charging",
            "flee": "fleeing from",
            "guard": "guarding",
        }
        verb = verbs.get(self.kind, self.kind)
        if target_name:
            return f"{enemy_name} — {verb} {target_name}"
        return f"{enemy_name} — {verb}"


class EnvFeature(BaseModel):
    """An interactable object in the fight space (chandelier, rope, barrel...)."""

    id: str = Field(default_factory=lambda: uuid.uuid4().hex[:8])
    name: str
    aliases: list[str] = Field(default_factory=list)
    description: str = ""
    broken: bool = False
    effect_damage: int = 0
    # Combatant ids caught in the effect zone (e.g. standing under the chandelier).
    linked_targets: list[str] = Field(default_factory=list)

    def matches(self, text: str) -> bool:
        lowered = text.lower()
        if self.name.lower() in lowered:
            return True
        return any(a.lower() in lowered for a in self.aliases)


class Combatant(BaseModel):
    id: str = Field(default_factory=lambda: uuid.uuid4().hex[:8])
    name: str
    is_enemy: bool = False
    max_hp: int = 30
    hp: int = 30
    max_stamina: int = 100
    stamina: int = 100
    position: str = "in the fray"
    cover: Cover = Cover.NONE
    conditions: list[str] = Field(default_factory=list)
    weapon: Weapon = Field(default_factory=Weapon)
    armour: Armour = Field(default_factory=Armour)
    intent: EnemyIntent | None = None
    injuries: list[str] = Field(default_factory=list)
    # Status: active | incapacitated | stable | dead
    status: str = "active"
    death_save_fails: int = 0
    death_save_successes: int = 0
    # Stamina anti-spam bookkeeping.
    last_primary_kind: str | None = None
    repeat_count: int = 0
    exhausted: bool = False

    @property
    def alive(self) -> bool:
        return self.status not in ("dead",)

    @property
    def can_act(self) -> bool:
        return self.status == "active"


class CombatState(BaseModel):
    id: str = Field(default_factory=lambda: uuid.uuid4().hex[:8])
    campaign_id: str = "default"
    mode: str = "standard"  # story | standard | hardcore | iron
    round_no: int = 1
    combatants: dict[str, Combatant] = Field(default_factory=dict)
    # Pairwise abstract distance, keyed "a_id<b_id" (sorted ids).
    ranges: dict[str, RangeBand] = Field(default_factory=dict)
    env: list[EnvFeature] = Field(default_factory=list)
    over: bool = False

    # -- ranges ---------------------------------------------------------
    @staticmethod
    def range_key(a: str, b: str) -> str:
        first, second = sorted((a, b))
        return f"{first}<{second}"

    def get_range(self, a: str, b: str) -> RangeBand:
        if a == b:
            return RangeBand.ENGAGED
        return self.ranges.get(self.range_key(a, b), RangeBand.NEAR)

    def set_range(self, a: str, b: str, band: RangeBand) -> None:
        self.ranges[self.range_key(a, b)] = band

    def close_distance(self, a: str, b: str, steps: int = 1) -> RangeBand:
        band = self.get_range(a, b)
        for _ in range(steps):
            band = band.closer()
        self.set_range(a, b, band)
        return band

    def find_env(self, text: str) -> EnvFeature | None:
        for feature in self.env:
            if not feature.broken and feature.matches(text):
                return feature
        return None

    def living_enemies_of(self, combatant_id: str) -> list[Combatant]:
        me = self.combatants[combatant_id]
        return [c for c in self.combatants.values() if c.is_enemy != me.is_enemy and c.alive]

    def to_snapshot(self) -> dict[str, Any]:
        return self.model_dump()
