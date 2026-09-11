"""Economy HTTP surface: upkeep preview."""
from __future__ import annotations

from fastapi import APIRouter

from app.modules.economy.economy import SINKS, daily_upkeep

router = APIRouter(prefix="/economy", tags=["economy"])


@router.get("/upkeep")
def upkeep(party_size: int = 1, wealth: int = 0) -> dict:
    return {
        "daily_upkeep": daily_upkeep(party_size, wealth=wealth),
        "sinks": [s.name for s in SINKS],
    }
