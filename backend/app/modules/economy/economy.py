"""Economy: currency sinks & reward design (GDD §87-88).

Recurring costs plus reward channels beyond money. Infinite wealth
accumulation is structurally discouraged: upkeep and carrying costs scale
with wealth, and fence prices decay as you flood the market.
"""
from __future__ import annotations

from dataclasses import dataclass
from enum import Enum


class RewardChannel(str, Enum):
    MONEY = "money"
    INFORMATION = "information"
    RELATIONSHIP = "relationship"
    ACCESS = "access"
    TRAINING = "training"
    REPUTATION = "reputation"
    EQUIPMENT = "equipment"
    PROPERTY = "property"
    FAVOUR = "favour"
    STORY = "story_opportunity"
    KNOWLEDGE = "rare_knowledge"
    SKILL = "skill_progression"


NON_MONEY_REWARDS: list[RewardChannel] = [c for c in RewardChannel if c != RewardChannel.MONEY]


@dataclass(frozen=True)
class Sink:
    name: str
    base_cost: int  # coppers per day (or per use where noted)


#: Recurring costs: food, lodging, equipment, repairs, training, transport,
#: healing, bribes, crafting materials.
SINKS: list[Sink] = [
    Sink("food", 3), Sink("lodging", 5), Sink("equipment", 4),
    Sink("repairs", 3), Sink("training", 8), Sink("transport", 6),
    Sink("healing", 7), Sink("bribes", 10), Sink("crafting materials", 5),
]

WEALTH_SOFT_CAP = 5000  # coppers; above this, money attracts costs


def daily_upkeep(party_size: int = 1, lifestyle: float = 1.0, wealth: int = 0) -> int:
    """Base sinks scaled by party/lifestyle, plus progressive wealth costs."""
    base = sum(s.base_cost for s in SINKS) * party_size * lifestyle
    carrying_fee = 0
    if wealth > WEALTH_SOFT_CAP:
        carrying_fee = int((wealth - WEALTH_SOFT_CAP) * 0.02)  # guards, storage, taxes, theft
    return int(base + carrying_fee)


def sell_price(base_value: int, units_already_sold: int) -> int:
    """Flooding the market decays prices: each prior sale shaves 10%."""
    return max(1, int(base_value * (0.9 ** units_already_sold)))


@dataclass(frozen=True)
class Reward:
    channel: RewardChannel
    description: str
    money_value: int = 0


def quest_rewards(quest_id: str) -> list[Reward]:
    """Quests pay mostly in non-money channels by design."""
    return [
        Reward(RewardChannel.MONEY, f"{quest_id}: modest coin", money_value=40),
        Reward(RewardChannel.RELATIONSHIP, f"{quest_id}: a friend in need"),
        Reward(RewardChannel.INFORMATION, f"{quest_id}: what the guild hides"),
        Reward(RewardChannel.SKILL, f"{quest_id}: lessons learned"),
    ]
