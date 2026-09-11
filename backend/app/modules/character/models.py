"""Character + Attribute/Skill/Trait/Relationship (Stream A)."""
from __future__ import annotations

from sqlalchemy import ForeignKey, Integer, String, Text
from sqlalchemy.orm import Mapped, mapped_column

from app.core.database import Base
from app.modules.campaign.world import _uid


class Character(Base):
    __tablename__ = "characters"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=_uid)
    campaign_id: Mapped[str] = mapped_column(
        String(36), ForeignKey("campaigns.id", ondelete="CASCADE"), index=True, nullable=False
    )
    name: Mapped[str] = mapped_column(String(200), nullable=False)
    pronouns: Mapped[str] = mapped_column(String(40), default="", nullable=False)
    appearance: Mapped[str] = mapped_column(Text, default="", nullable=False)
    age_range: Mapped[str] = mapped_column(String(40), default="", nullable=False)
    homeland: Mapped[str] = mapped_column(String(120), default="", nullable=False)
    portrait_url: Mapped[str] = mapped_column(String(500), default="", nullable=False)
    background: Mapped[str] = mapped_column(String(100), default="", nullable=False)
    drives_json: Mapped[str] = mapped_column(Text, default="[]", nullable=False)
    resolve_current: Mapped[int] = mapped_column(Integer, default=2, nullable=False)
    resolve_max: Mapped[int] = mapped_column(Integer, default=2, nullable=False)
    hp_current: Mapped[int] = mapped_column(Integer, default=10, nullable=False)
    hp_max: Mapped[int] = mapped_column(Integer, default=10, nullable=False)
    stamina_current: Mapped[int] = mapped_column(Integer, default=6, nullable=False)
    stamina_max: Mapped[int] = mapped_column(Integer, default=6, nullable=False)
    level: Mapped[int] = mapped_column(Integer, default=1, nullable=False)
    xp: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    creation_stage: Mapped[int] = mapped_column(Integer, default=6, nullable=False)  # 1..6 completed
    creation_json: Mapped[str] = mapped_column(Text, default="{}", nullable=False)


class CharacterAttribute(Base):
    __tablename__ = "character_attributes"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=_uid)
    campaign_id: Mapped[str] = mapped_column(
        String(36), ForeignKey("campaigns.id", ondelete="CASCADE"), index=True, nullable=False
    )
    character_id: Mapped[str] = mapped_column(
        String(36), ForeignKey("characters.id", ondelete="CASCADE"), index=True, nullable=False
    )
    name: Mapped[str] = mapped_column(String(32), nullable=False)  # Might|Agility|...
    value: Mapped[int] = mapped_column(Integer, nullable=False)


class Skill(Base):
    __tablename__ = "skills"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=_uid)
    campaign_id: Mapped[str] = mapped_column(
        String(36), ForeignKey("campaigns.id", ondelete="CASCADE"), index=True, nullable=False
    )
    character_id: Mapped[str] = mapped_column(
        String(36), ForeignKey("characters.id", ondelete="CASCADE"), index=True, nullable=False
    )
    name: Mapped[str] = mapped_column(String(100), nullable=False)
    rank: Mapped[int] = mapped_column(Integer, default=1, nullable=False)
    source: Mapped[str] = mapped_column(String(100), default="", nullable=False)  # background|training|...


class Trait(Base):
    __tablename__ = "traits"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=_uid)
    campaign_id: Mapped[str] = mapped_column(
        String(36), ForeignKey("campaigns.id", ondelete="CASCADE"), index=True, nullable=False
    )
    character_id: Mapped[str] = mapped_column(
        String(36), ForeignKey("characters.id", ondelete="CASCADE"), index=True, nullable=False
    )
    name: Mapped[str] = mapped_column(String(100), nullable=False)
    description: Mapped[str] = mapped_column(Text, default="", nullable=False)


class CharacterRelationship(Base):
    """Directed character -> entity bond."""

    __tablename__ = "character_relationships"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=_uid)
    campaign_id: Mapped[str] = mapped_column(
        String(36), ForeignKey("campaigns.id", ondelete="CASCADE"), index=True, nullable=False
    )
    character_id: Mapped[str] = mapped_column(
        String(36), ForeignKey("characters.id", ondelete="CASCADE"), index=True, nullable=False
    )
    to_entity_id: Mapped[str] = mapped_column(String(100), nullable=False)
    bond: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    note: Mapped[str] = mapped_column(Text, default="", nullable=False)
