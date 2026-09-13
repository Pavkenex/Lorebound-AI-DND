"""Saga digest (#4): rolling recap — build, refresh cadence, prompt wiring."""
from __future__ import annotations

from app.modules.narrator.prompts import PromptContext, assemble_prompt
from app.modules.play.engine import ActEngine
from app.modules.play.session import PlaySession
from app.modules.play.state import PlayState, seeded_state
from app.modules.story.saga import (
    SAGA_MAX_CHARS,
    build_saga_digest,
    refresh_saga,
    saga_due,
)


def _engine(campaign_id: str = "saga-digest") -> ActEngine:
    return ActEngine(PlaySession(campaign_id, seeded_state()))


def test_fresh_digest_names_hero_and_road():
    st = seeded_state()
    text = build_saga_digest(st)
    assert st.pc.get("name", "the hero") in text
    assert "Ravenford" in text
    assert "day 1" in text


def test_digest_stays_capped():
    st = seeded_state()
    for i in range(40):
        st.npc_memory_log.append(
            {
                "npc": f"extra-{i}",
                "text": f"unforgettable deed number {i} " + ("very " * 30) + "long",
                "kind": "",
                "sentiment": 1,
                "salience": 5,
                "day": 1,
                "hour": 20,
            }
        )
    for slug, value in [(f"friend-{i}", 40 + i) for i in range(10)]:
        st.attitudes[slug] = value
    text = build_saga_digest(st)
    assert len(text) <= SAGA_MAX_CHARS + 2


def test_digest_carries_bonds_and_weight():
    st = seeded_state()
    st.attitudes["marla"] = 35
    st.npc_memory_log.append(
        {
            "npc": "marla",
            "text": "stood up for Marla against Borin",
            "kind": "",
            "sentiment": 2,
            "salience": 4,
            "day": 1,
            "hour": 20,
        }
    )
    text = build_saga_digest(st)
    assert "Warm" in text
    assert "stood up for Marla" in text


def test_refresh_cadence():
    st = seeded_state()
    assert refresh_saga(st) is False  # opening beats: nothing to recap yet
    assert st.saga == ""
    st.actions_taken = 3
    assert saga_due(st) is True
    assert refresh_saga(st) is True
    assert st.saga != ""
    assert st.saga_at == 3
    assert saga_due(st) is False
    st.actions_taken = 15
    assert saga_due(st) is True


def test_walk_into_town_writes_the_digest():
    engine_obj = _engine("saga-walk-in")
    assert engine_obj.state.saga == ""
    engine_obj.act("I head down to the inn and step inside")
    assert engine_obj.state.saga != ""
    assert "Ravenford" in engine_obj.state.saga


def test_prompt_carries_story_so_far():
    bundle = assemble_prompt(PromptContext(saga="Marla trusts you. The road was long."))
    assert "[Story so far" in bundle.user
    assert "Marla trusts you" in bundle.user


def test_prompt_without_saga_stays_honest():
    bundle = assemble_prompt(PromptContext())
    assert "[Story so far" in bundle.user
    assert "the saga opens here" in bundle.user


def test_old_save_without_saga_still_loads():
    st = PlayState.from_json('{"location": "lantern-inn", "actions_taken": 30}')
    assert st.saga == ""
    assert st.saga_at == 0
    assert refresh_saga(st) is True
    assert "Lantern Inn" in st.saga
