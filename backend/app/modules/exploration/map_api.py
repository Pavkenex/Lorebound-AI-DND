"""Map screen data API (GDD §75).

Fantasy travel map data: known locations, roads, danger zones, undiscovered
areas, rumours, travel time, active incidents. Some information is
intentionally inaccurate until confirmed.
"""
from __future__ import annotations

import hashlib

from app.modules.exploration.regions import Region


def _jitter(key: str, scale: int = 3) -> tuple[int, int]:
    """Deterministic pseudo-inaccuracy for unconfirmed positions."""
    h = int(hashlib.md5(key.encode()).hexdigest()[:8], 16)
    return (h % (2 * scale + 1)) - scale, ((h >> 8) % (2 * scale + 1)) - scale


# Fixed canonical positions (valley grid).
POSITIONS: dict[str, tuple[int, int]] = {
    "ravenford": (0, 0), "lantern-inn": (0, 0), "ember-hollow": (-6, 3),
    "millbrook": (5, 2), "graywood": (-3, -5), "old-monastery": (-8, -2),
    "abandoned-mine": (7, -4), "silt-ford": (2, 5), "old-watchtower": (4, -1),
    "fox-den-hollow": (-4, 1), "barrow-field": (1, -6),
}

ROADS: list[tuple[str, str]] = [
    ("ravenford", "ember-hollow"), ("ravenford", "millbrook"),
    ("ravenford", "silt-ford"), ("millbrook", "silt-ford"),
    ("ember-hollow", "old-monastery"), ("ravenford", "old-watchtower"),
]


def get_map_data(
    region: Region,
    known_ids: set[str],
    confirmed_ids: set[str],
    rumours: list[dict] | None = None,
    incidents: list[dict] | None = None,
    danger_zones: list[dict] | None = None,
) -> dict:
    """Map payload. Unconfirmed entries carry accuracy='approximate' and a
    jittered position; confirmed ones are exact."""
    locations = []
    for loc in region.locations:
        if loc.id not in known_ids:
            continue
        confirmed = loc.id in confirmed_ids
        x, y = POSITIONS.get(loc.id, (0, 0))
        if not confirmed:
            dx, dy = _jitter(loc.id)
            x, y = x + dx, y + dy
        locations.append({
            "id": loc.id, "name": loc.name, "type": loc.type,
            "x": x, "y": y,
            "accuracy": "confirmed" if confirmed else "approximate",
        })
    undiscovered = sum(1 for loc in region.locations if loc.id not in known_ids)
    return {
        "region": region.name,
        "locations": locations,
        "roads": [{"from": a, "to": b} for a, b in ROADS],
        "danger_zones": list(danger_zones or []),
        "undiscovered_areas": undiscovered,
        "rumours": list(rumours or []),
        "incidents": list(incidents or []),
    }
