"""Live play HTTP surface: engine-backed actions.

- POST /act {text, campaign_id?, seed_roll?} -> engine resolves one action and
  returns the frontend contract: {ack, mechanics, narration, dialogue, newLeads}.
- Idempotency-Key replays return the stored response without re-applying effects.
- X-Content-Prefs (JSON) bounds narration tone (violence/horror/romance/nsfw).
"""
from __future__ import annotations

import json

from fastapi import APIRouter, Depends, HTTPException, Request, status
from pydantic import BaseModel, Field
from sqlalchemy.orm import Session

from app.core.database import get_db
from app.modules.auth.deps import require_user, scope_campaign
from app.modules.auth.models import User
from app.modules.campaign.models import Campaign
from app.modules.narrator.prefs import ContentPrefs
from app.modules.play.engine import ActEngine
from app.modules.play.session import PlaySession

router = APIRouter(tags=["play"])


class ActIn(BaseModel):
    text: str = Field(min_length=1, max_length=2000)
    campaign_id: str | None = None
    #: Test hook (same convention as ActionInput): deterministic first d20.
    seed_roll: int | None = Field(default=None, ge=1, le=20)


def _resolve_campaign(db: Session, user: User, campaign_id: str | None) -> Campaign:
    if campaign_id:
        return scope_campaign(db, user, campaign_id)
    campaign = (
        db.query(Campaign)
        .filter(Campaign.owner_user_id == user.id, Campaign.status == "active")
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


@router.post("/act")
def act(
    body: ActIn,
    request: Request,
    user: User = Depends(require_user),
    db: Session = Depends(get_db),
) -> dict:
    campaign = _resolve_campaign(db, user, body.campaign_id)
    key = request.headers.get("Idempotency-Key")

    session = PlaySession.load(db, campaign.id)
    cached = session.recall_action(db, key)
    if cached is not None:
        return cached

    engine = ActEngine(session, prefs=_parse_prefs(request.headers.get("X-Content-Prefs")))
    response, checkpoint = engine.act(body.text, seed_roll=body.seed_roll)

    # Resolved state is committed (and checkpointed) before the response leaves,
    # so a dropped connection can never lose a resolved action.
    session.checkpoint(db, checkpoint or "")
    session.remember_action(db, key, response)
    return response
