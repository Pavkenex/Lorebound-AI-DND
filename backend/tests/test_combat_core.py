"""Core state, range bands, turns, NL-action compatibility."""
from app.modules.combat.engine import CombatEngine
from app.modules.combat.models import Combatant, Cover, RangeBand, Weapon
from app.modules.combat.turns import (
    ActionKind,
    ActionSlot,
    SlotAction,
    traditional_turn,
    validate_turn,
)


def make_fighter(name="Ash", enemy=False, **kw):
    return Combatant(id=name.lower(), name=name, is_enemy=enemy, **kw)


def test_range_bands_default_and_move():
    eng = CombatEngine()
    a, b = make_fighter("Ash"), make_fighter("Bandit", enemy=True)
    state = eng.start([a, b])
    assert state.get_range("ash", "bandit") == RangeBand.NEAR
    state.set_range("ash", "bandit", RangeBand.FAR)
    assert eng.submit_turn(state, "ash", text="I rush the bandit").resolved_kind != "attack_out_of_range"
    assert state.get_range("ash", "bandit") == RangeBand.NEAR


def test_melee_out_of_range_at_far():
    eng = CombatEngine()
    a = make_fighter("Ash")
    b = make_fighter("Bandit", enemy=True)
    state = eng.start([a, b], ranges={("ash", "bandit"): RangeBand.FAR})
    res = eng.submit_turn(state, "ash", text="I slash the bandit with my sword")
    assert res.resolved_kind == "attack_out_of_range"
    assert b.hp == b.max_hp


def test_sprint_closes_two_bands():
    eng = CombatEngine()
    a, b = make_fighter("Ash"), make_fighter("Bandit", enemy=True)
    state = eng.start([a, b], ranges={("ash", "bandit"): RangeBand.DISTANT})
    eng.submit_turn(state, "ash", text="I sprint toward the bandit")
    assert state.get_range("ash", "bandit") == RangeBand.NEAR


def test_traditional_and_free_text_turns_coexist():
    eng = CombatEngine()
    a = make_fighter("Ash")
    b = make_fighter("Bandit", enemy=True)
    state = eng.start([a, b], ranges={("ash", "bandit"): RangeBand.ENGAGED})
    explicit = traditional_turn(
        primary=SlotAction(slot=ActionSlot.PRIMARY, kind=ActionKind.ATTACK_LIGHT, target_id="bandit"))
    assert validate_turn(explicit) == []
    r1 = eng.submit_turn(state, "ash", turn=explicit)
    assert r1.resolved_kind == "attack_hit"
    r2 = eng.submit_turn(state, "bandit", text="I swing my cudgel at Ash")
    assert r2.resolved_kind == "attack_hit"


def test_state_tracks_cover_conditions_weapon_armour():
    c = Combatant(id="m", name="Marla", cover=Cover.HALF, conditions=["prone"],
                  weapon=Weapon(name="Longbow", damage=8, ranged=True, reach=RangeBand.FAR))
    assert c.cover.ranged_bonus() == 2
    assert "prone" in c.conditions
    assert c.weapon.ranged


def test_ranged_attack_respects_cover_but_melee_ignores():
    eng = CombatEngine()
    archer = make_fighter("Archer", enemy=True,
                          weapon=Weapon(name="Bow", damage=8, ranged=True, reach=RangeBand.FAR))
    marla = Combatant(id="marla", name="Marla", cover=Cover.FULL)
    state = eng.start([archer, marla], ranges={("archer", "marla"): RangeBand.FAR})
    res = eng.submit_turn(state, "archer", text="I shoot Marla with my bow")
    # DC 8+5=13 vs roll 10 -> miss behind full cover
    assert res.resolved_kind == "attack_miss"
