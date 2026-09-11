"""NPC / Faction schema (Stream A owns tables; narrative streams own behavior)."""
from __future__ import annotations

from sqlalchemy import ForeignKey, Integer, String, Text
from sqlalchemy.orm import Mapped, mapped_column

from app.core.database import Base
from app.modules.campaign.world import _uid


class NPC(Base):
    __tablename__ = "npcs"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=_uid)
    campaign_id: Mapped[str] = mapped_column(
        String(36), ForeignKey("campaigns.id", ondelete="CASCADE"), index=True, nullable=False
    )
    name: Mapped[str] = mapped_column(String(200), nullable=False)
    role: Mapped[str] = mapped_column(String(200), default="", nullable=False)
    location_id: Mapped[str | None] = mapped_column(String(36), ForeignKey("locations.id", ondelete="SET NULL"))
    faction_id: Mapped[str | None] = mapped_column(String(36), ForeignKey("factions.id", ondelete="SET NULL"))
    disposition: Mapped[str] = mapped_column(String(32), default="neutral", nullable=False)
    description: Mapped[str] = mapped_column(Text, default="", nullable=False)


class NPCMemory(Base):
    __tablename__ = "npc_memories"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=_uid)
    campaign_id: Mapped[str] = mapped_column(
        String(36), ForeignKey("campaigns.id", ondelete="CASCADE"), index=True, nullable=False
    )
    npc_id: Mapped[str] = mapped_column(String(36), ForeignKey("npcs.id", ondelete="CASCADE"), nullable=False)
    memory: Mapped[str] = mapped_column(Text, nullable=False)
    salience: Mapped[int] = mapped_column(Integer, default=1, nullable=False)


class NPCGoal(Base):
    __tablename__ = "npc_goals"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=_uid)
    campaign_id: Mapped[str] = mapped_column(
        String(36), ForeignKey("campaigns.id", ondelete="CASCADE"), index=True, nullable=False
    )
    npc_id: Mapped[str] = mapped_column(String(36), ForeignKey("npcs.id", ondelete="CASCADE"), nullable=False)
    goal: Mapped[str] = mapped_column(Text, nullable=False)
    status: Mapped[str] = mapped_column(String(32), default="active", nullable=False)


class NPCRelationship(Base):
    """Directed NPC -> NPC (or NPC -> character id) attitude."""

    __tablename__ = "npc_relationships"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=_uid)
    campaign_id: Mapped[str] = mapped_column(
        String(36), ForeignKey("campaigns.id", ondelete="CASCADE"), index=True, nullable=False
    )
    from_npc_id: Mapped[str] = mapped_column(String(36), ForeignKey("npcs.id", ondelete="CASCADE"), nullable=False)
    to_entity_id: Mapped[str] = mapped_column(String(100), nullable=False)
    attitude: Mapped[int] = mapped_column(Integer, default=0, nullable=False)  # -100..100
    note: Mapped[str] = mapped_column(Text, default="", nullable=False)


class Faction(Base):
    __tablename__ = "factions"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=_uid)
    campaign_id: Mapped[str] = mapped_column(
        String(36), ForeignKey("campaigns.id", ondelete="CASCADE"), index=True, nullable=False
    )
    name: Mapped[str] = mapped_column(String(200), nullable=False)
    creed: Mapped[str] = mapped_column(Text, default="", nullable=False)


class FactionRelationship(Base):
    __tablename__ = "faction_relationships"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=_uid)
    campaign_id: Mapped[str] = mapped_column(
        String(36), ForeignKey("campaigns.id", ondelete="CASCADE"), index=True, nullable=False
    )
    from_faction_id: Mapped[str] = mapped_column(
        String(36), ForeignKey("factions.id", ondelete="CASCADE"), nullable=False
    )
    to_faction_id: Mapped[str] = mapped_column(
        String(36), ForeignKey("factions.id", ondelete="CASCADE"), nullable=False
    )
    stance: Mapped[str] = mapped_column(String(32), default="neutral", nullable=False)
    value: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
