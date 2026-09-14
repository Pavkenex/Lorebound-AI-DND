"""Focused tests for the saga digest hierarchy (spec §3.3).

Covers the cadence, quiet-window folding, the session→arc→campaign rollups,
pinned-fact references, summarizer injection (prose only) and the tiered
injectable() surfaces.
"""
from __future__ import annotations

import pytest

from engine.config import EngineConfig
from engine.memory import SESSIONS_PER_ARC, SagaDigest
from engine.models import WorldFact, to_row
from engine.store import Store


@pytest.fixture()
def store():
    s = Store(":memory:")
    try:
        yield s
    finally:
        s.close()


@pytest.fixture()
def config():
    cfg = EngineConfig()
    cfg.memory.session_summary_turns = 4
    return cfg


def _beat(store: Store, turn: int, actor: str = "player", summary: str | None = None,
          consequence: str = "") -> int:
    return store.insert("chronicle", {
        "turn_id": turn, "actor": actor,
        "action_summary": summary or f"did something at {turn}",
        "consequence_oneliner": consequence,
    })


def _pin(store: Store, statement: str, turn: int) -> int:
    return store.insert("world_facts", to_row(WorldFact(
        statement=statement, pinned=True, established_turn=turn, source="narrator")))


def _scopes(rows) -> list[str]:
    return [row.scope_id for row in rows]


# --------------------------------------------------------------------------- #
# Cadence and quiet windows
# --------------------------------------------------------------------------- #

def test_session_summary_only_at_the_cadence(store, config):
    saga = SagaDigest(store, config)
    for turn in range(1, 5):
        _beat(store, turn)

    assert saga.maybe_summarize(turn=3) == []  # 3 < one window of 4
    rows = saga.maybe_summarize(turn=4)
    assert [row.level for row in rows] == ["session"]
    assert rows[0].scope_id == "session-1"
    assert rows[0].created_turn == 4
    assert rows[0].text.startswith("Session 1 (turns 1-4):")
    assert "T1 player" in rows[0].text
    assert "did something at 4" in rows[0].text
    assert saga.maybe_summarize(turn=4) == []  # idempotent: nothing new to derive


def test_quiet_stretches_fold_into_the_next_summary(store, config):
    saga = SagaDigest(store, config)
    assert saga.maybe_summarize(turn=8) == []  # nothing happened: no hollow rows

    _beat(store, turn=10)
    rows = saga.maybe_summarize(turn=12)
    assert [row.level for row in rows] == ["session"]
    # the quiet windows fold in: coverage is gapless, session 1 spans turns 1-12
    assert rows[0].text.startswith("Session 1 (turns 1-12):")
    assert "did something at 10" in rows[0].text
    assert len(saga.archive_of("session")) == 1


def test_force_summarizes_a_partial_window_and_resumes_from_there(store, config):
    saga = SagaDigest(store, config)
    for turn in range(1, 4):
        _beat(store, turn)
    rows = saga.maybe_summarize(turn=3, force=True)
    assert [row.level for row in rows] == ["session"]
    assert rows[0].text.startswith("Session 1 (turns 1-3):")

    _beat(store, turn=10)
    rows = saga.maybe_summarize(turn=10, force=True)
    assert rows[0].scope_id == "session-2"
    assert rows[0].text.startswith("Session 2 (turns 4-10):")


def test_campaign_level_appears_only_once_an_arc_closes(store, config):
    saga = SagaDigest(store, config)  # W=4, SESSIONS_PER_ARC=3
    for turn in range(1, 9):
        _beat(store, turn)
    rows = saga.maybe_summarize(turn=8)
    assert [row.level for row in rows] == ["session", "session"]
    assert saga.archive_of("arc") == []
    assert saga.level_text("campaign") is None


# --------------------------------------------------------------------------- #
# Rollups and the archive
# --------------------------------------------------------------------------- #

def test_hierarchy_rolls_up_and_archives_every_level(store, config):
    saga = SagaDigest(store, config)
    for turn in range(1, 13):
        _beat(store, turn)

    rows = saga.maybe_summarize(turn=12)
    assert [row.level for row in rows] == ["session", "session", "session", "arc", "campaign"]
    assert _scopes(rows[:3]) == ["session-1", "session-2", "session-3"]

    arc = rows[3]
    assert (arc.scope_id, arc.created_turn) == ("arc-1", 12)
    assert arc.references == ["session-1", "session-2", "session-3"]
    assert arc.text.startswith("Arc 1 (turns 1-12):")
    assert "rolls up: session-1, session-2, session-3" in arc.text

    campaign = rows[4]
    assert (campaign.scope_id, campaign.created_turn) == ("campaign", 12)
    assert campaign.references == ["arc-1"]
    assert campaign.text.startswith("Campaign (turns 1-12):")
    assert "rolls up: arc-1" in campaign.text

    assert _scopes(saga.archive_of("session")) == ["session-1", "session-2", "session-3"]
    assert _scopes(saga.archive_of("arc")) == ["arc-1"]
    assert _scopes(saga.archive_of("campaign")) == ["campaign"]
    assert saga.level_text("arc") == arc.text
    assert saga.level_text("campaign") == campaign.text


def test_second_arc_appends_and_rebuilds_the_campaign(store, config):
    saga = SagaDigest(store, config)
    for turn in range(1, 25):
        _beat(store, turn)
    saga.maybe_summarize(turn=12)
    rows = saga.maybe_summarize(turn=24)

    assert [row.level for row in rows] == ["session", "session", "session", "arc", "campaign"]
    assert _scopes(saga.archive_of("session"))[-1] == "session-6"
    assert _scopes(saga.archive_of("arc")) == ["arc-1", "arc-2"]
    arc2 = saga.archive_of("arc")[-1]
    assert arc2.references[:SESSIONS_PER_ARC] == ["session-4", "session-5", "session-6"]
    assert arc2.text.startswith("Arc 2 (turns 13-24):")
    # the campaign digest is rebuilt and superseded, never edited in place
    assert _scopes(saga.archive_of("campaign")) == ["campaign", "campaign"]
    assert saga.level_text("campaign").startswith("Campaign (turns 1-24):")
    assert saga.archive_of("campaign")[-1].references == ["arc-1", "arc-2"]


def test_close_arc_rolls_up_the_pending_sessions(store, config):
    saga = SagaDigest(store, config)
    for turn in range(1, 5):
        _beat(store, turn)
    saga.maybe_summarize(turn=4)  # session-1

    rows = saga.close_arc(turn=4)
    assert [row.level for row in rows] == ["arc", "campaign"]
    assert rows[0].references == ["session-1"]
    assert saga.level_text("arc").startswith("Arc 1 (turns 1-4):")
    assert rows[1].references == ["arc-1"]
    # nothing pending afterwards
    assert saga.close_arc(turn=4) == []


def test_archive_is_append_only(store, config):
    saga = SagaDigest(store, config)
    for turn in range(1, 13):
        _beat(store, turn)
    saga.maybe_summarize(turn=12)
    before = {level: [row.text for row in saga.archive_of(level)]
              for level in ("session", "arc", "campaign")}
    saga.maybe_summarize(turn=12)
    after = {level: [row.text for row in saga.archive_of(level)]
             for level in ("session", "arc", "campaign")}
    assert after == before
    assert store.count("saga_levels") == 5


def test_unknown_level_is_rejected(store):
    saga = SagaDigest(store)
    with pytest.raises(ValueError, match="unknown saga level"):
        saga.archive_of("legend")
    with pytest.raises(ValueError, match="unknown saga level"):
        saga.level_text("legend")


# --------------------------------------------------------------------------- #
# Pinned facts and the injectable summarizer
# --------------------------------------------------------------------------- #

def test_pinned_facts_are_referenced_by_id_and_exempt_from_the_summarizer(store, config):
    seen: list[list[str]] = []

    def summarizer(texts):
        seen.append(list(texts))
        return f"compressed({len(texts)})"

    saga = SagaDigest(store, config, summarizer=summarizer)
    fact_id = _pin(store, "Marla vowed to burn the mill", turn=2)
    for turn in range(1, 5):
        _beat(store, turn)

    rows = saga.maybe_summarize(turn=4)
    session = rows[0]
    assert "compressed(" in session.text  # the summarizer produced the prose...
    assert session.references == [fact_id]  # ...but the pinned fact is referenced by id
    assert f"Pinned facts (exempt): #{fact_id} Marla vowed to burn the mill" in session.text
    assert seen and all("vowed" not in " ".join(lines) for lines in seen)

    _beat(store, turn=5)
    campaign = saga.close_arc(turn=5)[1]
    assert campaign.references == ["arc-1", fact_id]
    assert f"#{fact_id} Marla vowed to burn the mill" in campaign.text


def test_summarizer_sees_structured_source_lines_not_summaries(store, config):
    seen: list[list[str]] = []

    def summarizer(texts):
        seen.append(list(texts))
        return " | ".join(texts)

    saga = SagaDigest(store, config, summarizer=summarizer)
    for turn in range(1, 5):
        _beat(store, turn, summary=f"beat {turn}", consequence=f"fell {turn}")

    saga.maybe_summarize(turn=4)
    assert seen == [["T1 player: beat 1 -> fell 1", "T2 player: beat 2 -> fell 2",
                     "T3 player: beat 3 -> fell 3", "T4 player: beat 4 -> fell 4"]]


def test_a_summarizer_that_returns_nothing_degrades_to_the_source_join(store, config):
    """A failed/empty summarizer must not lose the window's beats."""
    saga = SagaDigest(store, config, summarizer=lambda texts: None)
    for turn in range(1, 5):
        _beat(store, turn, summary=f"beat {turn}")
    rows = saga.maybe_summarize(turn=4)
    assert [row.level for row in rows] == ["session"]
    assert "T1 player: beat 1" in rows[0].text
    assert "T4 player: beat 4" in rows[0].text


# --------------------------------------------------------------------------- #
# injectable()
# --------------------------------------------------------------------------- #

def test_injectable_is_campaign_only_when_the_scene_ignores_the_detail(store, config):
    saga = SagaDigest(store, config)
    for turn in range(1, 13):
        _beat(store, turn)
    saga.maybe_summarize(turn=12)

    assert [row.level for row in saga.injectable(scene_context=[], turn=13)] == ["campaign"]
    assert [row.level for row in saga.injectable(
        scene_context=["buying bread at the quiet market"], turn=13)] == ["campaign"]


def test_injectable_pulls_the_relevant_arc_and_session_above_the_campaign(store, config):
    saga = SagaDigest(store, config)
    for turn in range(1, 5):
        _beat(store, turn, summary="the salt mines flooded")
    for turn in range(5, 9):
        _beat(store, turn, summary="the silver bell rang at dusk")
    for turn in range(9, 13):
        _beat(store, turn, summary="the mill burned down")
    saga.maybe_summarize(turn=12)

    rows = saga.injectable(scene_context=["the silver bell rang again"], turn=13)
    assert [row.level for row in rows] == ["campaign", "arc", "session"]
    assert rows[2].scope_id == "session-2"
    assert "silver bell" in rows[2].text


def test_injectable_without_an_archive_is_empty(store):
    assert SagaDigest(store).injectable(scene_context=["anything"], turn=1) == []
