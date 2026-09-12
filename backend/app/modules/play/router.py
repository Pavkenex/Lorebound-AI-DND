"""Live play HTTP surface: engine-backed actions.

- POST /act {text, campaign_id?, seed_roll?} -> engine resolves one action and
  returns the frontend contract: {ack, mechanics, narration, dialogue, newLeads}.
- Idempotency-Key replays return the stored response without re-applying effects.
- X-Content-Prefs (JSON) bounds narration tone (violence/horror/romance/nsfw).
"""
from __future__ import annotations

import json

from fastapi import APIRouter, Depends, HTTPException, Request, Response, status
from pydantic import BaseModel, Field
from sqlalchemy.orm import Session

from app.core.database import get_db
from app.modules.auth.deps import require_user, scope_campaign
from app.modules.auth.models import User
from app.modules.campaign.models import Campaign
from app.modules.narrator.prefs import ContentPrefs
from app.modules.play import screens
from app.modules.play.engine import ActEngine
from app.modules.play.session import PlaySession
from app.modules.play.view import game_state_payload

router = APIRouter(tags=["play"])


class ActIn(BaseModel):
    text: str = Field(min_length=1, max_length=2000)
    campaign_id: str | None = None
    #: Test hook (same convention as ActionInput): deterministic first d20.
    seed_roll: int | None = Field(default=None, ge=1, le=20)


def _resolve_campaign(db: Session, user: User, campaign_id: str | None) -> Campaign:
    if campaign_id:
        return scope_campaign(db, user, campaign_id)
    # Prefer the most recent active campaign; after the arc completes the
    # campaign turns "completed" and free play continues there.
    campaign = (
        db.query(Campaign)
        .filter(Campaign.owner_user_id == user.id, Campaign.status == "active")
        .order_by(Campaign.created_at.desc())
        .first()
    )
    if campaign is None:
        campaign = (
            db.query(Campaign)
            .filter(Campaign.owner_user_id == user.id, Campaign.status != "archived")
            .order_by(Campaign.created_at.desc())
            .first()
        )
    if campaign is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="no campaign yet — create one first",
        )
    return campaign


def _parse_prefs(raw: str | None) -> ContentPrefs | None:
    if not raw:
        return None
    try:
        data = json.loads(raw)
    except (TypeError, ValueError):
        return None
    if not isinstance(data, dict):
        return None
    try:
        return ContentPrefs(**data)
    except Exception:  # noqa: BLE001 - malformed prefs fall back to defaults
        return None


@router.get("/state")
def game_state(
    campaign_id: str | None = None,
    user: User = Depends(require_user),
    db: Session = Depends(get_db),
) -> dict:
    """Live game state for the adventure screen (campaign-scoped, read-only)."""
    campaign = _resolve_campaign(db, user, campaign_id)
    session = PlaySession.load(db, campaign.id)
    return game_state_payload(session.state)


@router.get("/character")
def character_view(
    campaign_id: str | None = None,
    user: User = Depends(require_user),
    db: Session = Depends(get_db),
) -> dict:
    campaign = _resolve_campaign(db, user, campaign_id)
    session = PlaySession.load(db, campaign.id)
    return screens.character_payload(session.state)


@router.get("/skills")
def skills_view(
    campaign_id: str | None = None,
    user: User = Depends(require_user),
    db: Session = Depends(get_db),
) -> dict:
    campaign = _resolve_campaign(db, user, campaign_id)
    session = PlaySession.load(db, campaign.id)
    return screens.skills_payload(session.state)


@router.get("/journal")
def journal_view(
    campaign_id: str | None = None,
    user: User = Depends(require_user),
    db: Session = Depends(get_db),
) -> dict:
    campaign = _resolve_campaign(db, user, campaign_id)
    session = PlaySession.load(db, campaign.id)
    return screens.journal_payload(session.state)


@router.get("/map")
def map_view(
    campaign_id: str | None = None,
    user: User = Depends(require_user),
    db: Session = Depends(get_db),
) -> dict:
    campaign = _resolve_campaign(db, user, campaign_id)
    session = PlaySession.load(db, campaign.id)
    return screens.map_payload(session.state)


@router.get("/inventory")
def inventory_view(
    campaign_id: str | None = None,
    user: User = Depends(require_user),
    db: Session = Depends(get_db),
) -> list[dict]:
    campaign = _resolve_campaign(db, user, campaign_id)
    session = PlaySession.load(db, campaign.id)
    return screens.inventory_payload(session.state)


@router.get("/companions")
def companions_view(
    campaign_id: str | None = None,
    user: User = Depends(require_user),
    db: Session = Depends(get_db),
) -> list[dict]:
    campaign = _resolve_campaign(db, user, campaign_id)
    session = PlaySession.load(db, campaign.id)
    return screens.companions_payload(session.state)


@router.post("/act")
def act(
    body: ActIn,
    request: Request,
    response: Response,
    user: User = Depends(require_user),
    db: Session = Depends(get_db),
) -> dict:
    campaign = _resolve_campaign(db, user, body.campaign_id)
    key = request.headers.get("Idempotency-Key")

    session = PlaySession.load(db, campaign.id)
    cached = session.recall_action(db, key)
    if cached is not None:
        # A replayed action costs nothing and is served from the ledger.
        response.headers["x-ai-calls"] = "0"
        response.headers["x-ai-cost-usd"] = "0.000000"
        response.headers["x-cache"] = "hit"
        return cached

    engine = ActEngine(session, prefs=_parse_prefs(request.headers.get("X-Content-Prefs")))
    payload, checkpoint = engine.act(body.text, seed_roll=body.seed_roll)

    # Resolved state is committed (and checkpointed) before the response leaves,
    # so a dropped connection can never lose a resolved action.
    session.checkpoint(db, checkpoint or "")
    if session.state.completed and campaign.status != "completed":
        campaign.status = "completed"
        db.commit()
    session.remember_action(db, key, payload)

    # Live AI-cost truth (stub costs nothing, but the call count is real).
    report = engine.meter.report(campaign.id)
    response.headers["x-ai-calls"] = str(report["calls"])
    response.headers["x-ai-cost-usd"] = f"{report.get('cost_usd', 0.0):.6f}"
    response.headers["x-ai-cache"] = "miss"
    response.headers["x-cache"] = "miss"
    return payload
