"""Harness mechanics (R6): loading, namespace, throwaway DBs, and breakage.

The harness is eval infrastructure, so it gets the same rigor as the thing it
measures — the tests that prove it catches breakage execute real runs and
assert the failures (spec §9: "don't ship on vibes").
"""
from __future__ import annotations

import copy
import json
import subprocess
import sys
from pathlib import Path

import pytest

from evals.harness import (
    DEFAULT_SEED,
    Scenario,
    load_scenarios,
    run_e2e,
    run_scenarios,
)

ENGINE_ROOT = Path(__file__).resolve().parents[1]
SCENARIOS_DIR = ENGINE_ROOT / "evals" / "scenarios"
SHIPPED = ("boundary-clamps", "conservation", "contradiction-bait",
           "dead-npc-lock", "lead-gates")


def _write_json(directory: Path, name: str, mapping: dict) -> Path:
    path = directory / f"{name}.json"
    path.write_text(json.dumps(mapping), encoding="utf-8")
    return path


def _write_py(directory: Path, name: str, mapping: dict) -> Path:
    path = directory / f"{name}.py"
    path.write_text(f"SCENARIO = {mapping!r}\n", encoding="utf-8")
    return path


# --------------------------------------------------------------------------- #
# Loading
# --------------------------------------------------------------------------- #

def test_load_scenarios_reads_json_and_python_files(tmp_path):
    _write_json(tmp_path, "b-second", {
        "name": "second", "description": "json one", "options": {"seed": 3},
        "world": {"npcs": [{"name": "Marla"}]},
        "checks": [{"expr": "store.count('npcs') == 1"}],
    })
    _write_py(tmp_path, "a-first", {
        "name": "first", "steps": [{"update": {"table": "npcs", "id": 1,
                                               "data": {"alive": 0}}}],
        "checks": [{"expr": "True"}],
    })
    scenarios = load_scenarios(root=str(tmp_path))
    assert [scenario.name for scenario in scenarios] == ["first", "second"]
    assert all(isinstance(scenario, Scenario) for scenario in scenarios)
    assert scenarios[1].options == {"seed": 3}
    assert scenarios[0].steps[0]["update"]["table"] == "npcs"
    assert scenarios[0].source.endswith("a-first.py")


def test_load_scenarios_validates_the_dsl(tmp_path):
    bad_cases = [
        {"name": "x", "surprise": True},
        {"name": "x", "options": {"nonsense": 1}},
        {"name": "x", "steps": [{"teleport": {}}]},
        {"name": "x", "steps": [{"apply": [], }]},
        {"name": "x", "world": {"goblins": [{"name": "g"}]}},
        {"name": "x", "world": {"npcs": [{"nickname": "g"}]}},
        {"name": "x", "checks": [{"msg": "no expr"}]},
        {"name": "", "checks": [{"expr": "True"}]},
        {"name": "x", "turns": "not a list"},
    ]
    for index, mapping in enumerate(bad_cases):
        path = _write_json(tmp_path, f"bad{index}", mapping)
        with pytest.raises(ValueError):
            load_scenarios([path])


def test_load_scenarios_rejects_duplicate_names(tmp_path):
    _write_json(tmp_path, "one.json", {"name": "same", "checks": [{"expr": "True"}]})
    _write_json(tmp_path, "two.json", {"name": "same", "checks": [{"expr": "True"}]})
    with pytest.raises(ValueError, match="duplicate scenario name"):
        load_scenarios(root=str(tmp_path))


def test_load_scenarios_without_files_raises(tmp_path):
    with pytest.raises(ValueError, match="no scenario files"):
        load_scenarios(root=str(tmp_path))


# --------------------------------------------------------------------------- #
# Running: namespace, snapshots, telemetry, isolation
# --------------------------------------------------------------------------- #

def test_run_scenarios_exposes_the_documented_namespace(tmp_path):
    path = _write_json(tmp_path, "namespace.json", {
        "name": "namespace-smoke",
        "world": {
            "characters": [{"name": "Player", "stats": {"currency": 5}, "ref": "player"}],
            "npcs": [{"name": "Grunn", "ref": "grunn"}],
        },
        "steps": [
            {"apply": [{"kind": "currency", "data": {"amount": -5}}], "expect": ["accepted"]},
            {"check": {"expr": "state['character'].stats['currency'] == 0",
                       "msg": "the spend applied"}},
        ],
        "checks": [
            {"expr": "npc[refs['grunn']].name == 'Grunn'"},
            {"expr": "npc_by_name['Grunn'] is npc[refs['grunn']]"},
            {"expr": "store.count('characters') == 1"},
            {"expr": "facts == [] and memories == [] and ledger == []"},
            {"expr": "turn == 3"},
            {"expr": "verdict_count('accepted') == 1"},
            {"expr": "steps[0]['kind'] == 'apply' and steps[1]['kind'] == 'check'"},
            {"expr": "reports[0].accepted[0].kind == 'accepted' and outcomes == []"},
            {"expr": "state['inventory'] == [] and count_rows('npcs') == 1"},
            {"expr": "config.player_id == 'player' and validator.store is store"},
        ],
    })
    report = run_scenarios([path])
    assert report.ok
    result = report.results[0]
    assert result.passed and result.failures == []
    snapshot = result.snapshot
    assert snapshot["seed"] == DEFAULT_SEED
    assert snapshot["verdicts"] == {"accepted": 1, "clamped": 0, "rejected": 0}
    # 10 final checks + 1 inline check + 1 step expectation
    assert snapshot["assertions"] == 12 and snapshot["checks"] == 10
    assert snapshot["refs"] == {"player": 1, "grunn": 1}
    assert len(snapshot["steps"]) == 2
    stats = json.loads(snapshot["final_state"]["characters"][0]["stats"])
    assert stats == {"currency": 0}
    assert snapshot["telemetry_rows"] == 3  # two steps + the final checks row
    assert len(report.telemetry) == 3
    assert report.telemetry[0]["kind"] == "apply"
    assert report.telemetry[0]["verdicts"]["accepted"] == 1
    assert report.telemetry[-1]["kind"] == "checks" and report.telemetry[-1]["step"] is None


def test_run_scenarios_uses_a_throwaway_db_per_scenario(tmp_path):
    first = _write_json(tmp_path, "a-seeds", {
        "name": "seeds-rows",
        "world": {"npcs": [{"name": "Grunn"}]},
        "steps": [{"check": {"expr": "store.count('npcs') == 1", "msg": "own row"}}],
        "checks": [{"expr": "count_rows('npcs') == 1"}],
    })
    second = _write_json(tmp_path, "b-sees-nothing", {
        "name": "sees-nothing",
        "checks": [
            {"expr": "count_rows('npcs') == 0", "msg": "a fresh DB per scenario"},
            {"expr": "turn == 1"},
        ],
    })
    report = run_scenarios([first, second])
    assert report.ok, report.failure_lines()


def test_run_scenarios_db_dir_keeps_dbs_and_tempdir_is_cleaned(tmp_path):
    path = _write_json(tmp_path, "tiny", {
        "name": "tiny", "checks": [{"expr": "count_rows('npcs') == 0"}],
    })
    kept = run_scenarios([path], db_dir=str(tmp_path / "dbs"))
    assert kept.db_dir is not None
    assert Path(kept.db_dir).is_dir()
    assert (Path(kept.db_dir) / "tiny.sqlite").is_file()

    throwaway = run_scenarios([path])
    assert throwaway.db_dir is not None
    assert not Path(throwaway.db_dir).exists()  # the tempdir is gone after the run


def test_scripted_rng_is_exact_and_runs_are_deterministic(tmp_path):
    path = _write_json(tmp_path, "dice.json", {
        "name": "dice", "options": {"seed": 4, "rng": [15]},
        "world": {"characters": [{"name": "Player", "location_id": "yard"}],
                  "npcs": [{"name": "Tam", "location_id": "yard"}]},
        "steps": [{"resolve": {"kind": "action", "target": "npc:1", "text": "swing"},
                   "expect": {"kind": "attack", "band": "success", "roll": 15, "total": 15}}],
        "checks": [{"expr": "outcomes[0].check.modifier == 0"}],
    })
    first, second = run_scenarios([path]), run_scenarios([path])
    assert first.ok and second.ok
    outcomes = first.results[0].snapshot["outcomes"]
    assert outcomes == second.results[0].snapshot["outcomes"]
    assert outcomes[0]["roll"] == 15 and outcomes[0]["band"] == "success"
    assert first.results[0].snapshot["seed"] == 4


def test_validate_step_writes_nothing(tmp_path):
    path = _write_json(tmp_path, "readonly.json", {
        "name": "readonly",
        "steps": [
            {"validate": [{"kind": "fact", "data": {"statement": "A new rumour spreads"}}],
             "expect": ["accepted"]},
            {"check": {"expr": "store.count('world_facts') == 0",
                       "msg": "validate is read-only"}},
        ],
        "checks": [{"expr": "count_rows('world_facts') == 0"}],
    })
    report = run_scenarios([path])
    assert report.ok, report.failure_lines()


def test_insert_and_update_steps(tmp_path):
    path = _write_json(tmp_path, "rows.json", {
        "name": "rows",
        "world": {"npcs": [{"name": "Grunn", "ref": "grunn"}]},
        "steps": [
            {"insert": {"table": "npcs", "data": {"name": "Odo"}, "ref": "odo"},
             "note": "a late arrival"},
            {"update": {"table": "npcs", "id": 2, "data": {"alive": 0}},
             "expect": {"updated": True}},
            {"update": {"table": "npcs", "id": 99, "data": {"alive": 0}},
             "expect": {"updated": False}},
        ],
        "checks": [
            {"expr": "npc[refs['odo']].name == 'Odo' and npc[refs['odo']].alive is False"},
            {"expr": "npc[refs['grunn']].alive is True"},
            {"expr": "steps[2]['updated'] is False"},
        ],
    })
    report = run_scenarios([path])
    assert report.ok, report.failure_lines()


# --------------------------------------------------------------------------- #
# Breakage: the harness must FAIL when it should
# --------------------------------------------------------------------------- #

def test_breakage_wrong_check_fails(tmp_path):
    path = _write_json(tmp_path, "wrong", {
        "name": "wrong-check",
        "world": {"npcs": [{"name": "Grunn"}]},
        "checks": [{"expr": "store.count('npcs') == 2", "msg": "there are two npcs"}],
    })
    report = run_scenarios([path])
    assert not report.ok
    result = report.results[0]
    assert not result.passed
    assert any("there are two npcs" in failure for failure in result.failures)
    assert "FAIL wrong-check" in report.render()


def test_breakage_raising_check_is_reported_not_crashed(tmp_path):
    path = _write_json(tmp_path, "raising", {
        "name": "raising-check",
        "checks": [{"expr": "store.count('no_such_table')", "msg": "table exists"}],
    })
    report = run_scenarios([path])
    assert not report.ok
    assert any("raised ValueError" in failure for failure in report.results[0].failures)


def test_breakage_wrong_step_expectation_fails(tmp_path):
    path = _write_json(tmp_path, "expect", {
        "name": "wrong-expect",
        "world": {"npcs": [{"name": "Grunn"}]},
        "steps": [{"apply": [{"kind": "mood", "target": "npc:1",
                              "data": {"valence_delta": 0.2, "arousal_delta": 0.1}}],
                   "expect": ["rejected"]}],
        "checks": [{"expr": "True"}],
    })
    report = run_scenarios([path])
    assert not report.ok
    failure = report.results[0].failures[0]
    assert "expected kind 'rejected', got 'accepted'" in failure


def test_breakage_wrong_clamped_value_fails(tmp_path):
    path = _write_json(tmp_path, "clamp", {
        "name": "wrong-clamp",
        "world": {"npcs": [{"name": "Grunn"}],
                  "moods": [{"npc_id": "npc:1", "valence": 0.9, "arousal": 0.0}]},
        "steps": [{"apply": [{"kind": "mood", "target": "npc:1",
                              "data": {"valence_delta": 3, "arousal_delta": 0}}],
                   "expect": [{"kind": "clamped",
                               "clamped_to": {"valence_delta": 0.05, "arousal_delta": 0.0}}]}],
        "checks": [{"expr": "True"}],
    })
    report = run_scenarios([path])
    assert not report.ok
    assert "expected clamped_to" in report.results[0].failures[0]


def test_breakage_mutated_real_scenario_fails(tmp_path):
    """Flip one real check: the shipped scenario must stop passing."""
    real = next(s for s in load_scenarios(root=str(SCENARIOS_DIR))
                if s.name == "dead-npc-lock")
    mutated = copy.deepcopy(real)
    mutated.name = "dead-npc-lock-mutated"
    mutated.checks[0]["expr"] = "npc[refs['grunn']].alive is True"  # the lie
    path = _write_json(tmp_path, "mutated", {
        "name": mutated.name, "description": mutated.description, "world": mutated.world,
        "steps": mutated.steps, "checks": mutated.checks, "options": mutated.options,
    })
    assert run_scenarios([SCENARIOS_DIR / "dead-npc-lock.py"]).ok
    report = run_scenarios([path])
    assert not report.ok
    assert any("Grunn's death stayed committed" in failure
               for failure in report.results[0].failures)


def test_breakage_scenario_without_assertions_fails(tmp_path):
    path = _write_json(tmp_path, "empty", {
        "name": "no-assertions",
        "steps": [{"update": {"table": "npcs", "id": 1, "data": {"alive": 0}}}],
    })
    report = run_scenarios([path])
    assert not report.ok
    assert any("no assertions" in failure for failure in report.results[0].failures)


def test_breakage_step_exception_aborts_with_message(tmp_path):
    path = _write_json(tmp_path, "boom", {
        "name": "boom",
        "steps": [{"resolve": {"kind": "action", "text": "swing", "dc": "very high"}}],
        "checks": [{"expr": "False", "msg": "never reached"}],
    })
    report = run_scenarios([path])
    assert not report.ok
    assert "step 1 (resolve) raised TypeError" in report.results[0].failures[0]


def test_turns_require_the_pipeline_runner(tmp_path):
    path = _write_json(tmp_path, "turns", {
        "name": "pipeline-only",
        "turns": ["I open the door"],
        "checks": [{"expr": "True"}],
    })
    report = run_scenarios([path])
    assert not report.ok
    assert any("run_e2e" in failure for failure in report.results[0].failures)


def test_run_e2e_delegates_to_the_pipeline_suite():
    # R8 replaced the stub with the pipeline-level suite (evals/e2e.py); the
    # core runner still refuses `turns` (previous test) and the facade reports
    # the e2e kind, so callers can tell the suites apart.
    report = run_e2e()
    assert report.ok, report.failure_lines()
    assert report.kind == "e2e"
    assert report.total == 5


# --------------------------------------------------------------------------- #
# CLI: python -m evals
# --------------------------------------------------------------------------- #

def _run_cli(*args: str, cwd: Path = ENGINE_ROOT) -> subprocess.CompletedProcess:
    return subprocess.run(
        [sys.executable, "-m", "evals", *args],
        cwd=cwd, capture_output=True, text=True, check=False,
    )


def test_cli_runs_the_shipped_suite(tmp_path):
    proc = _run_cli("--json", "--db-dir", str(tmp_path / "dbs"))
    assert proc.returncode == 0, proc.stderr
    payload = json.loads(proc.stdout)
    assert payload["ok"] is True and payload["total"] == 5
    assert [result["name"] for result in payload["results"]] == list(SHIPPED)
    assert (tmp_path / "dbs" / "conservation.sqlite").is_file()


def test_cli_lists_scenarios():
    proc = _run_cli("--list")
    assert proc.returncode == 0, proc.stderr
    listed = [line.split(" — ")[0] for line in proc.stdout.splitlines()]
    assert listed == list(SHIPPED)


def test_cli_exits_1_when_a_scenario_fails(tmp_path):
    path = _write_json(tmp_path, "wrong", {
        "name": "wrong-check",
        "checks": [{"expr": "store.count('npcs') == 2", "msg": "there are two npcs"}],
    })
    proc = _run_cli("--scenario", str(path), "--json")
    assert proc.returncode == 1
    payload = json.loads(proc.stdout)
    assert payload["ok"] is False
    assert "there are two npcs" in payload["results"][0]["failures"][0]


def test_cli_exits_1_on_a_broken_scenario_file(tmp_path):
    bad = tmp_path / "bad.json"
    bad.write_text("{not json", encoding="utf-8")
    proc = _run_cli("--scenario", str(bad))
    assert proc.returncode == 1
    assert "invalid JSON" in proc.stderr
