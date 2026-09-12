"""Story / scene / combat schema (Stream A owns tables; systems streams own rules)."""
from __future__ import annotations

import uuid
from datetime import UTC, datetime

from sqlalchemy import DateTime, ForeignKey, Integer, String, Text
from sqlalchemy.orm import Mapped, mapped_column

from app.core.database import Base


def _utcnow() -> datetime:
    return datetime.now(UTC)


def _uid() -> str:
    return str(uuid.uuid4())


class StoryLead(Base):
    __tablename__ = "story_leads"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=_uid)
    campaign_id: Mapped[str] = mapped_column(
        String(36), ForeignKey("campaigns.id", ondelete="CASCADE"), index=True, nullable=False
    )
    title: Mapped[str] = mapped_column(String(200), nullable=False)
    status: Mapped[str] = mapped_column(String(32), default="open", nullable=False)  # open|advanced|closed
    summary: Mapped[str] = mapped_column(Text, default="", nullable=False)


class LeadClue(Base):
    __tablename__ = "lead_clues"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=_uid)
    campaign_id: Mapped[str] = mapped_column(
        String(36), ForeignKey("campaigns.id", ondelete="CASCADE"), index=True, nullable=False
    )
    lead_id: Mapped[str] = mapped_column(String(36), ForeignKey("story_leads.id", ondelete="CASCADE"), nullable=False)
    clue: Mapped[str] = mapped_column(Text, nullable=False)
    found: Mapped[str] = mapped_column(String(16), default="false", nullable=False)


class NarrativeMemory(Base):
    __tablename__ = "narrative_memories"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=_uid)
    campaign_id: Mapped[str] = mapped_column(
        String(36), ForeignKey("campaigns.id", ondelete="CASCADE"), index=True, nullable=False
    )
    kind: Mapped[str] = mapped_column(String(64), default="note", nullable=False)
    text: Mapped[str] = mapped_column(Text, nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_utcnow, nullable=False)


class Scene(Base):
    __tablename__ = "scenes"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=_uid)
    campaign_id: Mapped[str] = mapped_column(
        String(36), ForeignKey("campaigns.id", ondelete="CASCADE"), index=True, nullable=False
    )
    location_id: Mapped[str | None] = mapped_column(String(36), ForeignKey("locations.id", ondelete="SET NULL"))
    title: Mapped[str] = mapped_column(String(200), default="", nullable=False)
    state: Mapped[str] = mapped_column(String(32), default="active", nullable=False)  # active|resolved
    state_json: Mapped[str] = mapped_column(Text, default="{}", nullable=False)  # resolved state snapshot
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_utcnow, nullable=False)


class SceneEvent(Base):
    __tablename__ = "scene_events"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=_uid)
    campaign_id: Mapped[str] = mapped_column(
        String(36), ForeignKey("campaigns.id", ondelete="CASCADE"), index=True, nullable=False
    )
    scene_id: Mapped[str] = mapped_column(String(36), ForeignKey("scenes.id", ondelete="CASCADE"), nullable=False)
    kind: Mapped[str] = mapped_column(String(64), nullable=False)
    payload_json: Mapped[str] = mapped_column(Text, default="{}", nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_utcnow, nullable=False)


class PlayerAction(Base):
    __tablename__ = "player_actions"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=_uid)
    campaign_id: Mapped[str] = mapped_column(
        String(36), ForeignKey("campaigns.id", ondelete="CASCADE"), index=True, nullable=False
    )
    character_id: Mapped[str | None] = mapped_column(String(36), ForeignKey("characters.id", ondelete="SET NULL"))
    scene_id: Mapped[str | None] = mapped_column(String(36), ForeignKey("scenes.id", ondelete="SET NULL"))
    action: Mapped[str] = mapped_column(Text, nullable=False)
    result_json: Mapped[str] = mapped_column(Text, default="{}", nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_utcnow, nullable=False)


class Combat(Base):
    __tablename__ = "combats"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=_uid)
    campaign_id: Mapped[str] = mapped_column(
        String(36), ForeignKey("campaigns.id", ondelete="CASCADE"), index=True, nullable=False
    )
    scene_id: Mapped[str | None] = mapped_column(String(36), ForeignKey("scenes.id", ondelete="SET NULL"))
    status: Mapped[str] = mapped_column(String(32), default="ongoing", nullable=False)
    round: Mapped[int] = mapped_column(Integer, default=1, nullable=False)


class CombatParticipant(Base):
    __tablename__ = "combat_participants"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=_uid)
    campaign_id: Mapped[str] = mapped_column(
        String(36), ForeignKey("campaigns.id", ondelete="CASCADE"), index=True, nullable=False
    )
    combat_id: Mapped[str] = mapped_column(String(36), ForeignKey("combats.id", ondelete="CASCADE"), nullable=False)
    entity_id: Mapped[str] = mapped_column(String(100), nullable=False)
    side: Mapped[str] = mapped_column(String(32), default="foe", nullable=False)
    hp: Mapped[int] = mapped_column(Integer, default=1, nullable=False)
    initiative: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
