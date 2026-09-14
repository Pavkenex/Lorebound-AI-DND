"""The shipped core scenario suite: green, isolated, and outcome-specific.

``report.ok`` alone would pass if the scenarios asserted nothing, so every
scenario's real end state is asserted here from its JSON-safe snapshot
(verdict counts, exact clamp values, committed rows).
"""
from __future__ import annotations

import json
import math
from pathlib import Path

from evals.harness import load_scenarios, run_all, run_scenarios

SCENARIOS_DIR = Path(__file__).resolve().parents[1] / "evals" / "scenarios"
EXPECTED = ("boundary-clamps", "conservation", "contradiction-bait",
            "dead-npc-lock", "lead-gates")

# boundary-clamps reads its relationship meters DECAYED (spec §3.5), so a meter
# driven to ±100 one turn earlier sits one decay step inside the edge: the
# follow-up probe clamps to the slack the decay opened, not 0.0 — and the meter
# still lands exactly on ±100. Classes follow the applied magnitude (>=25
# durable, >=10 slow, else fast): the +10 remainder is "slow" (0.01/turn) and
# the -5 remainder is "fast" (0.08/turn). The scenario file derives the same
# constants beside the steps that pin their turns.
_TAM_SLACK = round(100 - round(90 + 10 * math.exp(-0.01), 6), 6)
_MARLA_SLACK = round(-100 - round(-95 - 5 * math.exp(-0.08), 6), 6)


def _snapshot(name: str, report) -> dict:
    return next(result.snapshot for result in report.results if result.name == name)


def _rows(snapshot: dict, table: str) -> list[dict]:
    return snapshot["final_state"][table]


def _by_id(snapshot: dict, table: str) -> dict[int, dict]:
    return {row["id"]: row for row in _rows(snapshot, table)}


# --------------------------------------------------------------------------- #
# Suite-level
# --------------------------------------------------------------------------- #

def test_core_suite_is_green_and_covers_the_five_required_scenarios():
    report = run_all()
    assert report.ok, report.failure_lines()
    assert (report.total, report.passed, report.failed) == (5, 5, 0)
    assert tuple(result.name for result in report.results) == EXPECTED
    for result in report.results:
        assert result.failures == []
        assert result.snapshot["assertions"] >= result.snapshot["checks"] > 0


def test_every_scenario_passes_in_isolation_with_the_same_outcome():
    """No cross-scenario state: running a file alone matches the full run."""
    everything = run_all()
    for path in sorted(SCENARIOS_DIR.glob("*")):
        if path.name.startswith("_") or path.suffix not in (".json", ".py"):
            continue
        alone = run_scenarios([path])
        assert alone.ok, (path, alone.failure_lines())
        snapshot = alone.results[0].snapshot
        whole = _snapshot(alone.results[0].name, everything)
        assert snapshot["verdicts"] == whole["verdicts"]
        assert snapshot["outcomes"] == whole["outcomes"]
        assert snapshot["final_state"] == whole["final_state"]


def test_core_suite_is_deterministic():
    first, second = run_all(), run_all()
    assert [r.snapshot["verdicts"] for r in first.results] == \
           [r.snapshot["verdicts"] for r in second.results]
    assert [r.snapshot["outcomes"] for r in first.results] == \
           [r.snapshot["outcomes"] for r in second.results]
    assert [r.snapshot["final_state"] for r in first.results] == \
           [r.snapshot["final_state"] for r in second.results]


def test_core_run_collects_telemetry_per_step():
    report = run_all()
    assert report.telemetry
    per_scenario: dict[str, int] = {}
    finals = 0
    for entry in report.telemetry:
        if entry["step"] is None:  # the per-scenario final checks row
            assert entry["kind"] == "checks"
            assert set(entry) == {"scenario", "step", "kind", "checks",
                                  "assertions", "failures"}
            finals += 1
            continue
        assert set(entry) == {"scenario", "step", "kind", "turn", "verdicts",
                              "outcome_kind", "ok", "note"}
        assert entry["ok"] is True
        per_scenario[entry["scenario"]] = per_scenario.get(entry["scenario"], 0) + 1
    assert finals == len(report.results)
    for result in report.results:
        assert per_scenario[result.name] == len(result.snapshot["steps"])


def test_scenario_files_are_the_documented_dsl():
    scenarios = load_scenarios(root=str(SCENARIOS_DIR))
    assert tuple(scenario.name for scenario in scenarios) == EXPECTED
    assert {scenario.source.rsplit(".", 1)[-1] for scenario in scenarios} == {"json", "py"}
    for scenario in scenarios:
        assert scenario.description
        assert not scenario.turns  # core scenarios never need the pipeline


# --------------------------------------------------------------------------- #
# Per-scenario outcomes
# --------------------------------------------------------------------------- #

def test_dead_npc_lock_outcomes():
    snapshot = _snapshot("dead-npc-lock", run_all())
    npcs = _by_id(snapshot, "npcs")
    assert npcs[snapshot["refs"]["grunn"]]["alive"] == 0  # the death is committed
    assert npcs[snapshot["refs"]["marla"]]["alive"] == 1
    assert snapshot["verdicts"] == {"accepted": 2, "clamped": 0, "rejected": 4}
    assert snapshot["final_state"]["relationship_ledger"] == []
    assert snapshot["final_state"]["moods"] == []
    assert [row["statement"] for row in _rows(snapshot, "world_facts")] == [
        "Marla the tanner keeps the ledger of debts",
        "The mill road was washed out by the storm",
    ]
    rejected = [verdict for step in snapshot["steps"] for verdict in step["verdicts"]
                if verdict["kind"] == "rejected"]
    assert len(rejected) == 4
    assert sum("dead" in verdict["note"] for verdict in rejected) == 4
    assert sum("revival by fiat" in verdict["note"] for verdict in rejected) == 1
    assert [outcome["kind"] for outcome in snapshot["outcomes"]] == ["blocked", "attack"]
    assert snapshot["outcomes"][0]["band"] is None  # a blocked action rolls nothing
    assert snapshot["outcomes"][0]["roll"] is None
    assert snapshot["outcomes"][1]["band"] == "success" and snapshot["outcomes"][1]["roll"] == 15
    # spec §9's canonical shape: kill in turn 5, probe in turn 40
    assert [step["turn"] for step in snapshot["steps"]] == [5, 6, 7, 8, 9, 40, 41, 42, 43]
    assert snapshot["turn"] == 44


def test_conservation_outcomes():
    snapshot = _snapshot("conservation", run_all())
    character = _rows(snapshot, "characters")[0]
    assert json.loads(character["stats"])["currency"] == 0
    assert json.loads(character["inventory"]) == []
    assert snapshot["verdicts"] == {"accepted": 4, "clamped": 0, "rejected": 6}
    rejected = [verdict for step in snapshot["steps"] for verdict in step["verdicts"]
                if verdict["kind"] == "rejected"]
    assert all("conservation" in verdict["note"] for verdict in rejected)
    # the refused deltas carry the real numbers, not the model's claim
    assert any("balance of 25" in verdict["note"] for verdict in rejected)
    assert any("holds 2 x rope" in verdict["note"] for verdict in rejected)


def test_lead_gates_outcomes():
    snapshot = _snapshot("lead-gates", run_all())
    leads = _by_id(snapshot, "leads")
    shipment = leads[snapshot["refs"]["shipment"]]
    history = json.loads(shipment["stage_history"])
    assert shipment["stage"] == "resolved"
    assert [entry["stage"] for entry in history] == [
        "rumored", "accepted", "in_progress", "complicated", "resolved"]
    assert [entry["turn"] for entry in history] == [4, 5, 7, 8, 9]
    assert history[0]["trigger"] == "the innkeeper mentioned it"
    untouched = leads[snapshot["refs"]["debt"]]
    assert untouched["stage"] == "unheard" and json.loads(untouched["stage_history"]) == []
    assert snapshot["verdicts"] == {"accepted": 5, "clamped": 0, "rejected": 5}
    rejected = [verdict for step in snapshot["steps"] for verdict in step["verdicts"]
                if verdict["kind"] == "rejected"]
    assert sum("illegal lead transition" in verdict["note"] for verdict in rejected) == 3
    assert sum("unknown lead" in verdict["note"] for verdict in rejected) == 2


def test_contradiction_bait_outcomes():
    snapshot = _snapshot("contradiction-bait", run_all())
    statements = [row["statement"] for row in _rows(snapshot, "world_facts")]
    assert statements == [
        "Marla the tanner is dead",
        "The east vault is locked with three seals",
        "The north gate is not barred at night",
        "The mill road was washed out by the storm",
    ]
    assert not any("five seals" in statement for statement in statements)
    assert snapshot["verdicts"] == {"accepted": 2, "clamped": 0, "rejected": 4}
    rejected = [verdict for step in snapshot["steps"] for verdict in step["verdicts"]
                if verdict["kind"] == "rejected"]
    reasons = " ".join(verdict["note"] for verdict in rejected)
    assert "antonym" in reasons and "numeric conflict" in reasons and "negation flip" in reasons
    assert sum("contradicts existing canon" in verdict["note"] for verdict in rejected) == 4
    reinforced = [verdict for step in snapshot["steps"] for verdict in step["verdicts"]
                  if verdict["kind"] == "accepted" and "reinforc" in verdict["note"]]
    assert len(reinforced) == 1  # the near-duplicate, not a second row


def test_boundary_clamps_outcomes():
    snapshot = _snapshot("boundary-clamps", run_all())
    moods = {row["npc_id"]: row for row in _rows(snapshot, "moods")}
    assert (moods["npc:1"]["valence"], moods["npc:1"]["arousal"]) == (1.0, -0.9)
    assert moods["npc:2"]["valence"] == -1.0
    assert (moods["npc:3"]["valence"], moods["npc:3"]["arousal"]) == (0.7, 0.2)
    ledger: dict[str, list[float]] = {}
    for row in _rows(snapshot, "relationship_ledger"):
        ledger.setdefault(row["npc_id"], []).append(row["delta"])
    assert ledger == {"npc:1": [10.0, _TAM_SLACK], "npc:2": [-5.0, _MARLA_SLACK],
                      "npc:3": [12.0], "npc:4": [40.0]}
    stats = json.loads(_rows(snapshot, "characters")[0]["stats"])
    assert stats["hp"] == 0 and stats["might"] == -10 and stats["currency"] == 10
    assert snapshot["verdicts"] == {"accepted": 2, "clamped": 14, "rejected": 0}
    clamped = [verdict for step in snapshot["steps"] for verdict in step["verdicts"]
               if verdict["kind"] == "clamped"]
    assert [verdict["clamped_to"] for verdict in clamped] == [
        {"valence_delta": 0.4, "arousal_delta": -0.5},
        {"valence_delta": -1.0, "arousal_delta": 0.0},
        {"valence_delta": 0.0, "arousal_delta": 0.0},
        10.0, _TAM_SLACK, -5.0, _MARLA_SLACK, 40.0,
        7.0, -10.0, 0.0,
        21.0, -40.0, 0.0,
    ]
