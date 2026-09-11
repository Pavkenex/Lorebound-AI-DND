"""Skill registry & mastery tiers (t_66f5359f)."""
from app.modules.progression.skills import (
    SKILL_REGISTRY,
    TIER_THRESHOLDS,
    MasteryTier,
    SkillCategory,
    SkillProgress,
    format_skill_display,
    next_threshold,
    tier_for_xp,
)


def test_six_categories():
    assert {c.value for c in SkillCategory} == {
        "Combat", "Physical", "Survival", "Knowledge", "Social", "Practical",
    }


def test_registry_covers_all_categories():
    covered = {cat for cat in SKILL_REGISTRY.values()}
    assert covered == set(SkillCategory)
    assert SKILL_REGISTRY["Swordsmanship"] == SkillCategory.COMBAT


def test_tier_order_and_thresholds():
    assert [t for t in MasteryTier] == [
        MasteryTier.UNTRAINED, MasteryTier.NOVICE, MasteryTier.COMPETENT,
        MasteryTier.SKILLED, MasteryTier.EXPERT, MasteryTier.MASTER,
    ]
    assert TIER_THRESHOLDS[MasteryTier.SKILLED] == 1200
    assert TIER_THRESHOLDS[MasteryTier.EXPERT] == 2000


def test_tier_for_xp_boundaries():
    assert tier_for_xp(0) == MasteryTier.UNTRAINED
    assert tier_for_xp(199) == MasteryTier.UNTRAINED
    assert tier_for_xp(200) == MasteryTier.NOVICE
    assert tier_for_xp(600) == MasteryTier.COMPETENT
    assert tier_for_xp(1420) == MasteryTier.SKILLED
    assert tier_for_xp(3200) == MasteryTier.MASTER
    assert next_threshold(1420) == 2000
    assert next_threshold(99999) is None


def test_exact_display_string():
    assert format_skill_display("Swordsmanship", 1420) == \
        "Swordsmanship — Skilled, 1,420 / 2,000 mastery XP"


def test_progress_add_xp_and_recent_lines():
    p = SkillProgress("Lockpicking", 190)
    before = p.add_xp(20, "Merchant's chest +14")
    assert before == MasteryTier.UNTRAINED
    assert p.tier == MasteryTier.NOVICE
    assert p.recent_lines == ["Merchant's chest +14"]
