"""History HTTP surface: event summary + achievements catalogue."""
from __future__ import annotations

from fastapi import APIRouter

from app.modules.history.achievements import ACHIEVEMENTS
from app.modules.history.store import HistoryStore

router = APIRouter(prefix="/history", tags=["history"])

_store = HistoryStore()


@router.get("/summary")
def summary(campaign_id: str = "default") -> dict:
    return _store.summarize_campaign(campaign_id)


@router.get("/achievements")
def achievements() -> dict:
    return {"achievements": ACHIEVEMENTS}
