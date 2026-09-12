"""Campaign, time, summary, and save-slot models (Stream A)."""
from __future__ import annotations

import uuid
from datetime import UTC, datetime
from typing import TYPE_CHECKING

from sqlalchemy import DateTime, ForeignKey, Integer, String, Text
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.core.database import Base

if TYPE_CHECKING:
    from app.modules.auth.models import User


def _utcnow() -> datetime:
    return datetime.now(UTC)


def _uid() -> str:
    return str(uuid.uuid4())


class Campaign(Base):
    __tablename__ = "campaigns"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=_uid)
    owner_user_id: Mapped[str] = mapped_column(
        String(36), ForeignKey("users.id", ondelete="CASCADE"), index=True, nullable=False
    )
    name: Mapped[str] = mapped_column(String(200), nullable=False)
    seed_key: Mapped[str] = mapped_column(String(100), nullable=False, default="custom")
    status: Mapped[str] = mapped_column(String(32), nullable=False, default="active")  # active|paused|completed
    current_scene_id: Mapped[str | None] = mapped_column(String(36), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_utcnow, nullable=False)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=_utcnow, onupdate=_utcnow, nullable=False
    )

    owner: Mapped[User] = relationship("User", back_populates="campaigns", lazy="joined")


class GameTime(Base):
    """Campaign clock (§GDD time). One row per campaign."""

    __tablename__ = "game_time"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=_uid)
    campaign_id: Mapped[str] = mapped_column(
        String(36), ForeignKey("campaigns.id", ondelete="CASCADE"), unique=True, index=True, nullable=False
    )
    day: Mapped[int] = mapped_column(Integer, default=1, nullable=False)
    hour: Mapped[int] = mapped_column(Integer, default=8, nullable=False)
    minute: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=_utcnow, onupdate=_utcnow, nullable=False
    )


class CampaignSummary(Base):
    """Rolling summary row per campaign (other streams append; A owns the table)."""

    __tablename__ = "campaign_summaries"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=_uid)
    campaign_id: Mapped[str] = mapped_column(
        String(36), ForeignKey("campaigns.id", ondelete="CASCADE"), unique=True, index=True, nullable=False
    )
    text: Mapped[str] = mapped_column(Text, default="", nullable=False)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=_utcnow, onupdate=_utcnow, nullable=False
    )


class SaveGame(Base):
    """Named save slots + autosave checkpoints. Snapshot is resolved state JSON."""

    __tablename__ = "saves"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=_uid)
    campaign_id: Mapped[str] = mapped_column(
        String(36), ForeignKey("campaigns.id", ondelete="CASCADE"), index=True, nullable=False
    )
    slot: Mapped[str] = mapped_column(String(64), nullable=False, default="manual")  # manual|autosave
    label: Mapped[str] = mapped_column(String(200), nullable=False, default="")
    checkpoint: Mapped[str] = mapped_column(String(64), nullable=False, default="manual")
    snapshot: Mapped[str] = mapped_column(Text, nullable=False, default="{}")  # JSON document
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_utcnow, nullable=False)
