"""Content router: read-only JSON for inventory / lead screens and slice data."""

from __future__ import annotations

from dataclasses import asdict

try:
    from fastapi import APIRouter
except Exception:  # pragma: no cover - fastapi always present in backend
    APIRouter = None  # type: ignore

from app.content.fixture import ARIC, LANTERN_INN, MARLA, MISSING_TRAVELERS_LEAD
from app.content.ravenford import (
    FACTIONS,
    LOCATIONS,
    NPCS,
    OPENING_SCENE,
    RUMOURS,
    THREADS,
)
from app.content.skills import SKILLS
from app.content.slice import (
    COMBAT_ENCOUNTER,
    INVENTORY_SCREEN,
    LEADS_SCREEN,
    MYSTERY,
    SESSION_BEATS,
    SESSION_MINUTES,
)

router = APIRouter(prefix="/content", tags=["content"]) if APIRouter else None

if router is not None:

    @router.get("/fixture")
    def fixture() -> dict:
        return {"pc": ARIC, "tavern": LANTERN_INN, "npc": MARLA,
                "lead": MISSING_TRAVELERS_LEAD}

    @router.get("/slice")
    def vertical_slice() -> dict:
        return {
            "pc": ARIC,
            "tavern": LANTERN_INN,
            "mystery": MYSTERY,
            "combat": COMBAT_ENCOUNTER,
            "skills": [asdict(s) for s in SKILLS],
            "session_minutes": SESSION_MINUTES,
            "beats": [asdict(b) for b in SESSION_BEATS],
        }

    @router.get("/ravenford")
    def ravenford() -> dict:
        return {
            "opening": OPENING_SCENE,
            "locations": [asdict(l) for l in LOCATIONS],
            "npcs": [asdict(n) for n in NPCS],
            "factions": [asdict(f) for f in FACTIONS],
            "threads": [asdict(t) for t in THREADS],
            "rumours": list(RUMOURS),
        }

    @router.get("/inventory-screen")
    def inventory_screen() -> dict:
        return dict(INVENTORY_SCREEN)

    @router.get("/leads-screen")
    def leads_screen() -> dict:
        return dict(LEADS_SCREEN)
