"""Weight capacity + encumbrance states (Stream A).

Broad-brush: total carried weight vs a Might-based budget. Four states —
Light / Normal / Heavy / Overloaded — influencing movement and stamina.
"""
from __future__ import annotations

from app.modules.character.attributes import attribute_modifier

EncumbranceState = str  # "Light" | "Normal" | "Heavy" | "Overloaded"

LIGHT_RATIO = 0.35
NORMAL_RATIO = 0.70
HEAVY_RATIO = 1.00


def carrying_capacity(might: int) -> float:
    """Weight budget in abstract units: 20 + Might mod * 5, minimum 5."""
    return max(5.0, 20.0 + attribute_modifier(might) * 5.0)


def total_weight(entries: list[tuple[float, int]]) -> float:
    """Sum of (unit_weight, quantity); containers count at face value (broad-brush)."""
    return sum(float(w) * int(q) for w, q in entries)


def encumbrance_state(carried: float, capacity: float) -> EncumbranceState:
    if capacity <= 0:
        return "Overloaded"
    ratio = carried / capacity
    if ratio <= LIGHT_RATIO:
        return "Light"
    if ratio <= NORMAL_RATIO:
        return "Normal"
    if ratio <= HEAVY_RATIO:
        return "Heavy"
    return "Overloaded"


MOVEMENT_FACTOR: dict[str, float] = {"Light": 1.1, "Normal": 1.0, "Heavy": 0.75, "Overloaded": 0.4}
STAMINA_DRAIN: dict[str, int] = {"Light": 0, "Normal": 0, "Heavy": 1, "Overloaded": 3}


def encumbrance_effects(state: EncumbranceState) -> dict[str, float | int]:
    return {"movement_factor": MOVEMENT_FACTOR[state], "extra_stamina_per_travel": STAMINA_DRAIN[state]}


def summarize(carried: float, might: int) -> dict:
    capacity = carrying_capacity(might)
    state = encumbrance_state(carried, capacity)
    return {"carried": carried, "capacity": capacity, "state": state, **encumbrance_effects(state)}
