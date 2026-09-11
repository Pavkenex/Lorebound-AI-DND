"""Difficulty, history store, achievements, content policy."""
import pytest

from app.core.events import EventKind, GameEvent
from app.modules.history.achievements import ACHIEVEMENTS, AchievementTracker
from app.modules.history.content_policy import (
    ContentGate,
    PolicyRefusal,
    classify,
    gate_narrator_text,
    request_procedural_worldgen,
)
from app.modules.history.store import HistoryStore, short_payload
from app.modules.progression.difficulty import (
    DifficultyName,
    ai_hostility,
    get_preset,
)


# t_f26e9eb5 — forgiveness, not hostility
def test_four_presets_exist():
    assert {p.value for p in DifficultyName} == {
        "Story", "Adventurer", "Veteran", "Iron Chronicle",
    }


def test_difficulty_forgives_but_ai_hostility_constant():
    story, vet, iron = (get_preset(DifficultyName.STORY), get_preset(DifficultyName.VETERAN),
                        get_preset(DifficultyName.IRON_CHRONICLE))
    assert story.check_bonus > vet.check_bonus > iron.check_bonus
    assert story.damage_taken_mult < vet.damage_taken_mult < iron.damage_taken_mult
    assert iron.permadeath and iron.save_limit == 1
    assert story.death_forgiveness and not iron.death_forgiveness
    hostilities = {ai_hostility(p) for p in DifficultyName}
    assert hostilities == {1.0}


# t_365cc3f2 — event store feeding summaries
def test_history_store_helpers_and_summary():
    store = HistoryStore()
    store.record(EventKind.PLAYER_ACTION, campaign_id="c1", intent="pick lock")
    store.record(EventKind.SKILL_IMPROVED, campaign_id="c1", skill="Lockpicking")
    store.record(EventKind.PLAYER_ACTION, campaign_id="c2", intent="haggle")
    assert len(store.by_kind(EventKind.SKILL_IMPROVED)) == 1
    assert len(store.by_campaign("c1")) == 2
    assert len(store.recent(2)) == 2
    summary = store.summarize_campaign("c1")
    assert summary["total_events"] == 2
    assert summary["counts"]["PLAYER_ACTION"] == 1
    assert len(summary["digest"]) == 2
    assert "pick lock" in summary["digest"][0]


def test_short_payload_picks_intent_first():
    e = GameEvent(kind=EventKind.PLAYER_ACTION, payload={"intent": "sneak past"})
    assert short_payload(e) == "intent=sneak past"


# t_d5e1babe — story achievements
def test_all_three_named_achievements_catalogued():
    assert set(ACHIEVEMENTS) == {"silver-tongue", "everyone-owes-me", "wrong-door"}


def test_silver_tongue_needs_ten_peaceful():
    t = AchievementTracker()
    for _ in range(9):
        assert t.observe(GameEvent(kind=EventKind.PLAYER_ACTION,
                                   payload={"resolved_without_combat": True})) == []
    assert t.observe(GameEvent(kind=EventKind.PLAYER_ACTION,
                               payload={"resolved_without_combat": True})) == ["silver-tongue"]


def test_everyone_owes_me_needs_five_influential():
    t = AchievementTracker()
    for i in range(4):
        t.observe(GameEvent(kind=EventKind.RELATIONSHIP_CHANGED,
                            payload={"favour_from_influential": True, "npc": f"Elder {i}"}))
    assert t.observe(GameEvent(kind=EventKind.RELATIONSHIP_CHANGED,
                               payload={"favour_from_influential": True, "npc": "Elder 4"})) == [
        "everyone-owes-me"
    ]


def test_wrong_door_on_accidental_major_discovery():
    t = AchievementTracker()
    assert t.observe(GameEvent(kind=EventKind.LOCATION_DISCOVERED,
                               payload={"accidental": True, "major": True})) == ["wrong-door"]
    # Deliberate discovery does not count.
    t2 = AchievementTracker()
    assert t2.observe(GameEvent(kind=EventKind.LOCATION_DISCOVERED,
                                payload={"accidental": False, "major": True})) == []


# t_65a08bb1 — hybrid content policy gate
def test_authored_vs_generated_gate():
    assert classify("major_faction") == ContentGate.AUTHORED
    assert classify("rumour") == ContentGate.GENERATED
    assert gate_narrator_text("major_faction", ContentGate.AUTHORED)
    assert not gate_narrator_text("major_faction", ContentGate.GENERATED)
    assert gate_narrator_text("rumour", ContentGate.GENERATED)
    with pytest.raises(PolicyRefusal):
        classify("brand_new_continent")
    with pytest.raises(PolicyRefusal):
        request_procedural_worldgen("a whole world please")
