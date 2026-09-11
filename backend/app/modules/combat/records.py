"""Snapshot persistence for combat states (SQLAlchemy side of the contract)."""
from __future__ import annotations

from datetime import UTC, datetime

from sqlalchemy import DateTime, String, Text
from sqlalchemy.orm import Mapped, mapped_column

from app.core.database import Base


class CombatRecord(Base):
    """Latest snapshot per combat id. History itself lives in GameEvents."""

    __tablename__ = "combat_records"

    id: Mapped[str] = mapped_column(String(64), primary_key=True)
    campaign_id: Mapped[str] = mapped_column(String(64), default="default")
    snapshot: Mapped[str] = mapped_column(Text, default="{}")
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=lambda: datetime.now(UTC)
    )
