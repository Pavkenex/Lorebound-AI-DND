"""Magic: six schools, approved techniques only — no invented powers."""
import pytest

from app.modules.combat.engine import CombatEngine
from app.modules.combat.magic import (
    APPROVED_TECHNIQUES,
    School,
    UnknownTechniqueError,
    approve_technique,
    get_technique,
    techniques_for_school,
)
from app.modules.combat.models import Combatant


def test_six_schools_all_have_techniques():
    assert len(School) == 6
    for school in School:
        assert techniques_for_school(school), f"{school} has no techniques"


def test_approved_cast_resolves():
    assert get_technique("emberbolt").school == School.ELEMENTALISM
    eng = CombatEngine()
    mage = Combatant(id="mage", name="Mage")
    foe = Combatant(id="bandit", name="Bandit", is_enemy=True, max_hp=40, hp=40)
    state = eng.start([mage, foe])
    res = eng.cast(state, "mage", "emberbolt", "bandit")
    assert res.resolved_kind == "cast_hit"
    assert foe.hp == 40 - APPROVED_TECHNIQUES["emberbolt"].damage


def test_restoration_heals():
    eng = CombatEngine()
    cleric = Combatant(id="cl", name="Cleric", max_hp=30, hp=12)
    state = eng.start([cleric])
    res = eng.cast(state, "cl", "mend_wounds", "cl")
    assert res.resolved_kind == "cast_heal"
    assert cleric.hp == 12 + APPROVED_TECHNIQUES["mend_wounds"].healing


def test_invented_power_rejected_not_hallucinated():
    with pytest.raises(UnknownTechniqueError):
        get_technique("shadowfire_doomblade")
    eng = CombatEngine()
    mage = Combatant(id="mage", name="Mage")
    foe = Combatant(id="bandit", name="Bandit", is_enemy=True, hp=40, max_hp=40)
    state = eng.start([mage, foe])
    res = eng.cast(state, "mage", "shadowfire_doomblade", "bandit")
    assert res.resolved_kind == "technique_unknown"
    assert foe.hp == foe.max_hp  # nothing happened, and that's the point


def test_new_definitions_go_through_approval_not_mid_scene():
    from app.modules.combat.magic import TechniqueDefinition
    approve_technique(TechniqueDefinition(
        id="test_spark", name="Test Spark", school=School.ELEMENTALISM, damage=2))
    try:
        assert get_technique("test_spark").damage == 2
    finally:
        del APPROVED_TECHNIQUES["test_spark"]
