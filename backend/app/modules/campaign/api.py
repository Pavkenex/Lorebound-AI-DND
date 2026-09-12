"""Campaign lifecycle HTTP surface: create + list (Stream: live play, owner-scoped).

- GET  /campaigns        -> list of the caller's campaigns (id, name, seed_key, status, created_at)
- POST /campaigns {name?, seed_key?} -> 201 create from seed + seed the live play state
"""
from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, status
from pydantic import BaseModel
from sqlalchemy.orm import Session

from app.core.database import get_db
from app.modules.auth.deps import require_user
from app.modules.auth.models import User
from app.modules.campaign.models import Campaign
from app.modules.campaign.service import SEEDS, create_campaign_from_seed, list_campaigns
from app.modules.play.session import PlaySession

router = APIRouter(prefix="/campaigns", tags=["campaigns"])

#: The vertical-slice seed: Ravenford, the hidden monastery cult, the missing travelers.
DEFAULT_SEED = "hollow_crown"


class CreateCampaignIn(BaseModel):
    name: str = ""
    seed_key: str = DEFAULT_SEED


def _row(campaign: Campaign) -> dict:
    return {
        "id": campaign.id,
        "name": campaign.name,
        "seed_key": campaign.seed_key,
        "status": campaign.status,
        "created_at": campaign.created_at.isoformat() if campaign.created_at else None,
    }


@router.get("")
def list_my_campaigns(
    user: User = Depends(require_user),
    db: Session = Depends(get_db),
) -> list[dict]:
    rows = list_campaigns(db, owner_user_id=user.id)
    rows.sort(key=lambda c: c.created_at, reverse=True)
    return [_row(c) for c in rows]


@router.post("", status_code=status.HTTP_201_CREATED)
def create_campaign(
    body: CreateCampaignIn | None = None,
    user: User = Depends(require_user),
    db: Session = Depends(get_db),
) -> dict:
    payload = body or CreateCampaignIn()
    seed_key = payload.seed_key or DEFAULT_SEED
    if seed_key not in SEEDS:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"unknown seed {seed_key!r}; expected one of {sorted(SEEDS)}",
        )
    name = (payload.name or "").strip() or SEEDS[seed_key]["name"]
    campaign = create_campaign_from_seed(
        db, owner_user_id=user.id, name=name, seed_key=seed_key
    )
    # A fresh campaign starts with a live play state (opening scene at the Lantern Inn).
    PlaySession.seed_for(db, campaign.id)
    return _row(campaign)
