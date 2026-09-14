"""Engine pilot HTTP surface — flag-gated ``/engine/*`` (docs/INTEGRATION_PLAN.md §5).

Routes (all behind ``settings.ENGINE_MODE``; every path 404s when the flag is off):

- ``POST /engine/campaigns`` {name} -> 201 campaign — starts a pilot chronicle
- ``GET  /engine/campaigns`` -> the caller's engine campaigns (id, name, world, last_turn_at)
- ``POST /engine/campaigns/{id}/turns`` {text} (+ ``X-Provider-Key``, optional
  ``X-Content-Prefs``) -> turn payload
- ``GET  /engine/campaigns/{id}/state`` -> state snapshot
- ``GET  /engine/connection`` / ``PUT /engine/connection`` -> non-secret prefs
- ``POST /engine/connection/check`` (+ ``X-Provider-Key``) -> {reachable, native_tools, detail}

Runtime-key contract (plan §4 — hard rules, do not relax): the key arrives in the
``X-Provider-Key`` header, is handed to the bridge for the one call that needs it,
and is never persisted, logged, prompted, or echoed. This module never logs request
bodies or headers. Error details leave scrubbed — the engine redacts its own
provider failures, and this boundary re-redacts with the exact runtime key as
defense in depth. Nothing here (or below it) has a key-shaped storage slot.

Content boundaries (plan §11): ``X-Content-Prefs`` carries the player's content
limits as JSON — the ``narrator.prefs.ContentPrefs`` vocabulary (``violence`` /
``horror`` / ``romance`` / ``language`` at ``off`` | ``reduced`` | ``standard``,
plus the ``nsfw`` master switch). Same header the legacy play surface reads.
Absent (or empty) means the defaults. A payload this layer cannot read falls
back to the defaults AND the turn payload carries a system note saying so — the
legacy silent drop is the bug being avoided, not a behaviour to copy. The applied
prefs are echoed in the payload's ``content`` block; the header payload itself is
never logged.

Error shapes (plan §5): 400 ``{"detail": "connect_your_ai"}`` when a live provider
is selected without a key; 502 for provider failures with the scrubbed detail; 404
for unknown/foreign campaigns — ownership goes through ``scope_campaign``, which
never reveals whether another account's campaign id exists.
"""
from __future__ import annotations

import json
from datetime import UTC, datetime

from engine.models import ChatMessage, ChatRequest, ProviderConfig
from engine.providers.base import ProviderError
from engine.providers.registry import (
    CANARY_MAX_TOKENS,
    CANARY_PROMPT,
    CANARY_TOOL_NAME,
    build_adapter,
    canary_tool_schema,
)
from engine.providers.transport import redact
from fastapi import APIRouter, Depends, HTTPException, Request, status
from pydantic import BaseModel, ConfigDict, Field, ValidationError
from sqlalchemy.orm import Session

from app.core.config import settings
from app.core.database import get_db
from app.modules.auth.deps import require_user, scope_campaign
from app.modules.auth.models import User
from app.modules.campaign.models import Campaign
from app.modules.engine import bridge
from app.modules.engine.models import EngineCampaignRow, EngineConnectionRow
from app.modules.narrator.prefs import ContentPrefs


def require_engine_mode() -> None:
    """The single router-wide guard: with ``ENGINE_MODE`` off, every path is gone.

    Applied as a router dependency, so it runs before auth and before body
    validation — a disabled pilot answers the same 404 a missing route would,
    for everyone, with the framework's own body shape.
    """
    if not settings.ENGINE_MODE:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Not Found")


router = APIRouter(prefix="/engine", tags=["engine"], dependencies=[Depends(require_engine_mode)])


# --------------------------------------------------------------------------- #
# Request bodies
# --------------------------------------------------------------------------- #


class CreateEngineCampaignIn(BaseModel):
    name: str = Field(default="", max_length=200)


class TurnIn(BaseModel):
    text: str = Field(min_length=1, max_length=2000)


class ConnectionIn(BaseModel):
    """PUT /engine/connection body — non-secret prefs only (plan §4).

    There is deliberately NO key field, and there must never be one. Unknown
    fields are ignored rather than rejected on purpose: pydantic echoes the
    offending input value in a 422 body, so ``extra="forbid"`` would bounce a
    stray ``api_key`` straight back in the response — which §4 forbids. An
    ignored field is never stored anywhere either.
    """

    model_config = ConfigDict(extra="ignore")

    provider: str | None = None
    base_url: str | None = None
    model: str | None = None
    timeout_s: int | None = None


# --------------------------------------------------------------------------- #
# Content boundaries (plan §11) — parsed with the legacy ContentPrefs contract
# --------------------------------------------------------------------------- #


def content_prefs(raw: str | None) -> tuple[ContentPrefs, bool]:
    """``(applied prefs, payload was readable)`` for an ``X-Content-Prefs`` value.

    The validation model is ``narrator.prefs.ContentPrefs`` itself — the same
    vocabulary (and the same level aliases) the legacy play surface accepts, so
    the engine path can never drift from it. Absent/empty input is the normal
    case (defaults, no note); anything unreadable — bad JSON, a non-object, an
    out-of-vocabulary level — answers the defaults with ``readable=False`` so
    the caller can surface the fallback instead of dropping it silently.
    """
    if raw is None or not raw.strip():
        return ContentPrefs(), True
    try:
        data = json.loads(raw)
    except (TypeError, ValueError):
        return ContentPrefs(), False
    if not isinstance(data, dict):
        return ContentPrefs(), False
    try:
        return ContentPrefs(**data), True
    except ValidationError:
        return ContentPrefs(), False


# --------------------------------------------------------------------------- #
# Error mapping (plan §5)
# --------------------------------------------------------------------------- #

#: EngineBridgeError.code -> HTTP status. Anything unmapped is a bad request.
_BRIDGE_STATUS: dict[str, int] = {
    "connect_your_ai": status.HTTP_400_BAD_REQUEST,
    "invalid_prefs": status.HTTP_400_BAD_REQUEST,
    "unsupported_provider": status.HTTP_400_BAD_REQUEST,
    "unknown_world": status.HTTP_400_BAD_REQUEST,
    "bad_campaign_id": status.HTTP_400_BAD_REQUEST,
    "provider_error": status.HTTP_502_BAD_GATEWAY,
    "no_campaign": status.HTTP_404_NOT_FOUND,
    "not_an_engine_campaign": status.HTTP_404_NOT_FOUND,
}


def _http_error(
    exc: bridge.EngineBridgeError, *, provider_key: str | None = None
) -> HTTPException:
    """Map a bridge failure onto its HTTP shape; details leave scrubbed."""
    code = _BRIDGE_STATUS.get(exc.code, status.HTTP_400_BAD_REQUEST)
    if code == status.HTTP_404_NOT_FOUND:
        # Never leaks whether another account's (or a legacy) campaign exists.
        detail = "campaign not found"
    elif exc.code == "connect_your_ai":
        detail = "connect_your_ai"  # exact plan §5 contract
    else:
        detail = _scrub(exc.detail or exc.code, provider_key)
    return HTTPException(status_code=code, detail=detail)


def _scrub(text: str, provider_key: str | None) -> str:
    """Defense in depth (§4): the engine redacts its own errors, but a detail
    string never leaves this surface with the runtime key still in it."""
    return redact(text, (provider_key,)) if provider_key else text


# --------------------------------------------------------------------------- #
# Campaigns
# --------------------------------------------------------------------------- #


def _played_at(campaign: Campaign, row: EngineCampaignRow) -> datetime:
    """Sort key for the list: last turn, else creation (naive-UTC, see below)."""
    when = row.last_turn_at or campaign.created_at
    if when.tzinfo is not None:
        when = when.astimezone(UTC).replace(tzinfo=None)
    return when


def _campaign_row(campaign: Campaign, row: EngineCampaignRow) -> dict:
    """The pilot's campaign shape (plan §5): id, name, world, last_turn_at."""
    return {
        "id": campaign.id,
        "name": campaign.name,
        "world": row.world,
        "last_turn_at": row.last_turn_at.isoformat() if row.last_turn_at else None,
    }


@router.get("/campaigns")
def list_engine_campaigns(
    user: User = Depends(require_user),
    db: Session = Depends(get_db),
) -> list[dict]:
    """The caller's engine campaigns, most recently played first.

    Engine campaigns are plain ``campaigns`` rows (seed_key ``engine``) plus
    their side-row; both conditions are filtered here so a stray row can never
    leak into the pilot list.
    """
    rows = (
        db.query(Campaign, EngineCampaignRow)
        .join(EngineCampaignRow, EngineCampaignRow.campaign_id == Campaign.id)
        .filter(Campaign.owner_user_id == user.id, Campaign.seed_key == "engine")
        .all()
    )
    rows.sort(key=lambda pair: _played_at(pair[0], pair[1]), reverse=True)
    return [_campaign_row(campaign, row) for campaign, row in rows]


@router.post("/campaigns", status_code=status.HTTP_201_CREATED)
def create_engine_campaign(
    body: CreateEngineCampaignIn | None = None,
    user: User = Depends(require_user),
    db: Session = Depends(get_db),
) -> dict:
    """Create an engine campaign: app row + side-row + seeded engine DB."""
    payload = body or CreateEngineCampaignIn()
    try:
        campaign = bridge.create_campaign(db, user, payload.name)
    except bridge.EngineBridgeError as exc:
        raise _http_error(exc) from exc
    # The side-row is committed in the same transaction as the campaign row.
    row = bridge.get_engine_campaign(db, campaign.id)
    return _campaign_row(campaign, row)


@router.post("/campaigns/{campaign_id}/turns")
def run_turn(
    campaign_id: str,
    body: TurnIn,
    request: Request,
    user: User = Depends(require_user),
    db: Session = Depends(get_db),
) -> dict:
    """Run exactly one engine turn and pass its payload through (plan §2).

    The optional ``X-Provider-Key`` header is the runtime-only BYOK key for this
    call; nothing is stored from it.

    The optional ``X-Content-Prefs`` header (plan §11) is the player's content
    boundaries in the ``ContentPrefs`` JSON vocabulary. It is validated here and
    handed to the bridge as an applied model; unreadable input falls back to the
    defaults with a system note in the payload. The payload echoes the applied
    prefs in its ``content`` block.
    """
    campaign = scope_campaign(db, user, campaign_id)
    provider_key = request.headers.get("X-Provider-Key")
    parsed, readable = content_prefs(request.headers.get("X-Content-Prefs"))
    try:
        return bridge.take_turn(
            db, user.id, campaign.id, body.text,
            provider_key=provider_key,
            content_prefs=parsed,
            content_prefs_invalid=not readable,
        )
    except bridge.EngineBridgeError as exc:
        raise _http_error(exc, provider_key=provider_key) from exc


@router.get("/campaigns/{campaign_id}/state")
def campaign_state(
    campaign_id: str,
    user: User = Depends(require_user),
    db: Session = Depends(get_db),
) -> dict:
    """State snapshot for one engine campaign (read-only, never creates one)."""
    campaign = scope_campaign(db, user, campaign_id)
    try:
        return bridge.get_state(campaign.id)
    except bridge.EngineBridgeError as exc:
        raise _http_error(exc) from exc


# --------------------------------------------------------------------------- #
# Connection (non-secret prefs + the capability canary)
# --------------------------------------------------------------------------- #


@router.get("/connection")
def get_connection(
    user: User = Depends(require_user),
    db: Session = Depends(get_db),
) -> dict:
    """The caller's stored engine prefs — provider/base_url/model/timeout only."""
    return bridge.connection_view(bridge.connection_settings(db, user.id))


@router.put("/connection")
def put_connection(
    body: ConnectionIn,
    user: User = Depends(require_user),
    db: Session = Depends(get_db),
) -> dict:
    """Persist non-secret prefs (partial update; omitted fields are kept)."""
    try:
        row = bridge.save_connection(
            db,
            user.id,
            provider=body.provider,
            base_url=body.base_url,
            model=body.model,
            timeout_s=body.timeout_s,
        )
    except bridge.EngineBridgeError as exc:
        raise _http_error(exc) from exc
    return bridge.connection_view(row)


@router.post("/connection/check")
def check_connection(
    request: Request,
    user: User = Depends(require_user),
    db: Session = Depends(get_db),
) -> dict:
    """Canary-verify the SAVED connection prefs with this request's key (§4/§5).

    Provider outcomes always answer 200 with ``{reachable, native_tools, detail}``
    — a check reports a verdict, it never fails the request. A live provider with
    no key still answers 400 ``connect_your_ai``: there is nothing to probe with.
    No store: the verdict is not cached (that happens on the first live turn).
    """
    provider_key = request.headers.get("X-Provider-Key")
    prefs = bridge.connection_settings(db, user.id)
    try:
        return _canary_verdict(prefs, provider_key)
    except bridge.EngineBridgeError as exc:
        raise _http_error(exc, provider_key=provider_key) from exc


def _canary_verdict(prefs: EngineConnectionRow, provider_key: str | None) -> dict:
    """``{reachable, native_tools, detail}`` — never raises on a probe failure.

    Composes the registry's own canary payload (its public ``CANARY_*`` pieces)
    instead of calling ``probe_capabilities``: that public probe folds "did the
    model answer at all" into a conservative ``native_tools=False`` verdict,
    while this endpoint's whole point is reporting reachability honestly. The
    native-tools predicate is the same one the registry applies.
    """
    mode = str(prefs.provider or "").strip().lower().replace("_", "-") or "stub"
    if mode not in bridge.SUPPORTED_PROVIDERS:
        raise bridge.EngineBridgeError(
            "unsupported_provider",
            detail=(
                f"provider {mode!r} is not available; "
                f"supported: {', '.join(bridge.SUPPORTED_PROVIDERS)}"
            ),
        )
    if mode == "stub":
        return {
            "reachable": True,
            "native_tools": False,
            "detail": "stub provider — no model call needed",
        }
    if not str(provider_key or "").strip():
        raise bridge.EngineBridgeError("connect_your_ai")

    cfg = ProviderConfig(
        name="openai",
        model=str(prefs.model or ""),
        base_url=str(prefs.base_url or ""),
        api_mode="openai",
        timeout_s=float(prefs.timeout_s or bridge.DEFAULT_TIMEOUT_S),
    )
    adapter = build_adapter(cfg, api_key=provider_key)
    try:
        response = adapter.complete(
            ChatRequest(
                model=cfg.model,
                messages=[ChatMessage(role="user", content=CANARY_PROMPT)],
                tools=canary_tool_schema(),
                max_tokens=CANARY_MAX_TOKENS,
                temperature=0.0,
            )
        )
    except ProviderError as exc:
        # Already engine-redacted; scrubbed once more with the runtime key.
        return {"reachable": False, "native_tools": False, "detail": _scrub(str(exc), provider_key)}
    except Exception as exc:  # noqa: BLE001 - a check must always answer with a verdict
        detail = _scrub(f"provider probe failed: {type(exc).__name__}: {exc}", provider_key)
        return {"reachable": False, "native_tools": False, "detail": detail[:300]}

    native = any(call.name == CANARY_TOOL_NAME for call in response.tool_calls or [])
    detail = (
        "model reachable; native tool calls available"
        if native
        else "model reachable; no native tool calls — the engine will use its JSON fallback"
    )
    return {"reachable": True, "native_tools": bool(native), "detail": detail}
