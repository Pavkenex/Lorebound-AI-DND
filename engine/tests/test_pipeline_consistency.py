"""Pass D — consistency gate: regeneration, deterministic patching, dedupe (R7).

The gate is the last line of defence before fiction hits the player, so these
tests drive it end to end with scripted envelopes: a first draft that would ship
a contradiction, and a corrected second draft from the same narrator.
"""
from __future__ import annotations

import json

import pytest

from engine.config import EngineConfig
from engine.fixtures.demo_world import demo_world, seed_world
from engine.models import Delta, MoodState, ProposalSet, to_row
from engine.pipeline import Orchestrator
from engine.play import StubNarrator
from engine.store import Store


@pytest.fixture()
def store(tmp_path):
    s = Store(tmp_path / "campaign.db")
    seed_world(s, demo_world())
    try:
        yield s
    finally:
        s.close()


def session_with(store, script, **kwargs) -> Orchestrator:
    return Orchestrator(
        store, EngineConfig(db_path=str(store._path)), StubNarrator(script=list(script)),
        **kwargs,
    )


def kill(store, name: str) -> None:
    row = store.find_one("npcs", {"name": name})
    store.update("npcs", int(row["id"]), {"alive": 0})


def mood_delta(target: str = "npc:4", amount: float = 0.5) -> Delta:
    return Delta(kind="mood", target=target,
                 data={"valence_delta": amount, "arousal_delta": 0.0}, reason="test spike")


# --------------------------------------------------------------------------- #
# regeneration
# --------------------------------------------------------------------------- #

def test_dead_npc_speech_triggers_exactly_one_regeneration(store) -> None:
    kill(store, "Marla Quist")
    bad = ProposalSet(narration='"Not now," Marla Quist says, and turns away.')
    good = ProposalSet(narration="The yard empties quietly around you.")
    orchestrator = session_with(store, [bad, good])
    result = orchestrator.take_turn(player_input="I ask Marla about the shipment", turn=1)

    assert result.narration == "The yard empties quietly around you."
    assert len(orchestrator.narrator.calls) == 2
    retry = orchestrator.narrator.calls[1]["retry_note"]
    assert retry is not None
    assert "Marla Quist is dead" in retry
    assert any("regenerating once" in line for line in result.system_lines)
    assert any("regeneration cleared the problem(s)" in line for line in result.system_lines)
    assert result.telemetry["notes"]["consistency"]["regenerations"] == 1
    assert result.telemetry["notes"]["consistency"]["problems"] == 0


def test_regeneration_never_happens_twice(store) -> None:
    kill(store, "Marla Quist")
    bad = ProposalSet(narration='Marla Quist says, "Take the coin."')
    still_bad = ProposalSet(narration='Marla Quist whispers, "Keep it."')
    orchestrator = session_with(store, [bad, still_bad])
    result = orchestrator.take_turn(player_input="I ask Marla about the shipment", turn=1)

    assert len(orchestrator.narrator.calls) == 2  # hard cap: one regeneration
    consistency = result.telemetry["notes"]["consistency"]
    assert consistency["regenerations"] == 1
    assert consistency["problems"] == 1


def test_regeneration_does_not_double_apply_deltas(store) -> None:
    kill(store, "Marla Quist")
    delta = mood_delta("npc:4")
    first = ProposalSet(narration="Marla Quist says, \"Good.\" "
                                   "Marla's brother Dain is alive and well.", deltas=[delta])
    second = ProposalSet(narration="Tam goes back to the well rope.", deltas=[delta])
    result = session_with(store, [first, second]).take_turn(player_input="I greet Tam", turn=1)

    assert len(result.report.applied) == 1
    row = store.find_one("moods", {"npc_id": "npc:4"})
    assert row["valence"] == pytest.approx(0.4 + 0.5)  # one spike, not two
    applied = json.loads(store.find_one("turn_log", {"turn": 1})["state_deltas_applied"])
    assert len(applied) == 1


# --------------------------------------------------------------------------- #
# deterministic patching (no usable rewrite)
# --------------------------------------------------------------------------- #

def test_second_bad_draft_is_patched_sentence_by_sentence(store) -> None:
    kill(store, "Marla Quist")
    bad = ProposalSet(narration='You knock. Marla Quist says, "Come in." The yard is quiet.')
    worse = ProposalSet(narration='Marla Quist answers, "Later." A gull crosses the gate.')
    result = session_with(store, [bad, worse]).take_turn(
        player_input="I ask Marla about the shipment", turn=1,
    )

    # the patch edits the second draft (the one being shipped): the dead
    # sentence goes, the clean sentence survives verbatim
    assert result.narration == "A gull crosses the gate."
    assert "Marla Quist answers" not in result.narration
    assert "You knock." not in result.narration  # the discarded first draft
    consistency = result.telemetry["notes"]["consistency"]
    assert consistency["patched_sentences"] == 1
    assert consistency["problems"] == 1
    assert any("sentence patch applied after regeneration" in line
               for line in result.system_lines)


def test_patching_drops_dialogue_from_the_dead(store) -> None:
    kill(store, "Marla Quist")
    bad = ProposalSet(narration="The yard is quiet.",
                      npc_dialogue=[{"npc_id": "npc:1", "name": "Marla Quist",
                                     "text": "Come in."}])
    worse = ProposalSet(narration="The yard stays quiet.",
                        npc_dialogue=[{"npc_id": "npc:1", "name": "Marla Quist",
                                       "text": "Later."}])
    result = session_with(store, [bad, worse]).take_turn(player_input="I knock", turn=1)

    assert result.npc_dialogue == []
    assert result.telemetry["notes"]["consistency"]["patched_dialogue"] == 1
    assert any("dialogue line(s) removed" in line for line in result.system_lines)


def test_patch_free_turn_leaves_fiction_untouched(store) -> None:
    good = ProposalSet(narration="You wait. The salt light shifts; nobody comes.")
    result = session_with(store, [good]).take_turn(player_input="I wait", turn=1)
    assert result.narration == "You wait. The salt light shifts; nobody comes."
    consistency = result.telemetry["notes"]["consistency"]
    assert consistency == {"problems": 0, "regenerations": 0,
                           "patched_sentences": 0, "patched_dialogue": 0}


def test_unresolved_pinned_fact_conflict_is_patched_or_reported(store) -> None:
    bad = ProposalSet(narration="Marla's brother Dain is alive and well.")
    worse = ProposalSet(narration="Marla's brother Dain is alive.")
    result = session_with(store, [bad, worse]).take_turn(player_input="I greet Marla", turn=1)
    # both drafts contradict the pinned fact: the second is stripped to a fallback
    # line rather than restating the contradiction
    assert "Dain" not in result.narration or "alive" not in result.narration
    assert result.telemetry["notes"]["consistency"]["problems"] == 1


# --------------------------------------------------------------------------- #
# rejected delta restatement
# --------------------------------------------------------------------------- #

def test_restating_a_rejected_delta_forces_a_regeneration(store) -> None:
    bad = ProposalSet(
        narration="You pay 500 gold coins and the debt is settled.",
        deltas=[Delta(kind="currency", target="player", data={"amount": -500},
                      reason="grand gesture")],
    )
    good = ProposalSet(
        narration="You count the coins you have and say nothing.",
        deltas=[Delta(kind="currency", target="player", data={"amount": -2}, reason="oats")],
    )
    result = session_with(store, [bad, good]).take_turn(player_input="I pay the broker", turn=1)

    assert result.narration == "You count the coins you have and say nothing."
    # the refused grand gesture stays on the record (once), the rerun's honest
    # spend is accepted, and the shipped prose no longer restates the refusal
    assert [verdict.delta.data["amount"] for verdict in result.report.rejected] == [-500]
    assert len(result.report.accepted) == 1
    assert result.telemetry["notes"]["consistency"]["regenerations"] == 1
    assert "pay 500" not in result.narration
    assert result.telemetry["notes"]["deltas"] == {"accepted": 1, "clamped": 0, "rejected": 1}


def test_a_rejected_delta_that_is_never_restated_stays_quiet(store) -> None:
    script = [ProposalSet(
        narration="You square up, but your purse stays shut.",
        deltas=[Delta(kind="currency", target="player", data={"amount": -500},
                      reason="grand gesture")],
    )]
    result = session_with(store, script).take_turn(player_input="I pay the broker", turn=1)
    assert len(result.report.rejected) == 1
    assert result.telemetry["notes"]["consistency"]["problems"] == 0
    assert result.telemetry["notes"]["consistency"]["regenerations"] == 0


# --------------------------------------------------------------------------- #
# telemetry of the consistency pass
# --------------------------------------------------------------------------- #

def test_consistency_counts_land_in_telemetry(store) -> None:
    kill(store, "Marla Quist")
    orchestrator = session_with(store, [
        ProposalSet(narration='Marla Quist says, "Come in."'),
        ProposalSet(narration="The yard stays quiet."),
    ])
    result = orchestrator.take_turn(player_input="I knock", turn=3)
    notes = result.telemetry["notes"]
    assert notes["consistency"] == {"problems": 0, "regenerations": 1,
                                    "patched_sentences": 0, "patched_dialogue": 0}
    assert notes["mechanics"]["verdict_line"] == result.mechanics.verdict_line


def test_negated_restatement_of_a_rejected_spend_is_left_alone(store) -> None:
    """Prose that agrees with the rejection ("you never pay the 500") is not a hit."""
    script = [ProposalSet(
        narration="Your hand stops at the purse. You never pay 500 gold coins for salt.",
        deltas=[Delta(kind="currency", target="player", data={"amount": -500},
                      reason="grand gesture")],
    )]
    result = session_with(store, script).take_turn(player_input="I pay the broker", turn=1)
    assert len(result.report.rejected) == 1
    consistency = result.telemetry["notes"]["consistency"]
    assert consistency["problems"] == 0 and consistency["regenerations"] == 0


def test_mood_decay_does_not_apply_to_the_absent(store) -> None:
    store.insert("moods", to_row(MoodState(
        npc_id="npc:3", valence=0.9, arousal=0.4,
        baseline_valence=0.0, baseline_arousal=0.1, last_updated_turn=0,
    )))
    session_with(store, [ProposalSet(narration="You wait.")]).take_turn(
        player_input="I wait", turn=1,
    )
    row = store.find_one("moods", {"npc_id": "npc:3"})
    # Sera is in the market: no decay tick, no row churn for an absent NPC
    assert row["valence"] == 0.9 and row["last_updated_turn"] == 0
