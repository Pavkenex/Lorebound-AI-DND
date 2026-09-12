"""Bring-your-own AI provider settings (per user).

Players can point narration at any OpenAI-compatible chat-completions
endpoint from Settings; when no settings are saved the environment
configuration applies (``AI_PROVIDER``, default ``stub``). One row per user.
The API key is stored server-side and never returned to the client — reads
expose only ``has_key``.
"""
from __future__ import annotations

import os
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
PROVIDERS: tuple[str, ...] = ("stub", "openai-compatible")


def _utcnow() -> datetime:
    return datetime.now(UTC)


class AiProviderSettingRow(Base):
    """One user's chosen text provider (empty row values mean 'not configured')."""

    __tablename__ = "ai_provider_settings"

    user_id: Mapped[str] = mapped_column(
        String(36), ForeignKey("users.id", ondelete="CASCADE"), primary_key=True
    )
    provider: Mapped[str] = mapped_column(String(32), nullable=False, default="stub")
    base_url: Mapped[str] = mapped_column(String(500), nullable=False, default="")
    model: Mapped[str] = mapped_column(String(200), nullable=False, default="")
    api_key: Mapped[str] = mapped_column(String(500), nullable=False, default="")
    timeout_s: Mapped[int] = mapped_column(Integer, nullable=False, default=DEFAULT_TIMEOUT_S)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=_utcnow, onupdate=_utcnow, nullable=False
    )


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
    """Environment-configured provider name (os.environ, else .env via settings)."""
    name = (os.environ.get("AI_PROVIDER") or "").strip().lower()
    if not name:
        name = (config.settings.AI_PROVIDER or "stub").strip().lower()
    return name or "stub"


def active_provider(db: Session, user_id: str) -> tuple[str, str]:
    """(provider name, source) that ``/act`` will actually narrate with."""
    row = load_row(db, user_id)
    if row is not None:
        return row.provider, "settings"
    name = env_provider_name()
    if name not in ("", "stub"):
        try:
            get_provider()
            return name, "env"
        except (ProviderError, ValueError):
            pass
    return "stub", "default"


def settings_doc(db: Session, user_id: str) -> dict[str, Any]:
    """Client-facing settings snapshot; never includes the API key."""
    row = load_row(db, user_id)
    active, source = active_provider(db, user_id)
    return {
        "provider": row.provider if row is not None else None,
        "configured": row is not None,
        "base_url": row.base_url if row is not None else "",
        "model": row.model if row is not None else "",
        "has_key": bool(row is not None and row.api_key),
        "timeout_s": row.timeout_s if row is not None else DEFAULT_TIMEOUT_S,
        "active_provider": active,
        "active_source": source,
        "env_provider": env_provider_name(),
    }


def resolve_provider(db: Session, user_id: str) -> Provider:
    """The provider ``/act`` should narrate with for this user.

    Precedence: saved settings > environment > stub. Never raises: a broken
    environment configuration degrades to the stub so the chronicle stays
    playable (surface configuration errors via ``/ai/settings/test``).
    """
    row = load_row(db, user_id)
    if row is None:
        try:
            return get_provider()
        except (ProviderError, ValueError):
            return StubProvider()
    if row.provider == "openai-compatible" and row.base_url and row.model:
        return OpenAICompatibleProvider(
            base_url=row.base_url,
            model=row.model,
            api_key=row.api_key,
            timeout_s=row.timeout_s,
        )
    return StubProvider()
