"""Postgres side-tables for engine campaigns (docs/INTEGRATION_PLAN.md §3).

Runtime-only keys by design: provider API keys travel per request
(``X-Provider-Key``), live in server memory for that one call, and are never
persisted — ``EngineConnectionRow`` deliberately has NO key column, and the
module must never grow one (plan §4). Game state itself is not here: it lives
in the campaign's own SQLite file (``paths.campaign_db_path``).

Model gate (P11): a connection is either connected to a real provider or not.
The ``provider`` column defaults to ``""`` (unset = not connected); an older
row still carrying the retired ``"stub"`` default reads identically — see
``bridge.connection_provider``. Client-side default only, so no migration.
"""
from __future__ import annotations

from datetime import UTC, datetime

from sqlalchemy import DateTime, ForeignKey, Integer, String
from sqlalchemy.orm import Mapped, mapped_column

from app.core.database import Base


def _utcnow() -> datetime:
    return datetime.now(UTC)


class EngineCampaignRow(Base):
    """App-side bookkeeping for a campaign played on the rebuilt engine."""

    __tablename__ = "engine_campaigns"

    campaign_id: Mapped[str] = mapped_column(
        String(36), ForeignKey("campaigns.id", ondelete="CASCADE"), primary_key=True
    )
    world: Mapped[str] = mapped_column(String(100), nullable=False, default="demo")
    engine_version: Mapped[str] = mapped_column(String(32), nullable=False, default="")
    last_turn_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=_utcnow, nullable=False
    )


class EngineConnectionRow(Base):
    """Non-secret BYOK prefs per user: provider, base_url, model, timeout only.

    No key column exists here, by design (plan §4): the runtime key never
    reaches Postgres, logs, or prompts.
    """

    __tablename__ = "engine_connection_settings"

    user_id: Mapped[str] = mapped_column(
        String(36), ForeignKey("users.id", ondelete="CASCADE"), primary_key=True
    )
    provider: Mapped[str] = mapped_column(String(32), nullable=False, default="")
    base_url: Mapped[str] = mapped_column(String(500), nullable=False, default="")
    model: Mapped[str] = mapped_column(String(200), nullable=False, default="")
    timeout_s: Mapped[int] = mapped_column(Integer, nullable=False, default=30)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=_utcnow, onupdate=_utcnow, nullable=False
    )
