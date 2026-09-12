"""World / Region / Location / LocationState / WorldFact tables (Stream A owns schema).

WorldFact field set per GDD §100: subjectType, subjectId, predicate, object,
truthState, confidence, visibility, createdAt, sourceEventId.
"""
from __future__ import annotations

import uuid
from datetime import UTC, datetime

from sqlalchemy import DateTime, Float, ForeignKey, String, Text
from sqlalchemy.orm import Mapped, mapped_column

from app.core.database import Base


def _utcnow() -> datetime:
    return datetime.now(UTC)


def _uid() -> str:
    return str(uuid.uuid4())


class World(Base):
    __tablename__ = "worlds"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=_uid)
    campaign_id: Mapped[str] = mapped_column(
        String(36), ForeignKey("campaigns.id", ondelete="CASCADE"), index=True, nullable=False
    )
    name: Mapped[str] = mapped_column(String(200), nullable=False)
    description: Mapped[str] = mapped_column(Text, default="", nullable=False)


class Region(Base):
    __tablename__ = "regions"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=_uid)
    campaign_id: Mapped[str] = mapped_column(
        String(36), ForeignKey("campaigns.id", ondelete="CASCADE"), index=True, nullable=False
    )
    world_id: Mapped[str] = mapped_column(String(36), ForeignKey("worlds.id", ondelete="CASCADE"), nullable=False)
    name: Mapped[str] = mapped_column(String(200), nullable=False)
    description: Mapped[str] = mapped_column(Text, default="", nullable=False)


class Location(Base):
    __tablename__ = "locations"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=_uid)
    campaign_id: Mapped[str] = mapped_column(
        String(36), ForeignKey("campaigns.id", ondelete="CASCADE"), index=True, nullable=False
    )
    region_id: Mapped[str | None] = mapped_column(String(36), ForeignKey("regions.id", ondelete="SET NULL"))
    name: Mapped[str] = mapped_column(String(200), nullable=False)
    description: Mapped[str] = mapped_column(Text, default="", nullable=False)


class LocationState(Base):
    """Mutable per-location state (control, danger, discovered, extra flags as JSON text)."""

    __tablename__ = "location_states"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=_uid)
    campaign_id: Mapped[str] = mapped_column(
        String(36), ForeignKey("campaigns.id", ondelete="CASCADE"), index=True, nullable=False
    )
    location_id: Mapped[str] = mapped_column(
        String(36), ForeignKey("locations.id", ondelete="CASCADE"), unique=True, nullable=False
    )
    discovered: Mapped[str] = mapped_column(String(16), default="false", nullable=False)
    danger: Mapped[str] = mapped_column(String(32), default="calm", nullable=False)
    flags_json: Mapped[str] = mapped_column(Text, default="{}", nullable=False)


class WorldFact(Base):
    """Canonical hidden/known truths. `visibility`: 'hidden' | 'party' | 'public'."""

    __tablename__ = "world_facts"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=_uid)
    campaign_id: Mapped[str] = mapped_column(
        String(36), ForeignKey("campaigns.id", ondelete="CASCADE"), index=True, nullable=False
    )
    subject_type: Mapped[str] = mapped_column(String(64), nullable=False)
    subject_id: Mapped[str] = mapped_column(String(100), nullable=False, default="")
    predicate: Mapped[str] = mapped_column(String(100), nullable=False)
    object: Mapped[str] = mapped_column("object", Text, nullable=False, default="")
    truth_state: Mapped[str] = mapped_column(String(32), nullable=False, default="true")  # true|false|rumor
    confidence: Mapped[float] = mapped_column(Float, default=1.0, nullable=False)
    visibility: Mapped[str] = mapped_column(String(32), nullable=False, default="public")
    source_event_id: Mapped[str] = mapped_column(String(100), default="", nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_utcnow, nullable=False)
