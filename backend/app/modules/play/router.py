"""Live play HTTP surface: engine-backed actions.

- POST /act {text, campaign_id?, seed_roll?, roll?, pending_token?} -> resolves
  one action and returns the frontend contract:
  {ack, mechanics, narration, dialogue, newLeads, suggestions, system}.
  When a surfaced check is called, the response carries ``pending_check``
  instead: the player throws the die, and a second POST with ``roll`` (the
  thrown face) + ``pending_token`` resolves the beat. A moved board answers
  409 ``check_expired``.
- Idempotency-Key replays return the stored response without re-applying effects.
- X-Content-Prefs (JSON) bounds narration tone (violence/horror/romance/nsfw).
"""
from __future__ import annotations

import json

from fastapi import APIRouter, Depends, HTTPException, Request, Response, status
from pydantic import BaseModel, Field
from sqlalchemy.orm import Session

from app.core.database import get_db
from app.modules.ai.settings_store import resolve_provider
from app.modules.auth.deps import require_user, scope_campaign
from app.modules.auth.models import User
from app.modules.campaign.models import Campaign
from app.modules.character import prebuilt
from app.modules.narrator.prefs import ContentPrefs
from app.modules.play import creation, screens
from app.modules.play.engine import ActEngine, PendingCheckStale
from app.modules.play.session import PlaySession
from app.modules.play.view import game_state_payload

router = APIRouter(tags=["play"])


class ActIn(BaseModel):
    text: str = Field(min_length=1, max_length=2000)
    campaign_id: str | None = None
    #: Test hook (same convention as ActionInput): deterministic first d20.
    seed_roll: int | None = Field(default=None, ge=1, le=20)
    #: The player's thrown d20 for a pending surfaced check (two-phase /act).
    roll: int | None = Field(default=None, ge=1, le=20)
    #: Token from the pending_check payload; guards against a moved board.
    pending_token: str | None = None


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
    return game_state_payload(session.state, campaign.id)


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


class AdvanceIn(BaseModel):
    stage: int = Field(ge=1, le=6)
    payload: dict = Field(default_factory=dict)


@router.get("/character/prebuilts")
def prebuilts_view(user: User = Depends(require_user)) -> list[dict]:
    """Standard prebuilt hero sheets a new game can start from."""
    return prebuilt.list_heroes()


@router.post("/character/prebuilts/{hero_id}/apply")
def prebuilt_apply(
    hero_id: str,
    campaign_id: str | None = None,
    user: User = Depends(require_user),
    db: Session = Depends(get_db),
) -> dict:
    """Take a prebuilt hero as this campaign's character (bypasses the wizard)."""
    campaign = _resolve_campaign(db, user, campaign_id)
    session = PlaySession.load(db, campaign.id)
    try:
        hero = prebuilt.get_hero(hero_id)
    except KeyError:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail=f"no prebuilt hero {hero_id!r}"
        )
    if session.state.pc.get("created"):
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST, detail="character already created"
        )
    draft = hero.build_creation_state()
    session.state.creation = json.loads(draft.to_json())
    try:
        applied = creation.apply_creation(db, campaign.id, session.state)
    except ValueError as exc:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(exc))
    session.checkpoint(db, "")
    return applied


@router.get("/character/creation")
def creation_state(
    campaign_id: str | None = None,
    user: User = Depends(require_user),
    db: Session = Depends(get_db),
) -> dict:
    """6-stage creation draft for this campaign + the catalogs the wizard renders."""
    campaign = _resolve_campaign(db, user, campaign_id)
    session = PlaySession.load(db, campaign.id)
    return {"options": creation.options(), **creation.get_creation(session.state)}


@router.post("/character/creation/advance")
def creation_advance(
    body: AdvanceIn,
    campaign_id: str | None = None,
    user: User = Depends(require_user),
    db: Session = Depends(get_db),
) -> dict:
    campaign = _resolve_campaign(db, user, campaign_id)
    session = PlaySession.load(db, campaign.id)
    try:
        payload = creation.advance_creation(session.state, body.stage, body.payload)
    except ValueError as exc:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(exc))
    session.checkpoint(db, "")
    return payload


@router.post("/character/creation/commit")
def creation_commit(
    campaign_id: str | None = None,
    user: User = Depends(require_user),
    db: Session = Depends(get_db),
) -> dict:
    """Apply the reviewed draft: DB character rows + the live play sheet."""
    campaign = _resolve_campaign(db, user, campaign_id)
    session = PlaySession.load(db, campaign.id)
    try:
        applied = creation.apply_creation(db, campaign.id, session.state)
    except ValueError as exc:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(exc))
    session.checkpoint(db, "")
    return applied


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

    engine = ActEngine(
        session,
        prefs=_parse_prefs(request.headers.get("X-Content-Prefs")),
        provider=resolve_provider(db, user.id),
    )
    try:
        payload, checkpoint = engine.act(
            body.text,
            # The thrown die IS the seed: one roll path, two callers.
            seed_roll=body.roll if body.roll is not None else body.seed_roll,
            # Player clients suspend on the first surfaced check; seeded calls
            # (tests, tooling) keep resolving in one pass. A throw resolves.
            suspend_on_check=body.roll is None and body.seed_roll is None,
            pending_token=body.pending_token,
        )
    except PendingCheckStale:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail={"code": "check_expired",
                    "message": "the board has moved since the check was called — call it again"},
        )

    # Live AI-cost truth (stub costs nothing, but the call count is real).
    report = engine.meter.report(campaign.id)
    response.headers["x-ai-calls"] = str(report["calls"])
    response.headers["x-ai-cost-usd"] = f"{report.get('cost_usd', 0.0):.6f}"
    response.headers["x-ai-cache"] = "miss"
    response.headers["x-cache"] = "miss"

    if payload.get("pending_check"):
        # A pending throw persists nothing: the action fully resolves on the
        # roll leg, so a dropped die can never leak a half-applied beat.
        return payload

    # Resolved state is committed (and checkpointed) before the response leaves,
    # so a dropped connection can never lose a resolved action.
    session.checkpoint(db, checkpoint or "")
    if session.state.completed and campaign.status != "completed":
        campaign.status = "completed"
        db.commit()
    session.remember_action(db, key, payload)

    return payload
