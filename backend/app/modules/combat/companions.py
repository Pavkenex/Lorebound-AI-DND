"""Companions: people, not inventory items.

Each companion has personality, skills, equipment, goals, relationships, a
personal storyline, and opinions about player decisions. Autonomy rules let
them refuse, flee, demand, or leave — and a hard guard stops them ever being
treated as items.
"""
from __future__ import annotations

from enum import Enum

from pydantic import BaseModel, Field


class CompanionOrderKind(str, Enum):
    FIGHT = "fight"
    TORTURE = "torture"
    STEAL = "steal"
    LOOT_SHARE = "loot_share"
    DANGEROUS_TASK = "dangerous_task"
    FOLLOW = "follow"


class CompanionReaction(str, Enum):
    ACCEPT = "accept"
    REFUSE = "refuse"
    FLEE = "flee"
    DEMAND = "demand"
    LEAVE = "leave"


class CompanionNotInventoryError(TypeError):
    pass


class CompanionOrder(BaseModel):
    kind: CompanionOrderKind
    detail: str = ""
    danger: int = 0  # 0-10
    share_offered: int = 0  # percent of loot offered, for LOOT_SHARE


class ReactionResult(BaseModel):
    decision: CompanionReaction
    line: str
    loyalty_delta: int = 0


class Companion(BaseModel):
    id: str
    name: str
    personality: dict[str, str] = Field(default_factory=dict)
    traits: dict[str, int] = Field(default_factory=dict)  # courage/principle/greed 0-10
    skills: list[str] = Field(default_factory=list)
    equipment: list[str] = Field(default_factory=list)
    goals: list[str] = Field(default_factory=list)
    relationships: dict[str, str] = Field(default_factory=dict)
    storyline: str = ""
    opinions: dict[str, str] = Field(default_factory=dict)
    loyalty: int = 50
    status: str = "active"  # active | fled | left

    def trait(self, name: str) -> int:
        return int(self.traits.get(name, 5))

    def react(self, order: CompanionOrder) -> ReactionResult:
        """Autonomy engine. Returns the companion's decision plus their words."""
        if self.status in ("fled", "left"):
            return ReactionResult(decision=CompanionReaction.LEAVE,
                                  line=f"{self.name} is already gone.")

        # 1. The principled refuse cruelty.
        if order.kind == CompanionOrderKind.TORTURE and self.trait("principle") >= 6:
            self.loyalty -= 15
            self.opinions["torture"] = "disgusted"
            if self.loyalty <= 0:
                self.status = "left"
                return ReactionResult(decision=CompanionReaction.LEAVE,
                                      line=f"{self.name} walks away: 'I want no part of this.'",
                                      loyalty_delta=-15)
            return ReactionResult(decision=CompanionReaction.REFUSE,
                                  line=f"{self.name} refuses: 'No. Find another butcher.'",
                                  loyalty_delta=-15)

        # 2. The cowardly flee real danger.
        if order.danger >= 7 and self.trait("courage") <= 3:
            self.status = "fled"
            self.opinions["last_battle"] = "terrified"
            return ReactionResult(decision=CompanionReaction.FLEE,
                                  line=f"{self.name} flees: 'I'm sorry — I can't!'")

        # 3. The greedy demand a bigger share.
        if order.kind == CompanionOrderKind.LOOT_SHARE and self.trait("greed") >= 6:
            fair = 20 + self.trait("greed")
            if order.share_offered < fair:
                self.loyalty -= 5
                self.opinions["loot"] = "insulted"
                return ReactionResult(
                    decision=CompanionReaction.DEMAND,
                    line=f"{self.name} demands more: '{fair}% or I walk.'",
                    loyalty_delta=-5)

        # 4. Broken loyalty ends the relationship.
        if self.loyalty <= 0 and order.kind not in (CompanionOrderKind.FOLLOW,):
            self.status = "left"
            return ReactionResult(decision=CompanionReaction.LEAVE,
                                  line=f"{self.name} leaves the company.")

        self.opinions[order.kind.value] = "agreed"
        return ReactionResult(decision=CompanionReaction.ACCEPT,
                              line=f"{self.name} nods: 'On it.'")

    def record_opinion(self, decision: str, opinion: str) -> None:
        self.opinions[decision] = opinion
        if opinion in ("betrayed", "disgusted", "insulted"):
            self.loyalty = max(0, self.loyalty - 10)
        elif opinion in ("inspired", "grateful", "impressed"):
            self.loyalty = min(100, self.loyalty + 10)
        if self.loyalty <= 0:
            self.status = "left"


def assert_not_item(obj: object) -> None:
    """Guard: companions must never enter inventory flows."""
    if isinstance(obj, Companion):
        raise CompanionNotInventoryError(
            "Companions are people, not inventory items: they cannot be "
            "stored, equipped, traded, or consumed."
        )


def transfer_to_inventory(obj: object, inventory: list) -> None:
    """Inventory intake entry point used by cross-module flows."""
    assert_not_item(obj)
    inventory.append(obj)
