"""Per-location mutable state, secrets, opening hours (GDD §40).

The narrator receives structured location data rather than guessing the layout.
"""
from __future__ import annotations

from dataclasses import dataclass, field

from app.modules.exploration.regions import Location


@dataclass
class LocationState:
    location_id: str
    flags: dict[str, object] = field(default_factory=dict)
    secrets_revealed: set[str] = field(default_factory=set)
    occupants_present: list[str] = field(default_factory=list)

    def set_flag(self, key: str, value: object) -> None:
        self.flags[key] = value

    def reveal_secret(self, secret: str) -> None:
        self.secrets_revealed.add(secret)

    def visible_secrets(self, location: Location) -> list[str]:
        return [s for s in location.secrets if s in self.secrets_revealed]


def is_open(location: Location, hour: int) -> bool:
    """Time-gated access. Hours may wrap midnight, e.g. Lantern 06:00-02:00."""
    if location.open_hours is None:
        return True
    open_h, close_h = location.open_hours
    hour = hour % 24
    close = close_h % 24
    if close <= open_h:  # wraps midnight
        return hour >= open_h or hour < close
    return open_h <= hour < close


def narrator_brief(location: Location, state: LocationState, hour: int) -> dict:
    """Structured location data for the narrator — no layout guessing.

    Unrevealed secrets are never included.
    """
    return {
        "id": location.id,
        "name": location.name,
        "type": location.type,
        "open_now": is_open(location, hour),
        "npcs_present": [n for n in location.npcs if n in state.occupants_present] or list(location.npcs),
        "features": list(location.features),
        "known_secrets": state.visible_secrets(location),
        "flags": dict(state.flags),
    }
