"""Engine bridge: campaign lifecycle, the one-turn wrapper, and the runtime-key flow.

``docs/INTEGRATION_PLAN.md`` §2–§4 is the spec (settled); §11 governs content
boundaries. The rebuilt engine (``engine/``, stdlib-only) runs in-process: each
campaign's game state lives in its own SQLite file (``paths.campaign_db_path``)
while Postgres keeps the app-side rows (``models.py``).

Runtime-key contract (plan §4 — hard rules, do not relax):
- ``provider_key`` is a runtime argument. It is handed to the engine adapter for
  the call that needs it and lives only in server memory for that call.
- It is never written to the engine DB, the app DB, a log line, or a prompt.
  Nothing in this module has a key-shaped slot, and nothing may ever grow one.
- Provider failures keep the engine's scrubbing: the already-redacted message is
  surfaced as ``EngineBridgeError("provider_error", detail=…)``; the raw
  exception is never re-raised with the key attached.

Model gate (owner ruling 2026-09-14 — do not relax):
- The pilot is NOT playable without a connected model. ``"stub"`` is not a
  player provider: an unset provider, the retired ``"stub"`` default (legacy
  rows included) and a live provider without a runtime key all read as "not
  connected" and answer ``EngineBridgeError("connect_your_ai")`` — the same
  contract as the keyless live case.
- The engine package's own stub narrator stays engine-internal dev/test
  machinery; nothing on the player path may reach it.

Content boundaries (plan §11 — do not relax either):
- ``take_turn`` takes the player's ``narrator.prefs.ContentPrefs`` (the router
  parses the ``X-Content-Prefs`` header with that model — no parallel
  vocabulary here) and hands the engine ``prefs.describe_for_prompt()``
  VERBATIM as the prompt's content policy. The directive is prompt-only: it is
  never persisted, never logged, never echoed beyond the applied-prefs summary.
- The applied prefs are echoed in the turn payload's ``content`` block (nsfw +
  the four levels) so the client can show what bounds narration.
- An unreadable header is NOT silently dropped (the legacy bug): the defaults
  apply and ``CONTENT_DEFAULTS_NOTE`` is added to the turn's system lines.

Transport-free by design — the flag-gated HTTP router (and account scoping via
``require_user``/``scope_campaign``) is later work on top of this surface.
"""
from __future__ import annotations

import threading
from collections.abc import Mapping
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import engine
from engine.models import ProviderConfig
from engine.play import PlaySession
from engine.providers.base import ProviderError
from engine.providers.registry import build_adapter
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.modules.campaign.models import Campaign
from app.modules.engine import paths
from app.modules.engine.models import EngineCampaignRow, EngineConnectionRow
from app.modules.narrator.prefs import ContentPrefs

#: Providers the app exposes (plan decision 3: openai-compatible only). The
#: engine's anthropic/gemini adapters stay tested but are not app choices yet.
#: The engine's internal ``stub`` is NOT a provider choice — keyless play was
#: removed from the player surface (P11, owner ruling 2026-09-14).
SUPPORTED_PROVIDERS: tuple[str, ...] = ("openai", "openai-compatible")

#: Stored values that mean "no model connected": unset, and the retired
#: ``"stub"`` default legacy rows were created under.
_NOT_CONNECTED = frozenset({"", "stub"})

#: World fixtures the pilot can seed: name -> engine fixture payload (None = demo).
WORLDS: dict[str, dict | None] = {"demo": None}

#: System line shown when a supplied ``X-Content-Prefs`` payload was unreadable
#: and the standard boundaries were applied instead (plan §11: never a silent
#: drop — the legacy narrator did exactly that with a malformed payload).
CONTENT_DEFAULTS_NOTE = "content: settings could not be read — standard boundaries applied"

DEFAULT_TIMEOUT_S = 30
MIN_TIMEOUT_S = 1
MAX_TIMEOUT_S = 600

#: Lead stages a suggestion chip may name — the ones the player has heard of.
_OPEN_LEAD_STAGES = frozenset({"rumored", "accepted", "in_progress", "complicated"})


class EngineBridgeError(Exception):
    """Bridge failure with a machine-readable code (``str(exc) == code``).

    Codes: ``bad_campaign_id``, ``not_an_engine_campaign``, ``no_campaign``,
    ``unknown_world``, ``unsupported_provider``, ``invalid_prefs``,
    ``connect_your_ai`` (no connected model: provider unset, the retired
    ``"stub"`` default, or a live provider with no key supplied),
    ``provider_error`` (engine-redacted provider failure; ``detail`` carries it).
    """

    def __init__(self, code: str, *, detail: str = "") -> None:
        super().__init__(code)
        self.code = code
        self.detail = detail


# --------------------------------------------------------------------------- #
# Per-campaign writer lock (plan §3: single writer per turn)
# --------------------------------------------------------------------------- #

_locks: dict[str, threading.Lock] = {}
_locks_guard = threading.Lock()


def _campaign_lock(campaign_id: str) -> threading.Lock:
    """The one lock guarding ``{campaign_id}.db`` writes (same object per id)."""
    with _locks_guard:
        lock = _locks.get(campaign_id)
        if lock is None:
            lock = _locks[campaign_id] = threading.Lock()
        return lock


# --------------------------------------------------------------------------- #
# Connection prefs (non-secret only — there is no key field, by design)
# --------------------------------------------------------------------------- #


def connection_settings(db: Session, user_id: str) -> EngineConnectionRow:
    """The user's engine prefs, created with defaults on first read.

    Get-or-create is race-safe: two concurrent first turns (two devices, a
    retried request) can both miss the row, and the loser re-reads after its
    insert hits the unique key instead of failing the turn.
    """
    row = db.get(EngineConnectionRow, user_id)
    if row is not None:
        return row
    row = EngineConnectionRow(user_id=user_id)
    db.add(row)
    try:
        db.commit()
    except IntegrityError:
        db.rollback()
        existing = db.get(EngineConnectionRow, user_id)
        if existing is None:
            raise
        return existing
    db.refresh(row)
    return row


def save_connection(
    db: Session,
    user_id: str,
    *,
    provider: str | None = None,
    base_url: str | None = None,
    model: str | None = None,
    timeout_s: int | None = None,
) -> EngineConnectionRow:
    """Persist non-secret prefs. There is deliberately no ``api_key`` parameter."""
    row = connection_settings(db, user_id)
    if provider is not None:
        mode = _provider_key(provider, strict=True)
        row.provider = mode
    if base_url is not None:
        row.base_url = _bounded("base_url", base_url, 500)
    if model is not None:
        row.model = _bounded("model", model, 200)
    if timeout_s is not None:
        row.timeout_s = _timeout(timeout_s)
    db.commit()
    db.refresh(row)
    return row


def connection_view(row: EngineConnectionRow) -> dict:
    """JSON-safe prefs for the HTTP surface — never a key, because none exists.

    The provider reads back as the connect surface sees it: unset and the
    retired ``"stub"`` default both normalize to ``""`` (not connected).
    """
    return {
        "provider": connection_provider(row),
        "base_url": row.base_url,
        "model": row.model,
        "timeout_s": row.timeout_s,
        "updated_at": row.updated_at.isoformat() if row.updated_at else None,
    }


def connection_provider(row: EngineConnectionRow) -> str:
    """The row's provider as "which model is connected": ``""`` when none is.

    One vocabulary for the model gate (P11): an unset value and the retired
    ``"stub"`` default legacy rows carry both mean NOT CONNECTED — there is no
    keyless/stub play on the player path.
    """
    mode = _provider_key(row.provider, strict=False)
    return "" if mode in _NOT_CONNECTED else mode


def _provider_key(provider: str, *, strict: bool) -> str:
    """Normalize a provider value; ``strict`` rejects anything not offered."""
    mode = str(provider or "").strip().lower().replace("_", "-")
    if strict and mode not in SUPPORTED_PROVIDERS:
        raise EngineBridgeError(
            "unsupported_provider",
            detail=f"provider {mode!r} is not available; supported: {', '.join(SUPPORTED_PROVIDERS)}",
        )
    return mode


def _bounded(field: str, value: Any, limit: int) -> str:
    text = str(value or "").strip()
    if len(text) > limit:
        raise EngineBridgeError("invalid_prefs", detail=f"{field} exceeds {limit} characters")
    return text


def _timeout(value: Any) -> int:
    try:
        seconds = int(value)
    except (TypeError, ValueError) as exc:
        raise EngineBridgeError("invalid_prefs", detail="timeout_s must be an integer") from exc
    if not MIN_TIMEOUT_S <= seconds <= MAX_TIMEOUT_S:
        raise EngineBridgeError(
            "invalid_prefs",
            detail=f"timeout_s must be between {MIN_TIMEOUT_S} and {MAX_TIMEOUT_S}",
        )
    return seconds


# --------------------------------------------------------------------------- #
# Campaign lifecycle
# --------------------------------------------------------------------------- #


def create_campaign(db: Session, user: Any, name: str, world: str = "demo") -> Campaign:
    """Create the app campaign + engine side-row, then seed its engine DB.

    The engine DB seeding is idempotent (``PlaySession.start`` seeds only an
    empty store), and a failed seed self-heals on the first turn; the app rows
    are committed first so the campaign always exists.
    """
    key = _world_key(world)
    campaign = Campaign(
        owner_user_id=user.id,
        name=(str(name or "").strip() or "New journey"),
        seed_key="engine",
        status="active",
    )
    db.add(campaign)
    db.flush()
    db.add(
        EngineCampaignRow(
            campaign_id=campaign.id,
            world=key,
            engine_version=str(engine.__version__),
        )
    )
    db.commit()
    db.refresh(campaign)
    _seed_campaign_db(campaign.id, key)
    return campaign


def get_engine_campaign(db: Session, campaign_id: str) -> EngineCampaignRow | None:
    """The engine side-row for ``campaign_id`` (None = not an engine campaign)."""
    return db.get(EngineCampaignRow, campaign_id)


def _world_key(world: str) -> str:
    key = str(world or "").strip().lower() or "demo"
    if key not in WORLDS:
        raise EngineBridgeError(
            "unknown_world", detail=f"known worlds: {', '.join(sorted(WORLDS))}"
        )
    return key


def _seed_campaign_db(campaign_id: str, world: str) -> None:
    """Seed a campaign's engine DB from its fixture (no-op when already seeded)."""
    session = PlaySession.start(db_path=str(paths.campaign_db_path(campaign_id)), world=WORLDS[world])
    session.close()


# --------------------------------------------------------------------------- #
# Turns
# --------------------------------------------------------------------------- #


def take_turn(
    db: Session,
    user_id: str,
    campaign_id: str,
    text: str,
    *,
    provider_key: str | None = None,
    content_prefs: ContentPrefs | None = None,
    content_prefs_invalid: bool = False,
) -> dict:
    """Run exactly one engine turn for ``campaign_id`` and return its payload.

    ``provider_key`` is a runtime-only BYOK value (plan §4): passed to the
    engine adapter for this call, never persisted, logged, or prompted. A turn
    needs a connected model: an unset provider, the retired ``"stub"`` default,
    or a live provider without a key all raise
    ``EngineBridgeError('connect_your_ai')`` (P11 — no keyless play).

    ``content_prefs`` is the player's content boundaries (plan §11), parsed by
    the router from ``X-Content-Prefs`` with ``narrator.prefs.ContentPrefs``;
    ``None`` means no header was supplied and the defaults apply.
    ``content_prefs_invalid`` marks a payload the router could not read: the
    defaults are applied anyway and the turn carries ``CONTENT_DEFAULTS_NOTE``
    in its system lines — never a silent drop.
    """
    applied = content_prefs if isinstance(content_prefs, ContentPrefs) else ContentPrefs()
    db_path = _db_path_or_error(campaign_id)
    row = db.get(EngineCampaignRow, campaign_id)
    if row is None:
        raise EngineBridgeError("not_an_engine_campaign")
    provider_cfg, adapter = _resolve_provider(connection_settings(db, user_id), provider_key)

    with _campaign_lock(campaign_id):
        session = PlaySession.start(
            db_path=str(db_path), adapter=adapter, provider=provider_cfg,
            # The player's boundaries, verbatim, as the prompt's content policy.
            content_policy=applied.describe_for_prompt(),
        )
        try:
            result = session.act(str(text or ""))
            payload = _turn_payload(
                result, session,
                content_prefs=applied, content_prefs_invalid=content_prefs_invalid,
            )
        except ProviderError as exc:
            # Already redacted by the engine's transport layer (plan §4).
            raise EngineBridgeError("provider_error", detail=str(exc)) from exc
        finally:
            session.close()

    row.last_turn_at = datetime.now(UTC)
    db.commit()
    return payload


def _resolve_provider(
    prefs: EngineConnectionRow, provider_key: str | None
) -> tuple[ProviderConfig, Any]:
    """``(provider config, adapter)`` for this turn — or ``connect_your_ai``.

    The single resolution point for the model gate (P11): ``"stub"`` no longer
    resolves to the engine's built-in stub. An unset provider and the retired
    ``"stub"`` default behave exactly like a keyless live provider — the turn
    is refused before the engine is ever reached.
    """
    mode = connection_provider(prefs)
    if not mode or not str(provider_key or "").strip():
        raise EngineBridgeError("connect_your_ai")
    if mode not in SUPPORTED_PROVIDERS:  # a legacy/foreign value in the row
        raise EngineBridgeError(
            "unsupported_provider",
            detail=f"provider {mode!r} is not available; supported: {', '.join(SUPPORTED_PROVIDERS)}",
        )
    cfg = ProviderConfig(
        name="openai",
        model=str(prefs.model or ""),
        base_url=str(prefs.base_url or ""),
        api_mode="openai",
        timeout_s=float(prefs.timeout_s or DEFAULT_TIMEOUT_S),
    )
    return cfg, build_adapter(cfg, api_key=provider_key)


def get_state(campaign_id: str) -> dict:
    """State snapshot for one engine campaign — never creates or seeds a DB.

    Ownership is the router's concern (``scope_campaign``); reads are lock-free
    (WAL allows a reader alongside the single writer).
    """
    db_path = _db_path_or_error(campaign_id)
    if not db_path.is_file():
        raise EngineBridgeError("no_campaign")
    session = PlaySession.start(db_path=str(db_path), auto_seed=False)
    try:
        if session.store.count("world") == 0:
            raise EngineBridgeError("no_campaign")
        return session.state_view()
    finally:
        session.close()


def _db_path_or_error(campaign_id: str) -> Path:
    try:
        return paths.campaign_db_path(campaign_id)
    except paths.EnginePathError as exc:
        raise EngineBridgeError(
            "bad_campaign_id", detail="campaign id must be a 36-character uuid"
        ) from exc


# --------------------------------------------------------------------------- #
# Response shaping
# --------------------------------------------------------------------------- #


def _turn_payload(result: Any, session: PlaySession,
                  content_prefs: ContentPrefs | None = None,
                  content_prefs_invalid: bool = False) -> dict:
    state = session.state_view()
    system_lines = [str(line) for line in (getattr(result, "system_lines", None) or [])]
    if content_prefs_invalid:
        system_lines.append(CONTENT_DEFAULTS_NOTE)
    return {
        "turn": int(getattr(result, "turn", 0) or 0),
        "narration": str(getattr(result, "narration", "") or ""),
        "dialogue": _dialogue(getattr(result, "npc_dialogue", None)),
        "mechanics": _mechanics(getattr(result, "mechanics", None)),
        "suggestions": _suggestions(state),
        "capability": _capability(session),
        "state": state,
        "system_lines": system_lines,
        # The boundaries narration actually ran under (plan §11) — the applied
        # prefs, not the header: unreadable input reads back as the defaults.
        "content": _content_view(content_prefs),
    }


def _content_view(prefs: ContentPrefs | None) -> dict:
    """JSON-safe echo of the applied content boundaries (nsfw + four levels)."""
    applied = prefs if isinstance(prefs, ContentPrefs) else ContentPrefs()
    return {
        "nsfw": bool(applied.nsfw),
        "violence": str(applied.violence),
        "horror": str(applied.horror),
        "romance": str(applied.romance),
        "language": str(applied.language),
    }


def _dialogue(entries: Any) -> list[dict]:
    """Engine dialogue rows, normalized to ``{npc_id, name, text}`` (engine's keys)."""
    out: list[dict] = []
    for entry in entries or []:
        if not isinstance(entry, Mapping):
            continue
        out.append(
            {
                "npc_id": str(entry.get("npc_id") or ""),
                "name": str(entry.get("name") or ""),
                "text": str(entry.get("text") or ""),
            }
        )
    return out


def _mechanics(outcome: Any) -> dict:
    payload: dict[str, Any] = {
        "kind": "",
        "label": "",
        "verdict_line": "",
        "band": None,
        "roll": None,
        "modifier": None,
        "total": None,
        "dc": None,
        "skill": "",
    }
    if outcome is None:
        return payload
    payload["kind"] = str(getattr(outcome, "kind", "") or "")
    payload["label"] = str(getattr(outcome, "label", "") or "")
    payload["verdict_line"] = str(getattr(outcome, "verdict_line", "") or "")
    check = getattr(outcome, "check", None)
    if check is not None:
        request = getattr(check, "request", None)
        payload.update(
            {
                "band": str(getattr(check, "band", "") or ""),
                "roll": getattr(check, "roll", None),
                "modifier": getattr(check, "modifier", None),
                "total": getattr(check, "total", None),
                "dc": getattr(request, "dc", None),
                "skill": str(getattr(request, "skill", "") or ""),
            }
        )
    return payload


def _suggestions(state: Mapping[str, Any]) -> list[str]:
    """Chip-sized next moves: the campaign's open leads the player knows about.

    The engine has no suggestion generator, so the bridge derives them from open
    leads — a lead still ``unheard`` is not a move the player can make yet.
    """
    out: list[str] = []
    for lead in state.get("leads") or []:
        if not isinstance(lead, Mapping):
            continue
        if str(lead.get("stage") or "") not in _OPEN_LEAD_STAGES:
            continue
        title = str(lead.get("title") or "").strip()
        if title:
            out.append(title)
    return out


def _capability(session: PlaySession) -> dict:
    """What the turn ran on: the player's live model, with/without native tools.

    Every player turn runs live (P11 — unconnected play is refused before the
    engine is reached), so the keyset is stable: ``provider``/``model``/``mode``
    plus the protocol verdict. ``degraded`` is the JSON-in-text fallback.
    """
    narrator = session.narrator
    getter = getattr(narrator, "capabilities", None)
    caps = getter() if callable(getter) else None
    native = bool(getattr(caps, "native_tools", False))
    return {
        "provider": "openai",
        "model": str(getattr(narrator, "model_name", "") or ""),
        "mode": "live",
        "native_tools": native,
        "degraded": not native,
    }
