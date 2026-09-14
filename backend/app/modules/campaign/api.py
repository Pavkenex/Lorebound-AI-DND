"""Campaign lifecycle HTTP surface: create + list + restart (Stream: live play, owner-scoped).

- GET  /campaigns        -> list of the caller's campaigns (id, name, seed_key, status, created_at)
- POST /campaigns {name?, seed_key?} -> 201 create from seed + seed the live play state
- POST /campaigns/{campaign_id}/restart -> 200 start a new journey (checkpoint old
  run when it has progress, reset play state + clock to the opening scene)
"""
from __future__ import annotations

import copy

from fastapi import APIRouter, Depends, HTTPException, status
from pydantic import BaseModel
from sqlalchemy.orm import Session

from app.core.database import get_db
from app.modules.auth.deps import require_user, scope_campaign
from app.modules.auth.models import User
from app.modules.campaign.models import Campaign, GameTime
from app.modules.campaign.npc import NPCMemory, NPCRelationship
from app.modules.campaign.service import (
    SEEDS,
    create_campaign_from_seed,
    list_campaigns,
    write_save,
)
from app.modules.play.models import PlayActionRow, PlayStateRow
from app.modules.play.session import PlaySession
from app.modules.play.state import reopen_prologue_opening, seeded_state

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


@router.post("/{campaign_id}/restart")
def restart_campaign(
    campaign_id: str,
    user: User = Depends(require_user),
    db: Session = Depends(get_db),
) -> dict:
    """Begin a new journey on this campaign (the menu's "New Journey").

    The run in progress is checkpointed onto the save shelf first — but only
    when it has actual progress; an untouched campaign just resets — then the
    live play state and clock go back to the opening scene, exactly as a
    freshly created campaign starts. Old saves stay loadable, so stepping
    back into the previous run is always one tap away.
    """
    campaign = scope_campaign(db, user, campaign_id)

    session = PlaySession.load(db, campaign_id)
    had_progress = bool(getattr(session.state, "actions_taken", 0))
    checkpoint_save_id = None
    if had_progress:
        checkpoint = write_save(
            db,
            campaign_id=campaign_id,
            slot="autosave",
            label="Before the new road",
            checkpoint="new_journey",
        )
        checkpoint_save_id = checkpoint.id

    fresh = seeded_state()
    # The character a player built is theirs across journeys: the sheet rides
    # into the new run, and with it the chronicle's opening — which the model
    # writes anew for that sheet (the seed itself carries no prose, P14).
    built = getattr(session.state, "pc", None) or {}
    if built.get("created"):
        fresh.pc = copy.deepcopy(built)
        reopen_prologue_opening(fresh)
    payload = fresh.to_json()
    ps_row = db.get(PlayStateRow, campaign_id)
    if ps_row is None:
        db.add(PlayStateRow(campaign_id=campaign_id, state_json=payload))
    else:
        ps_row.state_json = payload

    # Fresh clock, exactly as campaign creation seeds it (day 1, 08:00).
    # (Reset in place: delete+insert trips the unique campaign_id constraint
    # because inserts flush before deletes in one unit of work.)
    clock = db.query(GameTime).filter(GameTime.campaign_id == campaign_id).first()
    if clock is None:
        db.add(GameTime(campaign_id=campaign_id))
    else:
        clock.day, clock.hour, clock.minute = 1, 8, 0

    # Old-run idempotency keys must not replay results into the new journey.
    db.query(PlayActionRow).filter(PlayActionRow.campaign_id == campaign_id).delete()

    # The memory/relationship mirrors are derived data: drop them so they
    # rebuild from the fresh play state on the next save.
    db.query(NPCMemory).filter(NPCMemory.campaign_id == campaign_id).delete()
    db.query(NPCRelationship).filter(NPCRelationship.campaign_id == campaign_id).delete()

    # A restarted campaign is playable again even if the previous arc finished.
    campaign.status = "active"

    db.commit()
    return {
        "restarted": True,
        "campaign_id": campaign_id,
        "had_progress": had_progress,
        "checkpoint_save_id": checkpoint_save_id,
    }
