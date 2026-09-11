"""Rest: short is small/quick, long is big but needs safety + clock."""
from app.modules.combat.models import Combatant
from app.modules.combat.rest import long_rest, short_rest


def party():
    return [Combatant(id="ash", name="Ash", max_hp=40, hp=20, stamina=20),
            Combatant(id="marla", name="Marla", max_hp=30, hp=30, stamina=100,
                      injuries=["BruisedRibs"])]


def test_short_rest_small_quick_recovery():
    team = party()
    res = short_rest(team)
    assert res.hours_passed == 1
    assert res.hp_restored["ash"] == 10  # 25% of 40
    assert team[0].hp == 30
    assert team[0].stamina == 20 + 50
    assert not res.interrupted


def test_long_rest_big_recovery_heals_injury():
    team = party()
    res = long_rest(team, safe=True, danger=0)
    assert res.hours_passed == 8
    assert team[0].hp == 40 and team[0].stamina == 100
    assert team[1].injuries == []  # one lasting injury closed
    assert team[1].hp == 30
    assert not res.interrupted


def test_long_rest_in_danger_is_interrupted():
    team = party()
    res = long_rest(team, safe=False, danger=8)
    assert res.interrupted
    assert team[0].hp < 40  # only partial recovery
    assert team[1].injuries == ["BruisedRibs"]  # no healing under threat
    assert "danger" in res.event


def test_unsafe_ground_carries_risk_even_when_claimed_safe():
    team = party()
    res = long_rest(team, safe=True, danger=9)
    assert res.interrupted
