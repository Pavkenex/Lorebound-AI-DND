"""HP as capacity, lasting injuries, medicine/rest matter; modes & death."""
from app.modules.combat import injuries as inj
from app.modules.combat.engine import CombatEngine
from app.modules.combat.models import Combatant, RangeBand, Weapon
from app.modules.combat.modes import death_save, resolve_zero_hp, stabilize


def test_heavy_blow_inflicts_lasting_injury():
    c = Combatant(id="m", name="Marla", max_hp=30, hp=30)
    assert inj.should_inflict(14, c)  # >= 40% of max in one hit
    kind = inj.pick_injury(14, 30)
    inj.inflict(c, kind)
    assert kind in ("BruisedRibs", "BrokenArm", "DeepCut", "Concussion")
    assert kind in c.injuries


def test_injuries_persist_and_bleed_ticks():
    eng = CombatEngine()
    hero = Combatant(id="ash", name="Ash", weapon=Weapon(name="Axe", damage=14))
    foe = Combatant(id="bandit", name="Bandit", is_enemy=True, max_hp=30, hp=30)
    state = eng.start([hero, foe], ranges={("ash", "bandit"): RangeBand.ENGAGED})
    res = eng.submit_turn(state, "ash", text="I slash the bandit with my axe")
    assert res.injury_inflicted.get("bandit")
    assert foe.injuries  # outlives the exchange


def test_medicine_treats_what_rest_alone_slowly_heals():
    c = Combatant(id="m", name="Marla", injuries=["DeepCut"])
    assert inj.treat_injury(c, "DeepCut", with_medicine=False) is False
    assert inj.treat_injury(c, "DeepCut", with_medicine=True) is True
    assert c.injuries == []


def test_standard_mode_incapacitates_instead_of_killing():
    c = Combatant(id="m", name="Marla", hp=5)
    assert resolve_zero_hp(c, "standard") == "incapacitated"
    assert c.status == "incapacitated" and c.hp == 0
    assert stabilize(c) is True and c.status == "stable"


def test_story_mode_auto_stabilizes_iron_kills():
    a = Combatant(id="a", name="A", hp=1)
    assert resolve_zero_hp(a, "story") == "stable"
    b = Combatant(id="b", name="B", hp=1)
    assert resolve_zero_hp(b, "iron") == "dead"
    assert b.status == "dead"


def test_death_saves_forgive_three_strikes():
    c = Combatant(id="m", name="Marla", status="incapacitated", hp=0)
    death_save(c, "standard", 5)
    death_save(c, "standard", 5)
    assert c.status == "incapacitated"
    death_save(c, "standard", 5)
    assert c.status == "dead"
    d = Combatant(id="n", name="Nia", status="incapacitated", hp=0)
    assert death_save(d, "standard", 20) == "stable"


def test_engine_zero_hp_uses_mode_default_forgiving():
    eng = CombatEngine()
    hero = Combatant(id="ash", name="Ash", max_hp=30, hp=30,
                     weapon=Weapon(name="Axe", damage=30))
    foe = Combatant(id="ogre", name="Ogre", is_enemy=True)
    state = eng.start([hero, foe], mode="standard",
                      ranges={("ash", "ogre"): RangeBand.ENGAGED})
    foe_weapon = Weapon(name="Maul", damage=30)
    foe.weapon = foe_weapon
    eng.submit_turn(state, "ogre", text="I smash Ash with my maul")
    assert hero.status in ("incapacitated", "stable")  # meaningful, not dead
    assert hero.status != "dead"
