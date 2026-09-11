"""Economy sinks, non-money rewards, anti-infinite-wealth (t_718786b7)."""
from app.modules.economy.economy import (
    NON_MONEY_REWARDS,
    SINKS,
    RewardChannel,
    daily_upkeep,
    quest_rewards,
    sell_price,
)


def test_recurring_sinks_cover_costs_of_life():
    names = {s.name for s in SINKS}
    assert {"food", "lodging", "equipment", "repairs", "training",
            "transport", "healing", "bribes", "crafting materials"} <= names
    assert daily_upkeep() > 0


def test_infinite_wealth_structurally_discouraged():
    modest = daily_upkeep(party_size=1, wealth=100)
    rich = daily_upkeep(party_size=1, wealth=20000)
    assert rich > modest  # carrying costs scale with wealth
    assert sell_price(100, 0) > sell_price(100, 5)  # market floods


def test_rewards_beyond_money():
    assert len(NON_MONEY_REWARDS) >= 10
    rewards = quest_rewards("missing-caravan")
    channels = {r.channel for r in rewards}
    assert RewardChannel.MONEY in channels
    assert len(channels - {RewardChannel.MONEY}) >= 3
