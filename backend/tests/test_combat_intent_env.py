"""Enemy intent display + environmental interaction (chandelier rule)."""
from app.modules.combat.engine import CombatEngine
from app.modules.combat.models import Combatant, EnvFeature, Weapon
from app.modules.combat.turns import ActionKind, classify_free_text


def test_intent_display_names_enemy_and_target():
    eng = CombatEngine()
    archer = Combatant(id="ba", name="Bandit Archer", is_enemy=True,
                       weapon=Weapon(name="Bow", damage=8, ranged=True))
    marla = Combatant(id="marla", name="Marla")
    state = eng.start([archer, marla])
    line = eng.set_intent(state, "ba", "aim", "marla")
    assert line == "Bandit Archer — aiming at Marla"
    assert eng.intent_board(state) == ["Bandit Archer — aiming at Marla"]


def test_chandelier_rope_resolves_as_env_not_attack():
    eng = CombatEngine()
    hero = Combatant(id="ash", name="Ash")
    bandit = Combatant(id="bandit", name="Bandit", is_enemy=True, max_hp=40, hp=40)
    chandelier = EnvFeature(name="chandelier", aliases=["rope", "chandelier rope"],
                            effect_damage=14, linked_targets=["bandit"])
    state = eng.start([hero, bandit], env=[chandelier])
    parsed = classify_free_text(state, "ash", "I cut the rope holding the chandelier")
    assert parsed.primary is not None and parsed.primary.kind == ActionKind.ENV_INTERACT
    res = eng.submit_turn(state, "ash", text="I cut the rope holding the chandelier")
    assert res.resolved_kind == "env_interact"
    assert bandit.hp == 40 - 14
    assert "attack" not in res.resolved_kind


def test_missing_object_fails_open_never_rewritten_to_attack():
    eng = CombatEngine()
    hero = Combatant(id="ash", name="Ash")
    foe = Combatant(id="bandit", name="Bandit", is_enemy=True, max_hp=40, hp=40)
    state = eng.start([hero, foe])  # no env features at all
    res = eng.submit_turn(state, "ash", text="I cut the rope holding the chandelier")
    assert res.resolved_kind == "env_missing"
    assert foe.hp == foe.max_hp  # nobody got quietly attacked
    assert "isn't there" in res.summary


def test_env_with_no_one_under_it():
    eng = CombatEngine()
    hero = Combatant(id="ash", name="Ash")
    foe = Combatant(id="bandit", name="Bandit", is_enemy=True)
    barrel = EnvFeature(name="barrel", aliases=["powder barrel"], effect_damage=10)
    state = eng.start([hero, foe], env=[barrel])
    res = eng.submit_turn(state, "ash", text="I smash the powder barrel")
    assert res.resolved_kind == "env_no_effect"
    assert foe.hp == foe.max_hp
