"""Authored vertical-slice + Ravenford seed content (Stream G).

Self-contained: stdlib + pydantic-free dataclasses only, so fixture and
slice tests pass while other streams' modules are still landing.
"""

from app.content.fixture import (  # noqa: F401  (re-exported API)
    SCRIPTED_FLOWS,
    FixtureWorld,
    NarrativeResult,
    new_fixture_world,
)
from app.content.ravenford import (  # noqa: F401
    FACTIONS,
    LOCATIONS,
    NPCS,
    OPENING_SCENE,
    RUMOURS,
    THREADS,
)
from app.content.skills import SKILLS  # noqa: F401
from app.content.slice import (  # noqa: F401
    COMBAT_ENCOUNTER,
    INVENTORY_SCREEN,
    LEADS_SCREEN,
    MYSTERY,
    SESSION_BEATS,
    get_slice,
)
