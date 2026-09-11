"""Discovery of hidden locations (GDD §47).

Hidden caves, shrines, ruins, camps and shortcuts are found via exploration,
maps, rumours, NPC directions, tracking, high Awareness, or quests. At least
one major location is reachable accidentally ('Wrong Door' achievement).
"""
from __future__ import annotations

import random
from dataclasses import dataclass, field
from enum import Enum


class DiscoveryMethod(str, Enum):
    EXPLORE = "explore"
    MAP = "map"
    RUMOUR = "rumour"
    NPC = "npc"
    TRACK = "track"
    AWARENESS = "awareness"
    QUEST = "quest"
    ACCIDENT = "accident"


@dataclass
class HiddenLocation:
    id: str
    name: str
    major: bool
    awareness_dc: int  # Awareness needed to notice deliberately
    accidental_p: float  # chance to blunder in even with low Awareness
    methods: list[DiscoveryMethod] = field(default_factory=list)


HIDDEN: list[HiddenLocation] = [
    HiddenLocation("sunken-chapel", "Sunken Chapel", major=True, awareness_dc=16,
                   accidental_p=0.06,
                   methods=[DiscoveryMethod.EXPLORE, DiscoveryMethod.RUMOUR,
                            DiscoveryMethod.QUEST, DiscoveryMethod.ACCIDENT]),
    HiddenLocation("smuggler-cove", "Smuggler's Cove", major=False, awareness_dc=13,
                   accidental_p=0.02,
                   methods=[DiscoveryMethod.MAP, DiscoveryMethod.NPC, DiscoveryMethod.TRACK]),
    HiddenLocation("hermit-shrine", "Hermit's Shrine", major=False, awareness_dc=12,
                   accidental_p=0.03,
                   methods=[DiscoveryMethod.EXPLORE, DiscoveryMethod.AWARENESS, DiscoveryMethod.NPC]),
    HiddenLocation("old-shortcut", "Deer-Run Shortcut", major=False, awareness_dc=10,
                   accidental_p=0.05,
                   methods=[DiscoveryMethod.TRACK, DiscoveryMethod.EXPLORE]),
]


@dataclass
class DiscoveryAttempt:
    location_id: str
    found: bool
    via: DiscoveryMethod | None
    accidental: bool = False


def attempt_discovery(
    loc: HiddenLocation,
    awareness: int,
    methods_used: list[DiscoveryMethod],
    travel_discovery_bonus: int = 0,
    roll: float | None = None,
    accident_roll: float | None = None,
) -> DiscoveryAttempt:
    """Deliberate discovery via methods/Awareness, plus an accidental chance.

    ``roll``/``accident_roll`` inject determinism for tests.
    """
    rng = random.Random()
    # Accidental discovery: always possible, even with low Awareness.
    accident = accident_roll if accident_roll is not None else rng.random()
    if accident < loc.accidental_p:
        return DiscoveryAttempt(loc.id, True, DiscoveryMethod.ACCIDENT, accidental=True)
    if not methods_used:
        return DiscoveryAttempt(loc.id, False, None)
    effective = awareness + travel_discovery_bonus + (2 if DiscoveryMethod.MAP in methods_used else 0)
    r = roll if roll is not None else rng.random()
    # d20-style: succeed if effective + d20(1..20)*0.5 >= dc
    check = effective + (1 + r * 19) * 0.5
    if check >= loc.awareness_dc:
        return DiscoveryAttempt(loc.id, True, methods_used[0])
    return DiscoveryAttempt(loc.id, False, None)
