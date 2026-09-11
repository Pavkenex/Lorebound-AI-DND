"""Travel modes, fatigue, encounters, resources (GDD §46).

Travel normally / cautiously / quickly, plus search area, forage, hunt,
track, camp. Cautious travel is a genuine trade-off, not a worse option:
slower, but safer, cheaper on supplies, and better at discovery.
"""
from __future__ import annotations

from dataclasses import dataclass
from enum import Enum


class TravelMode(str, Enum):
    NORMAL = "normal"
    CAUTIOUS = "cautious"
    QUICK = "quick"


BASE_MIN_PER_KM = 12


@dataclass(frozen=True)
class TravelPlan:
    mode: TravelMode
    km: float
    time_min: int
    fatigue: int
    encounter_chance: float
    supply_use: int
    discovery_bonus: int


MODE_TABLE: dict[TravelMode, dict[str, float]] = {
    TravelMode.NORMAL: {"time": 1.0, "fatigue_km": 2.0, "encounter": 0.15, "supply_km": 1.0, "discovery": 0},
    TravelMode.CAUTIOUS: {"time": 1.5, "fatigue_km": 1.0, "encounter": 0.06, "supply_km": 0.5, "discovery": 3},
    TravelMode.QUICK: {"time": 0.7, "fatigue_km": 4.0, "encounter": 0.22, "supply_km": 1.5, "discovery": -2},
}


def plan_travel(km: float, mode: TravelMode | str = TravelMode.NORMAL) -> TravelPlan:
    if isinstance(mode, str):
        mode = TravelMode(mode)
    if km <= 0:
        raise ValueError("Distance must be positive")
    t = MODE_TABLE[mode]
    return TravelPlan(
        mode=mode,
        km=km,
        time_min=int(km * BASE_MIN_PER_KM * t["time"]),
        fatigue=int(km * t["fatigue_km"]),
        encounter_chance=t["encounter"],
        supply_use=int(km * t["supply_km"]),
        discovery_bonus=int(t["discovery"]),
    )


@dataclass(frozen=True)
class FieldActionResult:
    action: str
    minutes: int
    fatigue: int
    yield_note: str


def forage(party_size: int = 1) -> FieldActionResult:
    return FieldActionResult("forage", 45, 3, f"greens and roots for ~{party_size} meal(s)")


def hunt() -> FieldActionResult:
    return FieldActionResult("hunt", 120, 8, "game on a successful Survival check")


def track() -> FieldActionResult:
    return FieldActionResult("track", 30, 2, "trail reading; may reveal hidden routes")


def camp(sheltered: bool = False) -> FieldActionResult:
    return FieldActionResult("camp", 60, -15 if sheltered else -8, "rest; sheltered camps recover more")
