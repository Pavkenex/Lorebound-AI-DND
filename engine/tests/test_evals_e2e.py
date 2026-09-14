"""The shipped e2e suite: green, deterministic, and strict enough to fail.

``report.ok`` alone would pass even if a scenario asserted nothing, so these
tests re-derive the headline claims from the JSON-safe snapshots (catch rate and
per-case handling, the dead NPC's untouched state, memory decay at the prompt
level, cross-transport matrix equality, budget + drop accounting) and read the
persisted rows back out of the scenario's SQLite file. They also prove the
runner *fails* when a claim is false — a harness that only ever passes proves
nothing.
"""
from __future__ import annotations

import copy
import json
import pprint
import sqlite3
from functools import cache
from pathlib import Path

import pytest

from evals import e2e as e2e_module
from evals.e2e import load_e2e_scenarios, run_e2e_suite
from evals.harness import EvalReport, run_all, run_e2e

E2E_DIR = Path(__file__).resolve().parents[1] / "evals" / "scenarios" / "e2e"
SHIPPED = ("e2e-contradiction-battery", "e2e-death-permanence",
           "e2e-memory-retrieval", "e2e-model-matrix", "e2e-telemetry-budget")
CORE = ("boundary-clamps", "conservation", "contradiction-bait", "dead-npc-lock",
        "lead-gates")
BAIT_KINDS = {"dead_npc_prose", "dead_npc_dialogue", "pinned_fact_antonym",
              "pinned_fact_numeric", "pinned_fact_negation", "rejected_restatement"}


def _file(name: str) -> Path:
    """Scenario name -> its file (the shipped names carry the ``e2e-`` prefix)."""
    return E2E_DIR / f"{name.removeprefix('e2e-')}.py"


# --------------------------------------------------------------------------- #
# Helpers
# --------------------------------------------------------------------------- #

@cache
def _suite() -> EvalReport:
    return run_e2e_suite()


def _result(report: EvalReport, name: str):
    return next(result for result in report.results if result.name == name)


def _snapshot(report: EvalReport, name: str) -> dict:
    return _result(report, name).snapshot


def _session(snapshot: dict, name: str = "stub") -> dict:
    return snapshot["sessions"][name]


def _turn(session: dict, turn: int) -> dict:
    return next(record for record in session["turns"] if record["turn"] == turn)


def _rows(snapshot: dict, table: str, session: str = "stub") -> list[dict]:
    return _session(snapshot, session)["final_state"][table]


def _run_alone(name: str, db_dir: Path) -> dict:
    """One shipped scenario, its database kept for direct inspection."""
    report = run_e2e_suite([str(_file(name))], db_dir=str(db_dir))
    assert report.ok, report.failure_lines()
    return report.results[0].snapshot


def _db_rows(db: str, table: str) -> list[dict]:
    with sqlite3.connect(db) as conn:
        conn.row_factory = sqlite3.Row
        return [dict(row) for row in conn.execute(f"SELECT * FROM {table} ORDER BY id")]


# --------------------------------------------------------------------------- #
# Suite-level
# --------------------------------------------------------------------------- #

def test_e2e_suite_is_green_and_covers_the_five_required_scenarios():
    report = _suite()
    assert report.ok, report.failure_lines()
    assert report.kind == "e2e"
    assert (report.total, report.passed, report.failed) == (5, 5, 0)
    assert tuple(result.name for result in report.results) == SHIPPED
    for result in report.results:
        assert result.failures == []
        assert result.snapshot["kind"] == "e2e"
        assert result.snapshot["assertions"] > 0
        assert result.snapshot["sessions"]


def test_e2e_suite_is_deterministic():
    first, second = _suite(), run_e2e_suite()
    assert [r.snapshot["catch_rate"] for r in first.results] == \
           [r.snapshot["catch_rate"] for r in second.results]
    for left, right in zip(first.results, second.results, strict=True):
        assert left.name == right.name
        for session in left.snapshot["sessions"]:
            assert left.snapshot["sessions"][session]["digest"] == \
                   right.snapshot["sessions"][session]["digest"]
        if left.snapshot["matrix"] is not None:
            assert left.snapshot["matrix"]["equal"] is True
            assert right.snapshot["matrix"]["equal"] is True


def test_every_scenario_passes_in_isolation_with_the_same_outcome():
    """No cross-scenario state: running a file alone matches the full run."""
    everything = _suite()
    for name in SHIPPED:
        alone = run_e2e_suite([str(_file(name))])
        assert alone.ok, (name, alone.failure_lines())
        whole = _snapshot(everything, name)
        snapshot = alone.results[0].snapshot
        assert snapshot["catch_rate"] == whole["catch_rate"]
        assert snapshot["matrix"] == whole["matrix"]
        for session, record in snapshot["sessions"].items():
            assert record["digest"] == whole["sessions"][session]["digest"]


def test_core_suite_stays_green_and_does_not_discover_e2e_scenarios():
    core = run_all()
    assert core.ok, core.failure_lines()
    assert tuple(result.name for result in core.results) == CORE
    assert not set(SHIPPED) & {result.name for result in core.results}


def test_run_e2e_facade_delegates_and_takes_a_seed_override():
    report = run_e2e()
    assert isinstance(report, EvalReport)
    assert report.ok, report.failure_lines()
    assert (report.kind, report.total) == ("e2e", 5)
    assert run_e2e(seed=99).seed == 99


def test_scenario_files_are_the_documented_e2e_dsl():
    scenarios = load_e2e_scenarios()
    assert tuple(scenario.name for scenario in scenarios) == SHIPPED
    for scenario in scenarios:
        assert scenario.description
        assert scenario.world and scenario.sessions
        for spec in scenario.sessions:
            assert spec.get("profile", "stub") in e2e_module.PROFILES
            assert spec["turns"]
        assert e2e_module.describe_e2e(scenario).startswith(scenario.name)


# --------------------------------------------------------------------------- #
# Contradiction battery + permanence
# --------------------------------------------------------------------------- #

def test_catch_rate_is_100_percent_and_every_bait_records_its_handling():
    snapshot = _snapshot(_suite(), "e2e-contradiction-battery")
    assert snapshot["catch_rate"] == 1.0
    catches = snapshot["catches"]
    assert len(catches) == 9
    assert [case["turn"] for case in catches] == list(range(40, 49))
    assert all(case["caught"] for case in catches)
    assert all(case["shipped_clean"] for case in catches)
    assert all(case["detectors"] for case in catches)
    assert {kind for case in catches for kind in case["kinds"]} == BAIT_KINDS
    handling = [case["handling"] for case in catches]
    patched = [entry for entry in handling
               if entry["patched_sentences"] or entry["patched_dialogue"]]
    assert [entry["regenerations"] for entry in handling] == [1] * 9
    assert len(patched) == 2  # the two drafts that survive regeneration
    assert sorted(entry["problems_after_regen"] for entry in handling) == \
        [0] * 7 + [1] * 2  # only the patched drafts still had a problem
    # the control turn is clean and drafts nothing
    control = _turn(_session(snapshot), 50)
    assert control["bait"] is None
    assert control["consistency"]["regenerations"] == 0
    assert control["consistency"]["problems"] == 0
    # the delta-channel contradiction is refused by the validator, not narration
    rejected = _turn(_session(snapshot), 49)
    assert rejected["bait"] is None
    assert rejected["report"]["rejected"] == 1
    assert "contradicts existing canon" in " ".join(rejected["report"]["rejected_notes"])


def test_death_permanence_leaves_the_dead_npc_untouched(tmp_path):
    snapshot = _run_alone("e2e-death-permanence", tmp_path)
    session = _session(snapshot)
    npcs = {row["id"]: row for row in _rows(snapshot, "npcs")}
    assert [(row["name"], row["alive"]) for row in npcs.values()] == \
        [("Marla Quist", 0), ("Hob Fen", 1)]
    assert npcs[1]["location_id"] == "yard"  # she never moves after dying
    pinned = [row for row in _rows(snapshot, "world_facts") if row["pinned"]]
    assert len(pinned) == 2  # the seeded fact + the pinned death
    assert any("fever took her" in row["statement"] for row in pinned)
    assert [row["npc_id"] for row in _rows(snapshot, "moods")] == ["npc:2"]
    assert [row["npc_id"] for row in _rows(snapshot, "relationship_ledger")] == ["npc:2"]
    assert not [row for row in _rows(snapshot, "world_facts") if row["source"] == "npc:1"]
    bait = _turn(session, 40)
    assert bait["dialogue"] == []  # nothing she "says" ever ships
    assert bait["report"]["accepted"] == 0
    assert bait["report"]["rejected"] == 6  # mood + relationship + fact, both drafts
    assert bait["consistency"]["regenerations"] == 1
    assert bait["final_clean"] is True
    # the persisted rows agree with the snapshot
    db = session["db"]
    assert [row["npc_id"] for row in _db_rows(db, "moods")] == ["npc:2"]
    assert [row["alive"] for row in _db_rows(db, "npcs")] == [0, 1]
    assert not [row for row in _db_rows(db, "relationship_ledger")
                if row["npc_id"] == "npc:1"]


def test_memory_scenario_decays_the_stale_grievance_and_keeps_the_promise(tmp_path):
    snapshot = _run_alone("e2e-memory-retrieval", tmp_path)
    session = _session(snapshot)
    entries = {row["statement"]: row for row in _rows(snapshot, "npc_memory")}
    reinforced = entries["Rell promised Marla he would keep the night watch at the well."]
    control = entries["Rell promised Marla he would stand watch at the old well."]
    stale = entries["Rell broke the salt tally, and Marla counts it against him."]
    fresh = entries["Rell shorted the salt tally again this week."]
    assert (reinforced["reinforced_count"], reinforced["last_referenced_turn"]) == (3, 62)
    assert (control["reinforced_count"], control["last_referenced_turn"]) == (0, 0)
    assert stale["last_referenced_turn"] == 1  # retrieved once, then never again
    assert fresh["last_referenced_turn"] == 40  # retrieved when it was still fresh
    assert stale["type"] == "grievance" and stale["decay_rate"] > 0
    assert fresh["decay_rate"] == stale["decay_rate"]  # the decay rate is the type's
    # the last prompt carries the reinforced promise and not the stale control
    prompt_text = _turn(session, 62)["prompt"]["text"]
    assert "keep the night watch at the well" in prompt_text
    assert "stand watch at the old well" not in prompt_text
    assert "left the salt tally short on purpose" in prompt_text  # recent grief stays
    assert session["verdicts"]["rejected"] == 0
    assert session["stats"]["calls"] == 4
    # the persisted rows agree with the snapshot
    db = session["db"]
    rows = {row["statement"]: row for row in _db_rows(db, "npc_memory")}
    assert rows[reinforced["statement"]]["reinforced_count"] == 3
    assert rows[control["statement"]]["reinforced_count"] == 0


# --------------------------------------------------------------------------- #
# Cross-model matrix + telemetry
# --------------------------------------------------------------------------- #

def test_model_matrix_is_identical_across_transports():
    snapshot = _snapshot(_suite(), "e2e-model-matrix")
    matrix = snapshot["matrix"]
    assert matrix["equal"] is True
    assert matrix["diffs"] == []
    assert set(matrix["sessions"]) == {"native", "flaky", "degraded"}
    native, flaky, degraded = (_session(snapshot, name)
                               for name in ("native", "flaky", "degraded"))
    assert native["digest"] == flaky["digest"] == degraded["digest"]
    assert native["stats"]["native_tools"] is True
    assert degraded["stats"]["native_tools"] is False
    assert (native["stats"]["calls"], native["stats"]["requests"]) == (3, 3)
    assert (flaky["stats"]["calls"], flaky["stats"]["requests"]) == (3, 3)
    assert (degraded["stats"]["calls"], degraded["stats"]["requests"]) == (4, 4)
    assert flaky["stats"]["repairs"] == 1  # one malformed reply, repaired silently
    assert degraded["stats"]["parse_failures"] == 1  # one prose reply, retried
    assert degraded["stats"]["regenerations"] == 1
    assert native["stats"]["regenerations"] == flaky["stats"]["regenerations"] == 0
    # the degraded retry lives inside the model layer: one narrate() call for the
    # turn, two adapter requests
    retried = _turn(degraded, 3)
    assert len(retried["calls"]) == 1 and retried["requests"] == 2
    assert retried["narration"] == _turn(native, 3)["narration"] == \
        _turn(flaky, 3)["narration"]


def test_telemetry_scenario_reports_the_budget_and_its_drops(tmp_path):
    snapshot = _run_alone("e2e-telemetry-budget", tmp_path)
    session = _session(snapshot, "native")
    assert len(session["turns"]) == 2
    for record in session["turns"]:
        prompt = record["prompt"]
        budget = prompt["budget"]
        assert budget["window"] == 1200
        assert budget["used"] <= budget["total"]
        assert budget["output_reserve"] + budget["total"] == budget["window"]
        assert sum(prompt["sections"].values()) == budget["used"]
        assert sum(prompt["allocations"].values()) == budget["total"]
        dropped = prompt["dropped"]
        assert dropped, "a 1200-token window must drop something"
        assert all(entry["dropped_tokens"] > 0 for entry in dropped)
        assert all(entry["reason"] in ("over_budget", "system_ceiling")
                   for entry in dropped)
        sections = [entry["section"] for entry in dropped]
        assert "saga" in sections and "mechanics" not in sections
        assert prompt["sections"]["mechanics"] > 0
        # the drop order follows the declared priority: saga first, system last
        assert sections.index("saga") < sections.index("system")
    # non-negotiable content survives both turns even as the pressure changes
    assert session["turns"][0]["prompt"]["sections"]["system"] > 0
    assert session["turns"][0]["prompt"]["sections"]["pinned_facts"] > 0
    # the rows the pipeline wrote are readable from the session database
    rows = _db_rows(session["db"], "telemetry")
    assert [row["turn"] for row in rows] == [1, 2]
    assert (rows[-1]["provider"], rows[-1]["model"]) == ("fake-native", "budget-native")
    assert json.loads(rows[-1]["budget_alloc"]) == \
        session["turns"][1]["prompt"]["allocations"]
    # the row's own token columns are the pipeline's accounting, not a guess
    assert rows[-1]["prompt_tokens"] == session["turns"][1]["prompt"]["budget"]["used"]
    assert rows[-1]["completion_tokens"] > 0
    assert json.loads(rows[-1]["notes"])["consistency"]["regenerations"] == 0


# --------------------------------------------------------------------------- #
# The runner must fail when a claim is false
# --------------------------------------------------------------------------- #

_WORLD = {
    "world": [{"seed": "tiny", "day": 1, "hour": 12, "active_scene_id": "yard"}],
    "locations": [{"name": "The yard", "flags": {"slug": "yard"}}],
    "characters": [{"name": "Rell", "ref": "player", "location_id": "yard",
                    "stats": {"hp": 11, "max_hp": 11, "currency": 6}}],
    "npcs": [{"name": "Marla Quist", "ref": "marla", "location_id": "yard",
              "alive": True}],
}


def _envelope(narration: str, deltas: list | None = None) -> dict:
    return {"envelope": {"narration": narration, "npc_dialogue": [],
                         "deltas": list(deltas or [])}}


def _tiny(**overrides) -> dict:
    mapping = {
        "name": "tiny",
        "description": "a one-turn scenario for failure probes",
        "options": {"seed": 5},
        "world": copy.deepcopy(_WORLD),
        "sessions": [{
            "name": "stub",
            "turns": [{
                "turn": 1, "input": "I wait by the well.",
                "replies": [_envelope("The yard is quiet.")],
                "expect": {"calls": 1},
                "checks": [{"expr": "state['character'].stats.get('currency') == 6",
                            "msg": "the purse holds six"}],
            }],
        }],
    }
    mapping.update(overrides)
    return mapping


def _write(tmp_path: Path, name: str, mapping: dict) -> Path:
    path = tmp_path / f"{name}.py"
    path.write_text("E2E = " + pprint.pformat(mapping, width=96, sort_dicts=False) + "\n",
                    encoding="utf-8")
    return path


def test_a_wrong_expectation_fails_the_run(tmp_path):
    mapping = _tiny()
    mapping["sessions"][0]["turns"][0]["expect"] = {"calls": 5,
                                                    "narration_contains": ["dragon"]}
    report = run_e2e_suite([str(_write(tmp_path, "wrong_expect", mapping))])
    assert not report.ok
    failures = report.results[0].failures
    assert any("expected 5 narrator call(s), got 1" in failure for failure in failures)
    assert any("narration_contains: 'dragon' not found" in failure for failure in failures)


def test_a_corrupted_check_fails_the_run(tmp_path):
    mapping = _tiny()
    mapping["sessions"][0]["turns"][0]["checks"] = [
        {"expr": "state['character'].stats.get('currency') == 999",
         "msg": "the purse holds six"}]
    report = run_e2e_suite([str(_write(tmp_path, "wrong_check", mapping))])
    assert not report.ok
    assert any("turn 1 check 1 failed: the purse holds six" in failure
               for failure in report.results[0].failures)


def test_a_broken_check_expression_fails_the_run(tmp_path):
    mapping = _tiny()
    mapping["sessions"][0]["turns"][0]["checks"] = [
        {"expr": "no_such_helper()", "msg": "the helper exists"}]
    report = run_e2e_suite([str(_write(tmp_path, "broken_check", mapping))])
    assert not report.ok
    assert any("NameError" in failure for failure in report.results[0].failures)


def test_a_script_the_pipeline_never_consumes_fails_the_run(tmp_path):
    mapping = _tiny()
    mapping["sessions"][0]["turns"][0]["replies"] = [
        _envelope("The yard is quiet."), _envelope("A second reply nobody reads.")]
    report = run_e2e_suite([str(_write(tmp_path, "leftover", mapping))])
    assert not report.ok
    assert any("never consumed" in failure for failure in report.results[0].failures)


def test_a_scenario_without_assertions_fails_the_run(tmp_path):
    mapping = _tiny()
    mapping["sessions"][0]["turns"][0].pop("expect")
    mapping["sessions"][0]["turns"][0].pop("checks")
    report = run_e2e_suite([str(_write(tmp_path, "no_assertions", mapping))])
    assert not report.ok
    assert any("no assertions" in failure for failure in report.results[0].failures)


def test_wrong_session_stats_fail_the_run(tmp_path):
    mapping = _tiny()
    mapping["sessions"][0]["expect_stats"] = {"kind": "stub", "calls": 3}
    report = run_e2e_suite([str(_write(tmp_path, "wrong_stats", mapping))])
    assert not report.ok
    assert any("expected narrator stat calls=3" in failure
               for failure in report.results[0].failures)


def test_matrix_divergence_is_reported(tmp_path):
    mapping = _tiny()
    mapping["sessions"] = [
        {"name": "a", "turns": [{"turn": 1, "input": "I wait.",
                                 "replies": [_envelope("The yard is quiet.")],
                                 "expect": {"calls": 1}}]},
        {"name": "b", "turns": [{"turn": 1, "input": "I wait.",
                                 "replies": [_envelope("The yard is very quiet.")],
                                 "expect": {"calls": 1}}]},
    ]
    mapping["matrix"] = {"sessions": ["a", "b"], "compare_prompts": True}
    report = run_e2e_suite([str(_write(tmp_path, "divergent", mapping))])
    assert not report.ok
    snapshot = report.results[0].snapshot
    assert snapshot["matrix"]["equal"] is False
    assert any("diverge across transports" in failure
               for failure in report.results[0].failures)
    assert not snapshot["catches"]


def test_a_bait_that_is_caught_passes_and_a_missed_bait_fails(tmp_path):
    mapping = _tiny()
    mapping["world"]["world_facts"] = [
        {"statement": "The east vault is locked with three seals.",
         "pinned": True, "established_turn": 1, "source": "narrator"}]
    mapping["sessions"][0]["turns"][0].update({
        "bait": True,
        "replies": [_envelope("The east vault is locked with five seals."),
                    _envelope("The vault is locked, and the seals are cold.")],
        "expect": {"calls": 2},
    })
    report = run_e2e_suite([str(_write(tmp_path, "bait_caught", mapping))])
    assert report.ok, report.failure_lines()
    caught = report.results[0].snapshot
    assert caught["catch_rate"] == 1.0
    assert caught["catches"][0]["kinds"] == ["pinned_fact_numeric"]
    assert caught["catches"][0]["handling"]["patched_sentences"] == 0
    # the same scenario with prose the detector cannot see must fail
    mapping["name"] = "bait_missed"
    mapping["sessions"][0]["turns"][0]["replies"] = [
        _envelope("Dust settles over the empty road."),
        _envelope("Nothing moves on the road.")]
    report = run_e2e_suite([str(_write(tmp_path, "bait_missed", mapping))])
    assert not report.ok
    assert any("bait turn was not handled end to end" in failure
               for failure in report.results[0].failures)


# --------------------------------------------------------------------------- #
# Load-time validation + internals worth pinning
# --------------------------------------------------------------------------- #

@pytest.mark.parametrize("mutate, message", [
    (lambda m: m.update({"nope": 1}), "unknown scenario key"),
    (lambda m: m["sessions"][0]["turns"][0]["expect"].update({"calls_x": 1}),
     "unknown expectation key"),
    (lambda m: m["sessions"][0]["turns"][0]["replies"][0].update({"via": "telepathy"}),
     "unknown transport"),
    (lambda m: m["sessions"][0]["turns"][0]["replies"][0].update({"via": "text"}),
     "no transport variants"),
    (lambda m: m["sessions"][0].update({"expect_stats": {"calls_x": 1}}),
     "unknown expect_stats key"),
    (lambda m: m["sessions"][0]["turns"][0].update(
        {"steps": [{"memory": {"action": "forget", "npc_id": "npc:1",
                               "statement": "x"}}]}),
     "memory action must be one of"),
    (lambda m: m.update({"matrix": {"sessions": ["missing", "stub"]}}),
     "unknown session"),
    (lambda m: m["sessions"][0].update({"turns": []}), "non-empty 'turns'"),
    (lambda m: m["sessions"][0]["turns"][0].update({"replies": []}),
     "non-empty 'replies'"),
])
def test_scenario_validation_rejects_broken_files(tmp_path, mutate, message):
    mapping = _tiny()
    mutate(mapping)
    with pytest.raises(ValueError, match=message):
        load_e2e_scenarios([str(_write(tmp_path, "broken", mapping))])


def test_reply_shape_is_validated(tmp_path):
    mapping = _tiny()
    mapping["sessions"][0]["turns"][0]["replies"] = [
        {"envelope": {"narration": "x"}, "text": "both"}]
    with pytest.raises(ValueError, match="exactly one of 'envelope' or 'text'"):
        load_e2e_scenarios([str(_write(tmp_path, "two_bodies", mapping))])


def test_json_scenarios_load_with_the_same_dsl(tmp_path):
    path = tmp_path / "tiny.json"
    path.write_text(json.dumps(_tiny()), encoding="utf-8")
    report = run_e2e_suite([str(path)])
    assert report.ok, report.failure_lines()
    assert report.results[0].snapshot["catch_rate"] is None  # no bait turns


def test_detector_kinds_cover_the_battery_and_unknown_details_stay_visible():
    classify = e2e_module._detector_kind
    assert classify("Marla Quist has Marla Quist speak, but Marla Quist is dead "
                    "(alive = 0)") == "dead_npc_prose"
    assert classify("Marla Quist is dead (alive = 0) but speaks in this turn's "
                    "dialogue") == "dead_npc_dialogue"
    assert classify("the narration contradicts pinned fact #2 (antonym pair "
                    "'open'/'closed')") == "pinned_fact_antonym"
    assert classify("the narration contradicts pinned fact #1 (numeric conflict: "
                    "3 vs 5)") == "pinned_fact_numeric"
    assert classify("the narration contradicts pinned fact #3 (negation flip)") == \
        "pinned_fact_negation"
    assert classify("the narration contradicts pinned fact #4 (statement disagrees)") == \
        "pinned_fact_other"  # unrecognized reasons stay visible, never silently bucketed
    assert classify("the narration restates a delta the validator rejected "
                    "(currency on )") == "rejected_restatement"
    assert classify("something nobody has seen before") == "other"


def test_transport_table_matches_the_declared_transports():
    assert e2e_module.PROFILES == ("stub", "native", "flaky", "degraded")
    assert set(e2e_module._TRANSPORT) == set(e2e_module._TRANSPORTS)
    assert set(e2e_module._TRANSPORT["auto"]) == {"stub", "live", "degraded"}
    assert e2e_module._TRANSPORT["auto"]["stub"][0] == "payload"
