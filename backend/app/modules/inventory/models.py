"""Item / InventoryEntry / Equipment (Stream A).

Quality scale: Common / Fine / Exceptional / Masterwork / Legendary.
Slots: Head, Chest, Hands, Feet, Main Hand, Off Hand, Neck, Ring, Utility, Back.
"""
from __future__ import annotations

from sqlalchemy import Float, ForeignKey, Integer, String, Text
from sqlalchemy.orm import Mapped, mapped_column

from app.core.database import Base
from app.modules.campaign.world import _uid

EQUIPMENT_SLOTS: tuple[str, ...] = (
    "head", "chest", "hands", "feet", "main_hand",
    "off_hand", "neck", "ring", "utility", "back",
)

QUALITIES: tuple[str, ...] = ("Common", "Fine", "Exceptional", "Masterwork", "Legendary")

QUALITY_RANK: dict[str, int] = {q: i for i, q in enumerate(QUALITIES)}

# Quality shifts properties via multipliers — a single meaningful blade stays
# relevant; no loot treadmill of flat replacements.
QUALITY_CRAFT_BONUS: dict[str, int] = {
    "Common": 0, "Fine": 1, "Exceptional": 2, "Masterwork": 3, "Legendary": 5,
}


class Item(Base):
    __tablename__ = "items"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=_uid)
    campaign_id: Mapped[str] = mapped_column(
        String(36), ForeignKey("campaigns.id", ondelete="CASCADE"), index=True, nullable=False
    )
    name: Mapped[str] = mapped_column(String(200), nullable=False)
    category: Mapped[str] = mapped_column(String(64), default="misc", nullable=False)
    weight: Mapped[float] = mapped_column(Float, default=0.0, nullable=False)
    quality: Mapped[str] = mapped_column(String(32), default="Common", nullable=False)
    value: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    condition: Mapped[int] = mapped_column(Integer, default=100, nullable=False)  # 0..100
    description: Mapped[str] = mapped_column(Text, default="", nullable=False)
    modifiers_json: Mapped[str] = mapped_column(Text, default="{}", nullable=False)
    tags_json: Mapped[str] = mapped_column(Text, default="[]", nullable=False)
    history: Mapped[str] = mapped_column(Text, default="", nullable=False)  # narrative provenance


class InventoryEntry(Base):
    __tablename__ = "inventory_entries"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=_uid)
    campaign_id: Mapped[str] = mapped_column(
        String(36), ForeignKey("campaigns.id", ondelete="CASCADE"), index=True, nullable=False
    )
    character_id: Mapped[str] = mapped_column(
        String(36), ForeignKey("characters.id", ondelete="CASCADE"), index=True, nullable=False
    )
    item_id: Mapped[str] = mapped_column(String(36), ForeignKey("items.id", ondelete="CASCADE"), nullable=False)
    quantity: Mapped[int] = mapped_column(Integer, default=1, nullable=False)
    container: Mapped[str] = mapped_column(String(64), default="pack", nullable=False)


class Equipment(Base):
    """One row per occupied slot per character."""

    __tablename__ = "equipment"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=_uid)
    campaign_id: Mapped[str] = mapped_column(
        String(36), ForeignKey("campaigns.id", ondelete="CASCADE"), index=True, nullable=False
    )
    character_id: Mapped[str] = mapped_column(
        String(36), ForeignKey("characters.id", ondelete="CASCADE"), index=True, nullable=False
    )
    slot: Mapped[str] = mapped_column(String(32), nullable=False)
    item_id: Mapped[str] = mapped_column(String(36), ForeignKey("items.id", ondelete="CASCADE"), nullable=False)
