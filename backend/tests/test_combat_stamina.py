"""Stamina economy: costs, regen, anti-spam escalation."""
from app.modules.combat import stamina as st
from app.modules.combat.engine import CombatEngine
from app.modules.combat.models import Combatant, RangeBand, Weapon
from app.modules.combat.turns import ActionKind, ActionSlot, SlotAction, traditional_turn


def heavy_on(foe_id="bandit"):
    return traditional_turn(
        primary=SlotAction(slot=ActionSlot.PRIMARY, kind=ActionKind.ATTACK_HEAVY, target_id=foe_id))


def test_heavy_costs_more_than_light_and_regen_ticks():
    c = Combatant(id="a", name="Ash", stamina=100)
    r1 = st.spend_stamina(c, ActionKind.ATTACK_HEAVY.value)
    assert r1.cost == 25 and c.stamina == 75
    c2 = Combatant(id="b", name="Bo", stamina=100)
    r2 = st.spend_stamina(c2, ActionKind.ATTACK_LIGHT.value)
    assert r2.cost == 5
    gained = st.regenerate(c)
    assert gained == st.REGEN_PER_ROUND and c.stamina == 75 + st.REGEN_PER_ROUND


def test_spamming_heavy_is_self_defeating():
    eng = CombatEngine()
    ash = Combatant(id="ash", name="Ash", weapon=Weapon(name="Greataxe", damage=10, heavy=True))
    foe = Combatant(id="bandit", name="Bandit", is_enemy=True, max_hp=500, hp=500)
    state = eng.start([ash, foe], ranges={("ash", "bandit"): RangeBand.ENGAGED})
    results = [eng.submit_turn(state, "ash", turn=heavy_on()) for _ in range(4)]
    assert all(r.resolved_kind == "attack_heavy_hit" for r in results[:3])
    # Fourth consecutive heavy: surcharge (25 -> 37 -> 50 -> 62) outruns the pool.
    assert results[3].weakened or ash.exhausted or ash.stamina < 25
    # Mixed play stays affordable: a fresh light after heavies is cheap again.
    light = traditional_turn(
        primary=SlotAction(slot=ActionSlot.PRIMARY, kind=ActionKind.ATTACK_LIGHT, target_id="bandit"))
    eng.end_round(state)  # regen ticks
    r = eng.submit_turn(state, "ash", turn=light)
    assert r.resolved_kind == "attack_hit" and not r.weakened


def test_block_costs_stamina_and_halves_next_hit():
    eng = CombatEngine()
    ash = Combatant(id="ash", name="Ash")
    foe = Combatant(id="bandit", name="Bandit", is_enemy=True,
                    weapon=Weapon(name="Club", damage=10))
    state = eng.start([ash, foe], ranges={("ash", "bandit"): RangeBand.ENGAGED})
    before = ash.stamina
    eng.submit_turn(state, "ash",
                    turn=traditional_turn(
                        reaction=SlotAction(slot=ActionSlot.REACTION, kind=ActionKind.BLOCK)))
    assert ash.stamina == before - st.BASE_COSTS[ActionKind.BLOCK.value]
    hp_before = ash.hp
    eng.submit_turn(state, "bandit", text="I smash Ash with my club")
    assert (hp_before - ash.hp) <= 10 // 2  # blocked: halved
