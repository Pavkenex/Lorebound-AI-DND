"""Bring-your-own AI provider settings (per user).

Players can point narration at any OpenAI-compatible chat-completions
endpoint from Settings; when no settings are saved the environment
configuration applies (``AI_PROVIDER``). One row per user. The API key is
stored server-side and never returned to the client — reads expose only
``has_key``.

Model gate (owner ruling 2026-09-14, extended to the story game by P12 — do
not relax): the chronicle is NOT playable without a connected model. ``"stub"``
is not a provider choice — an unset row value, the retired ``"stub"`` default
legacy rows carry, an incomplete endpoint row and a broken environment config
all read as NOT CONNECTED (``AiConnection.connected`` is False). ``/act``
refuses those turns with ``400 {"detail": "connect_your_ai"}``. The built-in
stub provider survives only as internal test plumbing.

One truth: :func:`resolve` is the single resolution function. The Settings
panel (``settings_doc``) and the play path (:func:`resolve_provider`) both
derive from it, so ``/ai/settings``' ``active_*`` names exactly what ``/act``
will narrate with — in every case, environment included.
"""
from __future__ import annotations

import os
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any

from sqlalchemy import DateTime, ForeignKey, Integer, String
from sqlalchemy.orm import Mapped, Session, mapped_column

from app.core import config
from app.core.database import Base
from app.modules.ai.providers import (
    OpenAICompatibleProvider,
    Provider,
    ProviderError,
    StubProvider,
    get_provider,
)
from app.modules.auth.models import User  # noqa: F401  (users.id FK metadata)

DEFAULT_TIMEOUT_S = 30
#: Providers a saved row may name. The retired ``"stub"`` is NOT one of them:
#: the built-in storyteller is internal test plumbing, never play (P12).
PROVIDERS: tuple[str, ...] = ("openai-compatible",)
#: Stored values that mean "no model connected": unset, and the retired
#: ``"stub"`` default legacy rows were created under.
_NOT_CONNECTED = frozenset({"", "stub"})


def _utcnow() -> datetime:
    return datetime.now(UTC)


class AiProviderSettingRow(Base):
    """One user's chosen text provider (empty row values mean 'not configured')."""

    __tablename__ = "ai_provider_settings"

    user_id: Mapped[str] = mapped_column(
        String(36), ForeignKey("users.id", ondelete="CASCADE"), primary_key=True
    )
    provider: Mapped[str] = mapped_column(String(32), nullable=False, default="")
    base_url: Mapped[str] = mapped_column(String(500), nullable=False, default="")
    model: Mapped[str] = mapped_column(String(200), nullable=False, default="")
    api_key: Mapped[str] = mapped_column(String(500), nullable=False, default="")
    timeout_s: Mapped[int] = mapped_column(Integer, nullable=False, default=DEFAULT_TIMEOUT_S)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=_utcnow, onupdate=_utcnow, nullable=False
    )


@dataclass(frozen=True)
class AiConnection:
    """One resolution of "what will ``/act`` narrate with?" — the single truth.

    ``provider`` is ``None`` exactly when nothing is connected; ``name`` /
    ``source`` / ``model`` / ``base_url`` describe it for the Settings panel,
    and ``reason`` says why it is not connected (machine words the client maps
    to a sentence: ``unset``, ``stub``, ``incomplete``, ``misconfigured``,
    ``unsupported``).
    """

    provider: Provider | None
    name: str
    source: str  # "settings" | "env" | "default"
    model: str = ""
    base_url: str = ""
    reason: str = ""

    @property
    def connected(self) -> bool:
        return self.provider is not None


def load_row(db: Session, user_id: str) -> AiProviderSettingRow | None:
    return db.get(AiProviderSettingRow, user_id)


def normalize_base_url(raw: str) -> str:
    return (raw or "").strip().rstrip("/")


def save_settings(
    db: Session,
    user_id: str,
    *,
    provider: str,
    base_url: str = "",
    model: str = "",
    api_key: str | None = None,
    clear_key: bool = False,
    timeout_s: int = DEFAULT_TIMEOUT_S,
) -> AiProviderSettingRow:
    """Upsert one user's provider settings.

    ``api_key=None`` keeps the stored key (the client never sees it, so it
    cannot send it back); ``clear_key=True`` removes it.
    """
    row = load_row(db, user_id)
    if row is None:
        row = AiProviderSettingRow(user_id=user_id)
        db.add(row)
    row.provider = provider
    row.base_url = normalize_base_url(base_url)
    row.model = (model or "").strip()
    if clear_key:
        row.api_key = ""
    elif api_key is not None and api_key != "":
        row.api_key = api_key
    row.timeout_s = int(timeout_s)
    db.commit()
    db.refresh(row)
    return row


def env_provider_name() -> str:
    """Environment-configured provider name (os.environ, else .env via settings).

    Normalized for the panel (P12): the retired ``"stub"`` — which is also the
    config default — reads as ``""``, i.e. "the environment names no provider".
    That keeps the field meaningful: non-empty means the deploy config names a
    real provider, never the built-in storyteller.
    """
    name = (os.environ.get("AI_PROVIDER") or "").strip().lower()
    if not name:
        name = (config.settings.AI_PROVIDER or "").strip().lower()
    return "" if name in _NOT_CONNECTED else name


def env_connection() -> AiConnection:
    """The environment's connection — the same rules as a saved row (P12).

    An unset/``stub`` environment and one that cannot build a provider (missing
    base URL or model) both read as NOT CONNECTED: a broken deploy config is
    reported honestly instead of being silently swapped for the stub.
    """
    name = env_provider_name()
    if name in _NOT_CONNECTED:
        return AiConnection(None, "", "default", reason="unset")
    if name != "openai-compatible":
        return AiConnection(None, "", "env", reason="unsupported")
    try:
        provider = get_provider()
    except (ProviderError, ValueError):
        return AiConnection(None, "", "env", reason="misconfigured")
    if isinstance(provider, StubProvider):  # pragma: no cover - only for an unset env
        return AiConnection(None, "", "default", reason="unset")
    return AiConnection(
        provider,
        "openai-compatible",
        "env",
        model=str(getattr(provider, "model_name", "") or ""),
        base_url=str(getattr(provider, "base_url", "") or ""),
    )


def _resolve_row(row: AiProviderSettingRow) -> AiConnection:
    """One saved row -> a connection (never raises, never a silent stub)."""
    name = (row.provider or "").strip().lower()
    if name in _NOT_CONNECTED:
        return AiConnection(None, "", "settings", reason="stub" if name == "stub" else "unset")
    if name not in PROVIDERS:
        return AiConnection(None, "", "settings", reason="unsupported")
    base_url = normalize_base_url(row.base_url)
    model = (row.model or "").strip()
    if not base_url or not model:
        # A saved-but-incomplete endpoint is not a provider. The old code fell
        # back to the stub HERE while the panel still claimed the endpoint —
        # exactly the disagreement this single resolution removes.
        return AiConnection(None, "", "settings", reason="incomplete")
    return AiConnection(
        OpenAICompatibleProvider(
            base_url=base_url,
            model=model,
            api_key=row.api_key,
            timeout_s=row.timeout_s,
            session_id=row.user_id,
        ),
        "openai-compatible",
        "settings",
        model=model,
        base_url=base_url,
    )


def resolve(db: Session, user_id: str) -> AiConnection:
    """The one resolution: saved settings beat the environment, else nothing.

    Every surface — the Settings panel's ``active_*`` and the play path's
    provider — reads THIS function, so they can never disagree.
    """
    row = load_row(db, user_id)
    if row is not None:
        return _resolve_row(row)
    return env_connection()


def active_provider(db: Session, user_id: str) -> tuple[str, str]:
    """(provider name, source) that ``/act`` will actually narrate with.

    ``("", source)`` when nothing is connected — there is no stub to report.
    """
    conn = resolve(db, user_id)
    return conn.name, conn.source


def settings_doc(db: Session, user_id: str) -> dict[str, Any]:
    """Client-facing settings snapshot; never includes the API key.

    ``connected``/``active_*`` are the resolution the play path uses (P12):
    they name precisely what ``/act`` will narrate with — or that nothing is
    connected, and why.
    """
    row = load_row(db, user_id)
    conn = resolve(db, user_id)
    return {
        "provider": row.provider if row is not None else None,
        "configured": row is not None,
        "base_url": row.base_url if row is not None else "",
        "model": row.model if row is not None else "",
        "has_key": bool(row is not None and row.api_key),
        "timeout_s": row.timeout_s if row is not None else DEFAULT_TIMEOUT_S,
        "connected": conn.connected,
        "active_provider": conn.name,
        "active_source": conn.source,
        "active_model": conn.model,
        "active_base_url": conn.base_url,
        "active_reason": conn.reason,
        "env_provider": env_provider_name(),
        "providers": list(PROVIDERS),
    }


def resolve_provider(db: Session, user_id: str) -> Provider | None:
    """The provider ``/act`` should narrate with — ``None`` when none is connected.

    Precedence: saved settings > environment > nothing. Never raises, never
    returns the built-in stub: a caller without a provider must refuse the
    model-dependent turn (``400 connect_your_ai``), not narrate a placeholder.
    """
    return resolve(db, user_id).provider
