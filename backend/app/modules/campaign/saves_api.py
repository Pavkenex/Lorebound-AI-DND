"""Campaign save-slot HTTP surface: list / create / fetch (Stream A).

Contracts (frontend depends on these exact shapes):
- GET  /campaigns/{campaign_id}/saves -> 200 list of
  {id, slot, label, checkpoint, created_at} (auth required,
  owner-scoped; 404 on foreign campaigns).
- POST /campaigns/{campaign_id}/saves {label?, slot="manual",
  checkpoint="manual"} -> 201 save-row JSON.
- GET  /saves/{save_id} -> 200 {id, campaign_id, slot, label,
  checkpoint, snapshot dict, created_at}, or 404 for foreign campaigns.
- DELETE /saves/{save_id} -> 200 {deleted, save_id, campaign_id} (owner-scoped,
  404 on missing/foreign; removes only the snapshot row, never live state).
"""
from __future__ import annotations

import json

from fastapi import APIRouter, Depends, HTTPException, status
from pydantic import BaseModel
from sqlalchemy.orm import Session

from app.core.database import get_db
from app.modules.auth.deps import require_user, scope_campaign
from app.modules.auth.models import User
from app.modules.campaign.models import SaveGame
from app.modules.campaign.service import load_save, write_save

router = APIRouter(tags=["saves"])


class CreateSaveIn(BaseModel):
    label: str = ""
    slot: str = "manual"
    checkpoint: str = "manual"


def _row_summary(row: SaveGame) -> dict:
    created = row.created_at.isoformat() if row.created_at is not None else None
    return {
        "id": row.id,
        "slot": row.slot,
        "label": row.label,
        "checkpoint": row.checkpoint,
        "created_at": created,
    }


@router.get("/campaigns/{campaign_id}/saves")
def list_saves(
    campaign_id: str,
    user: User = Depends(require_user),
    db: Session = Depends(get_db),
) -> list[dict]:
    scope_campaign(db, user, campaign_id)
    rows = (
        db.query(SaveGame)
        .filter(SaveGame.campaign_id == campaign_id)
        .order_by(SaveGame.created_at.desc())
        .all()
    )
    return [_row_summary(r) for r in rows]


@router.post("/campaigns/{campaign_id}/saves", status_code=status.HTTP_201_CREATED)
def create_save(
    campaign_id: str,
    body: CreateSaveIn | None = None,
    user: User = Depends(require_user),
    db: Session = Depends(get_db),
) -> dict:
    scope_campaign(db, user, campaign_id)
    payload = body or CreateSaveIn()
    row = write_save(
        db,
        campaign_id=campaign_id,
        slot=payload.slot or "manual",
        label=payload.label or "",
        checkpoint=payload.checkpoint or "manual",
    )
    try:
        snapshot = json.loads(row.snapshot)
    except (json.JSONDecodeError, TypeError, ValueError):
        snapshot = {}
    out = _row_summary(row)
    out.update({"campaign_id": row.campaign_id, "snapshot": snapshot})
    return out


@router.get("/saves/{save_id}")
def get_save(
    save_id: str,
    user: User = Depends(require_user),
    db: Session = Depends(get_db),
) -> dict:
    row = db.query(SaveGame).filter(SaveGame.id == save_id).first()
    if row is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="save not found")
    # Ownership via the parent campaign: 404 (not 403) on foreign campaigns
    # so existence of another account's save never leaks.
    scope_campaign(db, user, row.campaign_id)
    try:
        snapshot = json.loads(row.snapshot)
    except (json.JSONDecodeError, TypeError, ValueError):
        snapshot = {}
    # Validate the snapshot is loadable through the ownership-scoped service
    # path as well (raises PermissionError/LookupError on any drift).
    try:
        load_save(db, save_id=row.id, owner_user_id=user.id)
    except LookupError:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="save not found")
    except PermissionError:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="save not found")
    return {
        "id": row.id,
        "campaign_id": row.campaign_id,
        "slot": row.slot,
        "label": row.label,
        "checkpoint": row.checkpoint,
        "snapshot": snapshot,
        "created_at": row.created_at.isoformat() if row.created_at is not None else None,
    }


@router.delete("/saves/{save_id}")
def delete_save(
    save_id: str,
    user: User = Depends(require_user),
    db: Session = Depends(get_db),
) -> dict:
    """Delete one save slot (owner-scoped; 404 for missing or foreign).

    Only the snapshot row goes — the live campaign state is untouched, so
    pruning the shelf of yesterdays never disturbs the run in progress.
    """
    row = db.query(SaveGame).filter(SaveGame.id == save_id).first()
    if row is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="save not found")
    # Ownership via the parent campaign: 404 (not 403) on foreign campaigns
    # so existence of another account's save never leaks.
    scope_campaign(db, user, row.campaign_id)
    campaign_id = row.campaign_id
    db.delete(row)
    db.commit()
    return {"deleted": True, "save_id": save_id, "campaign_id": campaign_id}


@router.post("/saves/{save_id}/load")
def restore_save(
    save_id: str,
    user: User = Depends(require_user),
    db: Session = Depends(get_db),
) -> dict:
    """Apply a save's snapshot to the live campaign (owner-scoped, 404 foreign).

    Restores the play state (position, mystery progress, memory, sheet) and the
    campaign clock; scenes/facts remain as recorded at save time.
    """
    from app.modules.campaign.models import GameTime
    from app.modules.play.models import PlayStateRow
    from app.modules.play.state import PlayState

    row = db.query(SaveGame).filter(SaveGame.id == save_id).first()
    if row is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="save not found")
    scope_campaign(db, user, row.campaign_id)

    try:
        snapshot = json.loads(row.snapshot)
    except (json.JSONDecodeError, TypeError, ValueError):
        snapshot = {}

    play = snapshot.get("play")
    if isinstance(play, dict):
        state = PlayState.from_json(json.dumps(play))
        state.actions_taken += 1
        state.append_feed("system", text=f"❧ The chronicle turns back to — {row.label or 'an earlier page'}.")
        ps_row = db.get(PlayStateRow, row.campaign_id)
        payload = state.to_json()
        if ps_row is None:
            db.add(PlayStateRow(campaign_id=row.campaign_id, state_json=payload))
        else:
            ps_row.state_json = payload

    clock = snapshot.get("clock")
    if isinstance(clock, dict):
        gt = db.query(GameTime).filter(GameTime.campaign_id == row.campaign_id).first()
        if gt is not None:
            gt.day = int(clock.get("day", gt.day))
            gt.hour = int(clock.get("hour", gt.hour))
            gt.minute = int(clock.get("minute", gt.minute))
    db.commit()
    return {
        "loaded": True,
        "save_id": row.id,
        "campaign_id": row.campaign_id,
        "label": row.label,
        "checkpoint": row.checkpoint,
        "had_play_state": isinstance(play, dict),
    }
