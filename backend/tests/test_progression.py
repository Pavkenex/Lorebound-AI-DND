"""Progression: XP formula, novelty, training, specs, traits, levels, screen."""
import pytest

from app.core.events import EventKind, GameEvent
from app.modules.progression.levels import (
    assert_world_unscaled,
    level_for_total_xp,
    unlocks_for_level,
    world_power,
)
from app.modules.progression.novelty import NoveltyTracker
from app.modules.progression.screen import NotificationCenter, build_skills_screen
from app.modules.progression.skills import MasteryTier, SkillProgress
from app.modules.progression.specializations import (
    SPEC_TREES,
    apply_specialization,
    available_specializations,
    choose_specialization,
)
from app.modules.progression.traits import (
    NarratorAuthorityError,
    Trait,
    TraitKind,
    derive_undead_trait,
    narrator_propose_story_trait,
)
from app.modules.progression.training import INSTRUCTORS, practice
from app.modules.progression.xp import (
    challenge_factor,
    compute_skill_xp,
    novelty_factor,
    performance_factor,
    training_modifier,
)


# t_159b55b9 — each factor independently testable
def test_challenge_harder_means_more():
    assert challenge_factor(8) > challenge_factor(1)
    assert challenge_factor(1) == 8.0


def test_performance_exceptional_beats_failure():
    assert performance_factor("critical_success") > performance_factor("success")
    assert performance_factor("success") > performance_factor("partial")
    assert performance_factor("failure") < performance_factor("partial")
    assert performance_factor("critical_failure") == 0.0


def test_performance_accepts_enum_and_string():
    from app.modules.progression.xp import Outcome

    assert performance_factor(Outcome.SUCCESS) == performance_factor("success")
    with pytest.raises(ValueError):
        performance_factor("transcendent")


def test_training_modifier_shifts_with_each_source():
    base = training_modifier()
    assert training_modifier(instructor=0.4) > base
    assert training_modifier(books=0.2) > base
    assert training_modifier(equipment=0.2) > base
    assert training_modifier(environment=0.2) > base
    assert training_modifier(traits=0.2) > base


def test_full_formula_trivial_success_is_8xp():
    assert compute_skill_xp(1, "success") == 8


# t_0bfdd8f6 — novelty decay 8,5..1,0
def test_novelty_decay_sequence():
    tracker = NoveltyTracker()
    seq = [tracker.award("Lockpicking", "easy lock", 1) for _ in range(7)]
    assert seq == [8, 5, 3, 2, 1, 0, 0]


def test_novelty_factor_pure():
    assert novelty_factor(0) == 1.0
    assert novelty_factor(99) == 0.0


def test_raising_challenge_resets_decay():
    tracker = NoveltyTracker()
    for _ in range(6):
        tracker.award("Lockpicking", "easy lock", 1)
    assert tracker.award("Lockpicking", "easy lock", 1) == 0
    assert tracker.award("Lockpicking", "hard lock", 6) > 0


# t_6889919a — practice, fatigue, injury, clock, ceilings
def test_practice_session_progress_fatigue_clock():
    from app.modules.exploration.clock import GameClock

    clock = GameClock()
    p = SkillProgress("Swordsmanship", 100)
    res = practice(p, 2, instructor="village guard", clock=clock, roll=0.99)
    assert res.xp_gained > 0
    assert res.fatigue_gained == 20
    assert res.minutes_elapsed == 120
    assert res.injury is False
    assert clock.hour == 10  # 08:00 + 2h


def test_practice_injury_on_bad_roll():
    p = SkillProgress("Swordsmanship", 100)
    res = practice(p, 4, current_fatigue=80, roll=0.0)
    assert res.injury is True


def test_practice_hours_validated():
    with pytest.raises(ValueError):
        practice(SkillProgress("Swordsmanship"), 0)
    with pytest.raises(ValueError):
        practice(SkillProgress("Swordsmanship"), 5)
    with pytest.raises(ValueError):
        practice(SkillProgress("Swordsmanship"), 2, instructor="random peasant")


def test_instructor_ceilings():
    guard = INSTRUCTORS["village guard"]
    master = INSTRUCTORS["swordmaster"]
    assert guard.ceiling == MasteryTier.COMPETENT
    assert master.ceiling == MasteryTier.EXPERT
    # Guard cannot teach a Competent student further.
    capped = practice(SkillProgress("Swordsmanship", 600), 2, instructor="village guard")
    assert capped.capped and capped.xp_gained == 0
    # Swordmaster can still teach a Skilled student, not an Expert one.
    ok = practice(SkillProgress("Swordsmanship", 1200), 2, instructor="swordmaster", roll=0.99)
    assert not ok.capped and ok.xp_gained > 0
    capped2 = practice(SkillProgress("Swordsmanship", 2000), 2, instructor="swordmaster")
    assert capped2.capped


# t_78b7e45f — specializations diverge identical bases
def test_swordsmanship_offers_four_paths():
    assert {s.name for s in SPEC_TREES["Swordsmanship"]} == {
        "Duelist", "Guardian", "Greatblade", "Counterfighter",
    }


def test_identical_bases_play_differently():
    base = {"duel": 10, "guard": 10, "cleave": 10, "riposte": 10}
    specs = {s.name: s for s in SPEC_TREES["Swordsmanship"]}
    duelist = apply_specialization(base, specs["Duelist"])
    guardian = apply_specialization(base, specs["Guardian"])
    assert duelist != guardian
    assert duelist["duel"] > guardian["duel"]
    assert guardian["guard"] > duelist["guard"]
    assert len({tuple(sorted(apply_specialization(base, s).items())) for s in specs.values()}) == 4


def test_specialization_needs_milestone_and_single_choice():
    early = SkillProgress("Swordsmanship", 600)
    assert available_specializations("Swordsmanship", early.tier) == []
    with pytest.raises(ValueError):
        choose_specialization(early, "Duelist")
    ready = SkillProgress("Swordsmanship", 1200)
    assert len(available_specializations("Swordsmanship", ready.tier)) == 4
    choose_specialization(ready, "Duelist")
    assert ready.specialization == "Duelist"
    with pytest.raises(ValueError):
        choose_specialization(ready, "Guardian")


# t_7b33ac86 — traits, story-derived via event layer only
def test_plain_traits_construct_freely():
    t = Trait("Night Owl", TraitKind.POSITIVE, "Rested after 6h sleep.")
    assert t.kind == TraitKind.POSITIVE


def test_story_trait_needs_event_layer():
    with pytest.raises(NarratorAuthorityError):
        Trait("Haunted", TraitKind.STORY, "Seen too much.")
    with pytest.raises(NarratorAuthorityError):
        narrator_propose_story_trait("Haunted")


def test_undead_exposure_derives_trait_from_events():
    def undead(outcome):
        return GameEvent(kind=EventKind.PLAYER_ACTION, payload={"foe": "undead", "outcome": outcome})

    hardened = derive_undead_trait([undead("success")] * 4)
    assert hardened is not None and hardened.name == "Hardened Against the Dead"
    assert hardened.kind == TraitKind.STORY
    fearful = derive_undead_trait([undead("terror")] * 3)
    assert fearful is not None and fearful.name == "Fear of the Restless Dead"
    assert derive_undead_trait([undead("success")] * 2) is None


# t_8fe89ca9 — level unlocks, never scales world
def test_level_unlocks_grow():
    assert level_for_total_xp(0) == 1
    low = unlocks_for_level(level_for_total_xp(0))
    high = unlocks_for_level(level_for_total_xp(12000))
    assert high.talent_slots > low.talent_slots
    assert high.resolve_capacity > low.resolve_capacity
    assert high.advanced_specializations and not low.advanced_specializations
    assert len(high.narrative_opportunities) > len(low.narrative_opportunities)


def test_level_never_scales_world():
    assert world_power(1) == world_power(10) == 1.0
    assert assert_world_unscaled()


# t_4b8ec292 — skills screen + subtle notifications
def test_skills_screen_payload():
    p = SkillProgress("Swordsmanship", 1420, recent_lines=["Merchant's chest +14"])
    screen = build_skills_screen([p], notifications=["Lockpicking improved"])
    entry = screen["skills"][0]
    assert entry["tier"] == "Skilled"
    assert entry["display"] == "Swordsmanship — Skilled, 1,420 / 2,000 mastery XP"
    assert 0.0 < entry["xp_bar"] < 1.0
    assert entry["recent_lines"] == ["Merchant's chest +14"]
    assert "Duelist" in entry["available_specializations"]
    assert entry["trainers"], "trainers listed"
    assert entry["practice_options"] == [1, 2, 3, 4]
    assert screen["notifications"] == ["Lockpicking improved"]


def test_notifications_only_on_tier_up():
    center = NotificationCenter()
    center.notify_gain("Lockpicking", 5, "Novice", "Novice")
    assert center.drain() == []
    center.notify_gain("Lockpicking", 200, "Novice", "Competent")
    assert center.drain() == ["Lockpicking improved to Competent"]
