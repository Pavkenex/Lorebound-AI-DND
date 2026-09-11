"""Companions: personalities, autonomy, opinions — never inventory items."""
import pytest

from app.modules.combat.companions import (
    Companion,
    CompanionNotInventoryError,
    CompanionOrder,
    CompanionOrderKind,
    CompanionReaction,
    transfer_to_inventory,
)


def principled():
    return Companion(id="c1", name="Ser Ossa", traits={"principle": 9, "courage": 7, "greed": 2},
                     skills=["swordplay"], equipment=["oathblade"], goals=["protect the weak"],
                     storyline="An oathbreaker seeking redemption.")


def coward():
    return Companion(id="c2", name="Nib", traits={"principle": 4, "courage": 1, "greed": 3},
                     skills=["hiding"], equipment=["lucky stone"], goals=["survive"])


def greedy():
    return Companion(id="c3", name="Vex", traits={"principle": 3, "courage": 6, "greed": 9},
                     skills=["appraisal"], equipment=["loaded dice"], goals=["get rich"])


def test_principled_companion_refuses_torture():
    c = principled()
    res = c.react(CompanionOrder(kind=CompanionOrderKind.TORTURE, detail="torture the prisoner"))
    assert res.decision == CompanionReaction.REFUSE
    assert c.opinions["torture"] == "disgusted"
    assert c.loyalty < 50


def test_repeated_cruelty_ends_relationship():
    c = principled()
    c.loyalty = 10
    res = c.react(CompanionOrder(kind=CompanionOrderKind.TORTURE))
    assert res.decision == CompanionReaction.LEAVE
    assert c.status == "left"


def test_coward_flees_real_danger():
    c = coward()
    res = c.react(CompanionOrder(kind=CompanionOrderKind.DANGEROUS_TASK, danger=9))
    assert res.decision == CompanionReaction.FLEE
    assert c.status == "fled"


def test_greedy_demands_bigger_share():
    c = greedy()
    res = c.react(CompanionOrder(kind=CompanionOrderKind.LOOT_SHARE, share_offered=5))
    assert res.decision == CompanionReaction.DEMAND
    fair = c.react(CompanionOrder(kind=CompanionOrderKind.LOOT_SHARE, share_offered=50))
    assert fair.decision == CompanionReaction.ACCEPT


def test_opinions_tracked_and_loyalty_moves():
    c = principled()
    c.record_opinion("saved the village", "inspired")
    assert c.opinions["saved the village"] == "inspired"
    assert c.loyalty == 60


def test_companion_is_never_an_inventory_item():
    c = principled()
    with pytest.raises(CompanionNotInventoryError):
        transfer_to_inventory(c, [])
    # ...while ordinary loot still flows into inventory.
    bag: list = []
    transfer_to_inventory({"name": "rope", "qty": 1}, bag)
    assert bag == [{"name": "rope", "qty": 1}]
