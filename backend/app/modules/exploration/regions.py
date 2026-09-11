"""Region/location model & hierarchy (GDD §39-40) + Ravenford Valley set.

Region -> Location -> Scene -> Event. Locations carry type, parent, NPCs,
features, secrets, open hours.
"""
from __future__ import annotations

from dataclasses import dataclass, field


@dataclass
class GameScene:
    id: str
    name: str
    description: str


@dataclass
class Location:
    id: str
    name: str
    type: str  # town | village | forest | ruin | mine | crossing | tower | wild
    parent_region: str
    npcs: list[str] = field(default_factory=list)
    features: list[str] = field(default_factory=list)
    secrets: list[str] = field(default_factory=list)
    open_hours: tuple[int, int] | None = None  # (open, close) 24h; close may wrap past midnight
    hidden: bool = False
    scenes: list[GameScene] = field(default_factory=list)


@dataclass
class Region:
    id: str
    name: str
    description: str
    locations: list[Location] = field(default_factory=list)

    def by_id(self, location_id: str) -> Location:
        for loc in self.locations:
            if loc.id == location_id:
                return loc
        raise KeyError(f"Unknown location: {location_id!r}")


def get_ravenford_valley() -> Region:
    """MVP region: main town, two villages, forest, ruined monastery,
    abandoned mine, river crossing, old watchtower, plus wilderness spots."""
    return Region(
        id="ravenford-valley",
        name="Ravenford Valley",
        description="A frontier valley of farms, old woods and older ruins.",
        locations=[
            Location("ravenford", "Ravenford", "town", "ravenford-valley",
                     npcs=["Mayor Selka", "Marla the smith"],
                     features=["market square", "Lantern Inn", "militia hall"],
                     scenes=[GameScene("lantern-common", "Lantern Common Room",
                                       "Smoke, stew and low talk.")]),
            Location("ember-hollow", "Ember Hollow", "village", "ravenford-valley",
                     npcs=["Elder Bram"], features=["charcoal kilns", "chapel"]),
            Location("millbrook", "Millbrook", "village", "ravenford-valley",
                     npcs=["Miller Dova"], features=["watermill", "grain stores"]),
            Location("graywood", "Graywood Forest", "forest", "ravenford-valley",
                     features=["old game trails", "wolf dens", "hunter camp"]),
            Location("old-monastery", "Ruined Monastery", "ruin", "ravenford-valley",
                     features=["broken cloister", "crypt door"],
                     secrets=["crypt passage to the Sunken Chapel"]),
            Location("abandoned-mine", "Abandoned Mine", "mine", "ravenford-valley",
                     features=["flooded shaft", "old assay office"],
                     secrets=["smuggler cache"]),
            Location("silt-ford", "Silt Ford", "crossing", "ravenford-valley",
                     features=["ferry rope", "ford stones"]),
            Location("old-watchtower", "Old Watchtower", "tower", "ravenford-valley",
                     features=["signal brazier", "collapsed stair"]),
            Location("lantern-inn", "Lantern Inn", "town", "ravenford-valley",
                     npcs=["Innkeep Tess"], features=["common room", "cellar"],
                     secrets=["smuggler entrance behind the cellar casks"],
                     open_hours=(6, 26)),  # 06:00-02:00 (wraps midnight)
            Location("fox-den-hollow", "Fox Den Hollow", "wild", "ravenford-valley",
                     features=["briar thicket", "spring"]),
            Location("barrow-field", "Barrow Field", "wild", "ravenford-valley",
                     features=["old barrows", "standing stones"]),
        ],
    )
