"""Eval harness + core scenario runner (spec §9).

Scenarios are plain data: ``*.json`` files holding a scenario mapping, or
``*.py`` modules defining ``SCENARIO = {...}``, under ``evals/scenarios/``.
The core suite (this card) drives the state layer only — real ``Store`` +
mechanics resolver + validator — so it is deterministic, offline, and needs no
narrator. Pipeline-level end-to-end suites are ``run_e2e`` (R8).

Scenario shape
--------------
::

    SCENARIO = {
        "name": "dead-npc-lock",            # required, unique per run
        "description": "...",
        "world": {                          # fixtures, or None/omitted
            "characters": [{"name": "Player", "stats": {...}}],
            "npcs": [{"name": "Grunn", "ref": "grunn", "alive": False}],
            "leads": [...], "world_facts": [...], "moods": [...],
        },
        "steps": [                          # ordered actions (see below)
            {"update": {"table": "npcs", "id": 1, "data": {"alive": 0}},
             "note": "the fight killed Grunn"},
            {"resolve": {"kind": "action", "target": "npc:1"},
             "expect": {"kind": "blocked"}},
            {"validate": [<delta>, ...], "expect": ["rejected"]},   # no commit
            {"apply": [<delta>, ...], "expect": [{"kind": "clamped",
                                                  "clamped_to": 0.4}]},
            {"insert": {"table": "npcs", "data": {...}, "ref": "odo"}},
            {"check": {"expr": "state['character'].stats['currency'] == 25",
                       "msg": "balance is untouched"}},
        ],
        "checks": [                         # final assertions (after all steps)
            {"expr": "npc[1].alive is False", "msg": "Grunn stays dead"},
        ],
        "options": {"seed": 7, "rng": [15], "turn": 1},
    }

Deltas are the wire form of :class:`engine.models.Delta` (``kind``, ``target``,
``data``, ``reason``). Steps:

* ``apply``    — validate the deltas, then commit the accepted/clamped ones.
* ``validate`` — validate only; nothing is written (proves exactly that).
* ``resolve``  — ``intent`` mapping -> ``resolve_action`` outcome.
* ``update``   — direct ``store.update`` (commits the game's own rulings,
                 e.g. an NPC's death, which no delta kind expresses).
* ``insert``   — direct ``store.insert`` of model-kwarg ``data``.
* ``check``    — an inline assertion evaluated immediately after the step.

``expect`` is checked per step: a bare verdict kind or a mapping with ``kind``
/ ``clamped_to`` / ``note_contains`` for ``apply``/``validate`` (one entry per
delta), or ``kind`` / ``band`` / ``roll`` / ``total`` / ``note_contains`` /
``verdict_contains`` for ``resolve``. A step without ``expect`` is not an
assertion on its own.

Check namespace
---------------
Checks are single expressions evaluated with ``eval`` (no real builtins — only
the safe helpers below; scenario files are repository code, like tests):

    session    EvalSession (store, config, validator, rng, turn, steps, refs)
    state      live state view: character/characters, npcs (by id),
               npc_by_name, leads (by id), moods, world, inventory
    npc        == state["npcs"]; npcs / npc_by_name; facts (WorldFact list),
               memories (NPCMemoryEntry list), ledger (RelationshipDelta list)
    store      the live Store;  turn  the current turn;  refs  fixture aliases
    steps      step records;  verdicts  every verdict;  outcomes  every
               resolved MechanicalOutcome;  reports  every CommitReport
    verdict_count(kind), count_rows(table, where?), resolve_action

Anything raising during a check is reported as that check failing — never as
a crashed run.

Runner contract
---------------
``run_scenarios(paths=None, *, root=None, seed=None, db_dir=None,
narrator=None) -> EvalReport``: one THROWAWAY sqlite DB per scenario (a
tempdir when ``db_dir`` is None, cleaned up on return; ``<db_dir>/<name>.sqlite``
when given). Deterministic RNG — ``options.seed`` wins, else the run seed
(default :data:`DEFAULT_SEED`); ``options.rng`` pins an exact die sequence.
Telemetry rows are collected into :attr:`EvalReport.telemetry`; each result
carries a JSON-safe snapshot (step verdicts, outcomes, counts, final state).
``run_all`` discovers ``evals/scenarios/*.json|*.py`` (subdirectories are
reserved for the e2e suites). ``narrator`` is accepted for R8 compatibility
and unused by core scenarios, which never call a model.
"""
from __future__ import annotations

import importlib.util
import json
import re
import sys
import tempfile
from dataclasses import dataclass, field, fields
from pathlib import Path
from typing import Any

from engine.config import EngineConfig
from engine.models import (
    NPC,
    Character,
    ChronicleEntry,
    Delta,
    Intent,
    Lead,
    Location,
    MoodState,
    NPCMemoryEntry,
    RelationshipDelta,
    SagaRow,
    TelemetryRow,
    TurnLog,
    World,
    WorldFact,
    from_row,
    to_row,
)
from engine.resolve import SeededRng, resolve_action
from engine.store import Store
from engine.validate import Validator

DEFAULT_SEED = 13
SCENARIOS_DIR = Path(__file__).resolve().parent / "scenarios"

# fixture table -> model (the mapping is the insert-time encoder/validator)
_FIXTURE_MODELS: dict[str, type] = {
    "world": World,
    "locations": Location,
    "characters": Character,
    "npcs": NPC,
    "leads": Lead,
    "world_facts": WorldFact,
    "turn_log": TurnLog,
    "npc_memory": NPCMemoryEntry,
    "chronicle": ChronicleEntry,
    "relationship_ledger": RelationshipDelta,
    "moods": MoodState,
    "saga_levels": SagaRow,
    "telemetry": TelemetryRow,
}

_STEP_ACTIONS = ("apply", "validate", "resolve", "update", "insert", "check")
_STEP_EXTRAS = ("note", "turn", "expect")
_TOP_LEVEL_KEYS = ("name", "description", "world", "turns", "checks", "options", "steps")
_OPTION_KEYS = ("seed", "rng", "turn")
_VERDICT_KINDS = ("accepted", "clamped", "rejected")
_SNAPSHOT_TABLES = (
    "characters", "npcs", "leads", "world_facts", "relationship_ledger", "moods",
)
_STATE_TABLES = (*_SNAPSHOT_TABLES, "locations", "world")

# Check helpers: the ONLY builtins a check expression can reach.
_CHECK_BUILTINS: dict[str, Any] = {
    "abs": abs, "all": all, "any": any, "bool": bool, "dict": dict,
    "enumerate": enumerate, "float": float, "int": int, "isinstance": isinstance,
    "len": len, "list": list, "max": max, "min": min, "range": range,
    "repr": repr, "round": round, "set": set, "sorted": sorted, "str": str,
    "sum": sum, "tuple": tuple, "zip": zip,
}


class _ScenarioError(Exception):
    """Fatal, scenario-level problem: abort the scenario with this message."""


# --------------------------------------------------------------------------- #
# Scenario model + loading
# --------------------------------------------------------------------------- #

@dataclass
class Scenario:
    """One scripted eval scenario (loaded from json or a py ``SCENARIO`` dict)."""

    name: str = ""
    description: str = ""
    world: dict | None = None
    turns: list = field(default_factory=list)  # player inputs — pipeline (R8) only
    checks: list = field(default_factory=list)
    options: dict = field(default_factory=dict)
    steps: list = field(default_factory=list)  # the core-step DSL
    source: str = ""

    @classmethod
    def from_mapping(cls, mapping: Any, *, source: str = "") -> Scenario:
        """Build + validate a scenario from its mapping form (raises ValueError)."""
        if not isinstance(mapping, dict):
            raise ValueError(f"{source}: scenario must be a mapping, got {type(mapping).__name__}")
        unknown = [key for key in mapping if key not in _TOP_LEVEL_KEYS]
        if unknown:
            raise ValueError(
                f"{source}: unknown scenario key(s) {sorted(unknown)}; "
                f"allowed: {list(_TOP_LEVEL_KEYS)}"
            )
        name = str(mapping.get("name") or "").strip()
        if not name:
            raise ValueError(f"{source}: scenario needs a non-empty 'name'")

        world = mapping.get("world")
        if world is not None:
            _validate_world(world, where=f"{source}: world")
        turns = mapping.get("turns") or []
        if not isinstance(turns, list):
            raise ValueError(f"{source}: 'turns' must be a list")
        steps = mapping.get("steps") or []
        if not isinstance(steps, list):
            raise ValueError(f"{source}: 'steps' must be a list")
        for index, step in enumerate(steps, start=1):
            _validate_step(step, where=f"{source}: step {index}")
        checks = mapping.get("checks") or []
        if not isinstance(checks, list):
            raise ValueError(f"{source}: 'checks' must be a list")
        for index, check in enumerate(checks, start=1):
            _validate_check(check, where=f"{source}: check {index}")
        options = mapping.get("options") or {}
        if not isinstance(options, dict):
            raise ValueError(f"{source}: 'options' must be a mapping")
        unknown_options = [key for key in options if key not in _OPTION_KEYS]
        if unknown_options:
            raise ValueError(
                f"{source}: unknown option(s) {sorted(unknown_options)}; "
                f"allowed: {list(_OPTION_KEYS)}"
            )
        if "rng" in options and not all(
            isinstance(value, int) and not isinstance(value, bool) for value in options["rng"]
        ):
            raise ValueError(f"{source}: option 'rng' must be a list of integers")

        return cls(
            name=name,
            description=str(mapping.get("description") or ""),
            world=world,
            turns=list(turns),
            checks=list(checks),
            options=dict(options),
            steps=list(steps),
            source=source,
        )


def _validate_world(world: Any, *, where: str) -> None:
    if not isinstance(world, dict):
        raise ValueError(f"{where}: must be a mapping of table -> [rows]")
    for table, entries in world.items():
        if table not in _FIXTURE_MODELS:
            raise ValueError(
                f"{where}: unknown table {table!r}; known: {sorted(_FIXTURE_MODELS)}"
            )
        if not isinstance(entries, list):
            raise ValueError(f"{where}: {table} must be a list of rows")
        for index, entry in enumerate(entries, start=1):
            _validate_row(table, entry, where=f"{where}: {table}[{index}]", allow_ref=True)


def _validate_row(table: str, entry: Any, *, where: str, allow_ref: bool) -> None:
    if not isinstance(entry, dict):
        raise ValueError(f"{where}: row must be a mapping of model kwargs")
    known = {f.name for f in fields(_FIXTURE_MODELS[table])} | ({"ref"} if allow_ref else set())
    unknown = [key for key in entry if key not in known]
    if unknown:
        raise ValueError(
            f"{where}: unknown field(s) {sorted(unknown)} for table {table!r}; "
            f"known: {sorted(known)}"
        )


def _validate_check(check: Any, *, where: str) -> None:
    if not isinstance(check, dict):
        raise ValueError(f"{where}: check must be a mapping with 'expr' (and optional 'msg')")
    unknown = [key for key in check if key not in ("expr", "msg")]
    if unknown:
        raise ValueError(f"{where}: unknown check key(s) {sorted(unknown)}; allowed: ['expr', 'msg']")
    if not str(check.get("expr") or "").strip():
        raise ValueError(f"{where}: check needs a non-empty 'expr'")


def _validate_step(step: Any, *, where: str) -> None:
    if not isinstance(step, dict):
        raise ValueError(f"{where}: step must be a mapping")
    unknown = [key for key in step if key not in _STEP_ACTIONS and key not in _STEP_EXTRAS]
    if unknown:
        raise ValueError(
            f"{where}: unknown step key(s) {sorted(unknown)}; "
            f"allowed: {[*_STEP_ACTIONS, *_STEP_EXTRAS]}"
        )
    actions = [key for key in step if key in _STEP_ACTIONS]
    if len(actions) != 1:
        raise ValueError(f"{where}: exactly one of {list(_STEP_ACTIONS)} is required")
    action = actions[0]
    value = step[action]
    if action in ("apply", "validate"):
        if not isinstance(value, list) or not value:
            raise ValueError(f"{where}: {action} needs a non-empty list of deltas")
        for index, delta in enumerate(value, start=1):
            if not isinstance(delta, dict) or not str(delta.get("kind") or "").strip():
                raise ValueError(f"{where}: {action} delta {index} needs a 'kind'")
            if "data" in delta and not isinstance(delta["data"], dict):
                raise ValueError(f"{where}: {action} delta {index} 'data' must be a mapping")
    elif action == "resolve":
        if not isinstance(value, dict):
            raise ValueError(f"{where}: resolve needs a mapping of Intent kwargs")
        unknown_resolve = [key for key in value if key not in ("kind", "text", "skill", "target", "dc")]
        if unknown_resolve:
            raise ValueError(
                f"{where}: unknown resolve key(s) {sorted(unknown_resolve)}; "
                "allowed: ['dc', 'kind', 'skill', 'target', 'text']"
            )
    elif action == "update":
        if not isinstance(value, dict) or "id" not in value or "data" not in value:
            raise ValueError(f"{where}: update needs {{'table', 'id', 'data'}}")
        if str(value.get("table") or "") not in _FIXTURE_MODELS:
            raise ValueError(f"{where}: update table {value.get('table')!r} is unknown")
        if not isinstance(value["data"], dict) or not value["data"]:
            raise ValueError(f"{where}: update 'data' must be a non-empty mapping")
        _validate_row(value["table"], value["data"], where=f"{where}: update", allow_ref=False)
    elif action == "insert":
        if not isinstance(value, dict) or "data" not in value:
            raise ValueError(f"{where}: insert needs {{'table', 'data'}}")
        if str(value.get("table") or "") not in _FIXTURE_MODELS:
            raise ValueError(f"{where}: insert table {value.get('table')!r} is unknown")
        _validate_row(value["table"], value["data"], where=f"{where}: insert", allow_ref=False)
    elif action == "check":
        _validate_check(value, where=f"{where}: step check")
    if "turn" in step and not isinstance(step["turn"], int):
        raise ValueError(f"{where}: 'turn' must be an integer")


def _scenario_files(paths: list | None, root: str | None) -> list[Path]:
    """Resolve scenario files: explicit paths (files/dirs) or a root directory."""
    dirs: list[Path] = []
    files: list[Path] = []
    if paths is None:
        dirs.append(Path(root) if root is not None else SCENARIOS_DIR)
    else:
        for raw in paths:
            path = Path(raw)
            if path.is_dir():
                dirs.append(path)
            elif path.is_file():
                files.append(path)
            else:
                raise ValueError(f"scenario path does not exist: {path}")
    found: list[Path] = []
    for directory in dirs:
        if not directory.is_dir():
            raise ValueError(f"scenario directory does not exist: {directory}")
        found.extend(sorted((*directory.glob("*.json"), *directory.glob("*.py"))))
    found.extend(files)
    found = [path for path in found if not path.name.startswith("_")]
    if not found:
        raise ValueError(f"no scenario files found under {dirs or files}")
    return found


def _read_scenario_mapping(path: Path) -> Any:
    if path.suffix == ".json":
        try:
            return json.loads(path.read_text(encoding="utf-8"))
        except json.JSONDecodeError as exc:
            raise ValueError(f"{path}: invalid JSON ({exc})") from exc
    if path.suffix == ".py":
        name = f"_evals_scenario_{path.stem}_{abs(id(path))}"
        spec = importlib.util.spec_from_file_location(name, path)
        if spec is None or spec.loader is None:  # pragma: no cover - defensive
            raise ValueError(f"{path}: cannot be imported as a python module")
        module = importlib.util.module_from_spec(spec)
        sys.modules[name] = module
        spec.loader.exec_module(module)
        mapping = getattr(module, "SCENARIO", None)
        if mapping is None:
            raise ValueError(f"{path}: module does not define SCENARIO")
        return mapping
    raise ValueError(f"{path}: unsupported scenario file type {path.suffix!r} (use .json or .py)")


def load_scenarios(paths: list | None = None, *, root: str | None = None) -> list[Scenario]:
    """Load + validate every scenario file (explicit ``paths``, else ``root``)."""
    scenarios = [
        Scenario.from_mapping(_read_scenario_mapping(path), source=str(path))
        for path in _scenario_files(paths, root)
    ]
    seen: dict[str, str] = {}
    for scenario in scenarios:
        if scenario.name in seen:
            raise ValueError(
                f"duplicate scenario name {scenario.name!r}: {seen[scenario.name]} and {scenario.source}"
            )
        seen[scenario.name] = scenario.source
    return scenarios


# --------------------------------------------------------------------------- #
# Results
# --------------------------------------------------------------------------- #

@dataclass
class EvalResult:
    """Outcome of one scenario: pass/fail, failure lines, JSON-safe snapshot."""

    name: str = ""
    passed: bool = False
    failures: list = field(default_factory=list)
    snapshot: dict = field(default_factory=dict)


@dataclass
class EvalReport:
    """Aggregated run: per-scenario results + telemetry + pass/fail totals."""

    kind: str = "core"
    seed: Any = None
    db_dir: str | None = None
    total: int = 0
    passed: int = 0
    failed: int = 0
    results: list = field(default_factory=list)  # list[EvalResult]
    telemetry: list = field(default_factory=list)

    @property
    def ok(self) -> bool:
        return self.failed == 0 and self.total > 0

    def failure_lines(self) -> list[str]:
        lines: list[str] = []
        for result in self.results:
            lines.extend(f"{result.name}: {failure}" for failure in result.failures)
        if self.total == 0:
            lines.append("no scenarios ran")
        return lines

    def render(self) -> str:
        header = (
            f"evals [{self.kind}] seed={self.seed}: "
            f"{self.passed}/{self.total} scenario(s) passed"
        )
        lines = [header]
        for result in self.results:
            lines.append(f"  {'PASS' if result.passed else 'FAIL'} {result.name}")
            lines.extend(f"      - {failure}" for failure in result.failures)
        lines.append("RESULT: OK" if self.ok else "RESULT: FAIL")
        return "\n".join(lines)

    def to_dict(self) -> dict:
        return {
            "kind": self.kind,
            "seed": self.seed,
            "total": self.total,
            "passed": self.passed,
            "failed": self.failed,
            "ok": self.ok,
            "results": [
                {"name": r.name, "passed": r.passed, "failures": list(r.failures),
                 "snapshot": r.snapshot}
                for r in self.results
            ],
            "telemetry": list(self.telemetry),
        }


# --------------------------------------------------------------------------- #
# Run state
# --------------------------------------------------------------------------- #

@dataclass
class EvalSession:
    """Everything a scenario run exposes to its checks (see module docstring)."""

    scenario: Scenario
    seed: Any
    db_path: str
    store: Any = None
    config: Any = None
    validator: Any = None
    rng: Any = None
    turn: int = 1
    steps: list = field(default_factory=list)      # step records, in order
    verdicts: list = field(default_factory=list)   # every Verdict, in order
    outcomes: list = field(default_factory=list)   # every MechanicalOutcome
    reports: list = field(default_factory=list)    # every CommitReport
    refs: dict = field(default_factory=dict)       # fixture alias -> row id
    failures: list = field(default_factory=list)
    telemetry: list = field(default_factory=list)


class _ScriptedRng:
    """Fixed-sequence RNGSource (``options.rng``) for exact dice in scenarios."""

    def __init__(self, values: list[int]) -> None:
        self._values = list(values)
        self.calls: list[tuple[int, int]] = []

    def randint(self, a: int, b: int) -> int:
        self.calls.append((a, b))
        if not self._values:
            raise AssertionError(f"scripted rng exhausted after {len(self.calls)} draw(s)")
        value = self._values.pop(0)
        if not a <= value <= b:
            raise AssertionError(f"scripted rng value {value} outside [{a}, {b}]")
        return value


# --------------------------------------------------------------------------- #
# Runner
# --------------------------------------------------------------------------- #

def run_scenarios(paths: list | None = None, *, root: str | None = None,
                  seed: Any = None, db_dir: str | None = None,
                  narrator: Any = None) -> EvalReport:
    """Run each scenario in its own throwaway DB; aggregate into an EvalReport.

    ``narrator`` is unused by core scenarios (they never call a model) and is
    accepted so R8's pipeline suites keep one call shape.
    """
    _ = narrator
    scenarios = load_scenarios(paths, root=root)
    run_seed = DEFAULT_SEED if seed is None else seed
    owner: tempfile.TemporaryDirectory | None = None
    if db_dir is None:
        owner = tempfile.TemporaryDirectory(prefix="lorebound-evals-")
        directory = Path(owner.name)
    else:
        directory = Path(db_dir)
        directory.mkdir(parents=True, exist_ok=True)
    report = EvalReport(kind="core", seed=run_seed, db_dir=str(directory))
    try:
        for scenario in scenarios:
            result, telemetry = _run_scenario(
                scenario, run_seed=run_seed, directory=directory)
            report.results.append(result)
            report.telemetry.extend(telemetry)
    finally:
        if owner is not None:
            owner.cleanup()
    report.total = len(report.results)
    report.passed = sum(1 for result in report.results if result.passed)
    report.failed = report.total - report.passed
    return report


def run_all(*, root: str | None = None, **kwargs: Any) -> EvalReport:
    """Discover and run every scenario under ``root`` (default ``evals/scenarios/``)."""
    return run_scenarios(None, root=root, **kwargs)


def run_e2e(*, root: str | None = None, **kwargs: Any) -> EvalReport:
    """Pipeline-level end-to-end suites (implemented by R8)."""
    _ = root, kwargs
    raise NotImplementedError("R8 card implements run_e2e (pipeline-level suites)")


def _slug(name: str) -> str:
    slug = re.sub(r"[^A-Za-z0-9_.-]+", "-", name).strip("-")
    return slug or "scenario"


def _run_scenario(scenario: Scenario, *, run_seed: Any, directory: Path,
                  ) -> tuple[EvalResult, list]:
    effective_seed = scenario.options.get("seed", run_seed)
    db_path = directory / f"{_slug(scenario.name)}.sqlite"
    result = EvalResult(name=scenario.name)
    telemetry: list = []
    session = EvalSession(scenario=scenario, seed=effective_seed, db_path=str(db_path))
    session.turn = int(scenario.options.get("turn", 1))

    if scenario.turns:
        result.failures.append(
            "scenario defines player-input 'turns'; that needs the pipeline runner "
            "(run_e2e — R8), which core scenarios cannot exercise"
        )
        result.snapshot = _snapshot(session, db_path=db_path, telemetry=telemetry)
        return result, telemetry

    assertions = _assertion_count(scenario)
    if assertions == 0:
        session.failures.append(
            "scenario defines no assertions (checks, inline checks, or step expectations)")
    session.telemetry = telemetry

    store = Store(db_path)
    session.store = store
    session.config = EngineConfig()
    session.validator = Validator(store, session.config)
    session.rng = _make_rng(scenario.options, effective_seed)
    try:
        try:
            for table, entries in (scenario.world or {}).items():
                for entry in entries:
                    _insert_row(store, session, table, entry)
            for index, step in enumerate(scenario.steps, start=1):
                _run_step(session, step, index)
                _append_step_telemetry(session, index, step)
            for index, check in enumerate(scenario.checks, start=1):
                _eval_check(session, check, f"check {index}", None)
        except _ScenarioError as exc:
            session.failures.append(str(exc))
        telemetry.append({
            "scenario": scenario.name,
            "step": None,
            "kind": "checks",
            "checks": len(scenario.checks),
            "assertions": assertions,
            "failures": len(session.failures),
        })
        result.failures = list(session.failures)
        result.passed = not result.failures
        result.snapshot = _snapshot(session, db_path=db_path, telemetry=telemetry)
    finally:
        store.close()
    return result, telemetry


def _assertion_count(scenario: Scenario) -> int:
    total = len(scenario.checks)
    for step in scenario.steps:
        if "check" in step:
            total += 1
        if "expect" in step:
            total += 1
    return total


def _make_rng(options: dict, seed: Any) -> Any:
    scripted = options.get("rng")
    return _ScriptedRng(scripted) if scripted else SeededRng(seed)


# --------------------------------------------------------------------------- #
# Steps
# --------------------------------------------------------------------------- #

def _run_step(session: EvalSession, step: dict, index: int) -> None:
    kind = next(key for key in step if key in _STEP_ACTIONS)
    turn = int(step.get("turn", session.turn))
    record: dict = {
        "index": index,
        "kind": kind,
        "turn": turn,
        "note": str(step.get("note") or ""),
        "failures": [],
        "verdicts": [],
        "outcome": None,
        "report": None,
    }
    session.steps.append(record)
    try:
        if kind in ("apply", "validate"):
            deltas = [_delta_from_mapping(mapping) for mapping in step[kind]]
            verdicts = session.validator.validate(deltas, turn=turn)
            record["verdicts"] = list(verdicts)
            session.verdicts.extend(verdicts)
            if kind == "apply":
                record["report"] = session.validator.commit(verdicts, turn=turn)
                session.reports.append(record["report"])
            _check_verdict_expectations(session, record, step.get("expect"), index)
        elif kind == "resolve":
            spec = dict(step["resolve"])
            intent = Intent(
                kind=str(spec.get("kind") or "action"),
                text=str(spec.get("text") or ""),
                skill=spec.get("skill"),
                target=spec.get("target"),
            )
            outcome = resolve_action(
                intent, rng=session.rng, store=session.store, turn=turn,
                dc=spec.get("dc"),
            )
            record["outcome"] = outcome
            session.outcomes.append(outcome)
            _check_outcome_expectations(session, record, step.get("expect"), index)
        elif kind == "update":
            spec = step["update"]
            updated = session.store.update(
                str(spec["table"]), int(spec["id"]), dict(spec["data"]))
            record["updated"] = updated
            expect = step.get("expect") or {}
            if not updated and "updated" not in expect:
                # An explicit {"updated": False} expectation makes a no-op
                # update a legitimate assertion; a silent miss is a failure.
                _fail(session, record, f"step {index} (update): no {spec['table']}"
                                       f" row with id {spec['id']}")
            _check_value_expectations(session, record, step.get("expect"), index)
        elif kind == "insert":
            spec = step["insert"]
            record["row_id"] = _insert_row(session.store, session, str(spec["table"]),
                                           dict(spec["data"]), ref=spec.get("ref"))
        elif kind == "check":
            _eval_check(session, step["check"], f"step {index} (check)", record)
    except _ScenarioError:
        raise
    except Exception as exc:
        raise _ScenarioError(
            f"step {index} ({kind}) raised {type(exc).__name__}: {exc}") from exc
    finally:
        if turn >= session.turn:
            session.turn = turn + 1


def _delta_from_mapping(mapping: dict) -> Delta:
    kind = str(mapping.get("kind") or "").strip()
    if not kind:
        raise ValueError("delta is missing 'kind'")
    return Delta(
        kind=kind,
        target=str(mapping.get("target") or ""),
        data=dict(mapping.get("data") or {}),
        reason=str(mapping.get("reason") or ""),
    )


def _insert_row(store: Any, session: EvalSession, table: str, entry: dict,
                *, ref: Any = None) -> int:
    model = _FIXTURE_MODELS.get(table)
    if model is None:
        raise ValueError(f"unknown table {table!r}; known: {sorted(_FIXTURE_MODELS)}")
    values = dict(entry)
    ref = values.pop("ref", None) if ref is None else ref
    row_id = store.insert(table, to_row(model(**values)))
    if ref is not None:
        name = str(ref)
        if name in session.refs:
            raise ValueError(f"duplicate ref {name!r} (already bound to row {session.refs[name]})")
        session.refs[name] = row_id
    return row_id


# --------------------------------------------------------------------------- #
# Expectations
# --------------------------------------------------------------------------- #

def _fail(session: EvalSession, record: dict, message: str) -> None:
    record["failures"].append(message)
    session.failures.append(message)


def _check_verdict_expectations(session: EvalSession, record: dict, expect: Any,
                                index: int) -> None:
    if expect is None:
        return
    if not isinstance(expect, list):
        _fail(session, record, f"step {index} ({record['kind']}): 'expect' must be a list")
        return
    verdicts = record["verdicts"]
    if len(expect) != len(verdicts):
        _fail(session, record,
              f"step {index} ({record['kind']}): expected {len(expect)} verdict(s), "
              f"got {len(verdicts)}")
    for position, (want, verdict) in enumerate(zip(expect, verdicts, strict=False), start=1):
        for failure in _match_verdict(
                want, verdict, f"step {index} ({record['kind']}) verdict {position}"):
            _fail(session, record, failure)


def _match_verdict(want: Any, verdict: Any, label: str) -> list[str]:
    if isinstance(want, str):
        want = {"kind": want}
    if not isinstance(want, dict):
        return [f"{label}: expectation must be a verdict kind or mapping"]
    failures: list[str] = []
    if "kind" in want and verdict.kind != want["kind"]:
        failures.append(
            f"{label}: expected kind {want['kind']!r}, got {verdict.kind!r} ({verdict.note})")
    if "clamped_to" in want and not _same(want["clamped_to"], verdict.clamped_to):
        failures.append(
            f"{label}: expected clamped_to {want['clamped_to']!r}, "
            f"got {verdict.clamped_to!r} ({verdict.note})")
    if "note_contains" in want and str(want["note_contains"]) not in (verdict.note or ""):
        failures.append(
            f"{label}: note {verdict.note!r} does not contain {want['note_contains']!r}")
    return failures


def _check_outcome_expectations(session: EvalSession, record: dict, expect: Any,
                                index: int) -> None:
    if expect is None:
        return
    if not isinstance(expect, dict):
        _fail(session, record, f"step {index} (resolve): 'expect' must be a mapping")
        return
    outcome = record["outcome"]
    check = outcome.check
    label = f"step {index} (resolve)"
    failures: list[str] = []
    if "kind" in expect and outcome.kind != expect["kind"]:
        failures.append(f"{label}: expected kind {expect['kind']!r}, got {outcome.kind!r}")
    if "band" in expect and (check is None or check.band != expect["band"]):
        failures.append(
            f"{label}: expected band {expect['band']!r}, got "
            f"{check.band if check else None!r} (no check was rolled)")
    if "roll" in expect and (check is None or check.roll != expect["roll"]):
        failures.append(f"{label}: expected roll {expect['roll']!r}, got "
                        f"{check.roll if check else None!r}")
    if "total" in expect and (check is None or check.total != expect["total"]):
        failures.append(f"{label}: expected total {expect['total']!r}, got "
                        f"{check.total if check else None!r}")
    if "note_contains" in expect and not any(
            str(expect["note_contains"]) in note for note in outcome.notes):
        failures.append(f"{label}: notes {outcome.notes!r} do not contain "
                        f"{expect['note_contains']!r}")
    if "verdict_contains" in expect and str(expect["verdict_contains"]) not in outcome.verdict_line:
        failures.append(f"{label}: verdict line {outcome.verdict_line!r} does not contain "
                        f"{expect['verdict_contains']!r}")
    for failure in failures:
        _fail(session, record, failure)


def _check_value_expectations(session: EvalSession, record: dict, expect: Any,
                              index: int) -> None:
    """``update`` step: ``expect`` may carry ``{'updated': bool}``."""
    if expect is None:
        return
    if isinstance(expect, dict) and "updated" in expect:
        if bool(expect["updated"]) != bool(record.get("updated")):
            _fail(session, record,
                  f"step {index} (update): expected updated={expect['updated']!r}, "
                  f"got {record.get('updated')!r}")
        return
    _fail(session, record, f"step {index} (update): 'expect' must be {{'updated': bool}}")


def _same(a: Any, b: Any) -> bool:
    """Equality with a 1e-9 tolerance for numbers (clamp values are round-6)."""
    if isinstance(a, bool) or isinstance(b, bool):
        return a is b
    if isinstance(a, (int, float)) and isinstance(b, (int, float)):
        return abs(float(a) - float(b)) <= 1e-9
    if isinstance(a, dict) and isinstance(b, dict):
        return set(a) == set(b) and all(_same(a[key], b[key]) for key in a)
    if isinstance(a, (list, tuple)) and isinstance(b, (list, tuple)):
        return len(a) == len(b) and all(_same(x, y) for x, y in zip(a, b, strict=False))
    return a == b


# --------------------------------------------------------------------------- #
# Checks: namespace + evaluation
# --------------------------------------------------------------------------- #

def _eval_check(session: EvalSession, check: dict, label: str, record: dict | None) -> bool:
    """Evaluate one check expression. Failures never raise."""
    expr = str(check.get("expr") or "").strip()
    message = str(check.get("msg") or expr)
    try:
        value = eval(expr, {"__builtins__": {}, **_CHECK_BUILTINS},  # noqa: B307
                     _namespace(session))
    except Exception as exc:
        _report_check_failure(session, record, f"{label} ({message}) raised "
                                               f"{type(exc).__name__}: {exc}")
        return False
    if not value:
        _report_check_failure(session, record, f"{label} failed: {message}")
        return False
    return True


def _report_check_failure(session: EvalSession, record: dict | None, message: str) -> None:
    if record is not None:
        record["failures"].append(message)
    session.failures.append(message)


def _namespace(session: EvalSession) -> dict:
    store = session.store
    state = _state_view(store)
    return {
        "session": session,
        "store": store,
        "config": session.config,
        "validator": session.validator,
        "rng": session.rng,
        "turn": session.turn,
        "refs": dict(session.refs),
        "steps": session.steps,
        "verdicts": session.verdicts,
        "outcomes": session.outcomes,
        "reports": session.reports,
        "state": state,
        "npc": state["npcs"],
        "npcs": state["npcs"],
        "npc_by_name": state["npc_by_name"],
        "facts": state["facts"],
        "memories": [from_row(NPCMemoryEntry, row)
                     for row in store.find("npc_memory", order_by="id")],
        "ledger": [from_row(RelationshipDelta, row)
                   for row in store.find("relationship_ledger", order_by="id")],
        "verdict_count": lambda kind: sum(1 for v in session.verdicts if v.kind == kind),
        "count_rows": lambda table, where=None: store.count(table, where),
        "resolve_action": resolve_action,
    }


def _state_view(store: Any) -> dict:
    """Decoded snapshot of game state for checks (single-player campaign)."""
    characters = [from_row(Character, row) for row in store.find("characters", order_by="id")]
    npc_objs = [from_row(NPC, row) for row in store.find("npcs", order_by="id")]
    npcs = {npc.id: npc for npc in npc_objs}
    world_row = store.find_one("world", order_by="id")
    return {
        "world": from_row(World, world_row) if world_row else None,
        "characters": characters,
        "character": characters[0] if characters else None,
        "npcs": npcs,
        "npc_by_name": {npc.name: npc for npc in npc_objs},
        "leads": {row["id"]: from_row(Lead, row) for row in store.find("leads", order_by="id")},
        "moods": {row["npc_id"]: row for row in store.find("moods", order_by="id")},
        "facts": [from_row(WorldFact, row) for row in store.find("world_facts", order_by="id")],
        "inventory": characters[0].inventory if characters else [],
    }


# --------------------------------------------------------------------------- #
# Snapshots + telemetry
# --------------------------------------------------------------------------- #

def _verdict_counts(verdicts: list) -> dict:
    counts = dict.fromkeys(_VERDICT_KINDS, 0)
    for verdict in verdicts:
        counts[verdict.kind] = counts.get(verdict.kind, 0) + 1
    return counts


def _verdict_snapshot(verdict: Any) -> dict:
    return {"kind": verdict.kind, "note": verdict.note, "clamped_to": verdict.clamped_to}


def _outcome_snapshot(outcome: Any) -> dict:
    check = outcome.check
    return {
        "kind": outcome.kind,
        "label": outcome.label,
        "band": check.band if check else None,
        "roll": check.roll if check else None,
        "total": check.total if check else None,
        "verdict_line": outcome.verdict_line,
    }


def _append_step_telemetry(session: EvalSession, index: int, step: dict) -> None:
    record = session.steps[-1]
    outcome = record.get("outcome")
    session.telemetry.append({
        "scenario": session.scenario.name,
        "step": index,
        "kind": record["kind"],
        "turn": record["turn"],
        "verdicts": _verdict_counts(record.get("verdicts") or []),
        "outcome_kind": outcome.kind if outcome is not None else None,
        "ok": not record["failures"],
        "note": record["note"],
    })


def _snapshot(session: EvalSession, *, db_path: Path, telemetry: list) -> dict:
    store = session.store
    final_state = {
        table: store.find(table, order_by="id") for table in _SNAPSHOT_TABLES
    } if store is not None else {}
    return {
        "seed": session.seed,
        "db": str(db_path),
        "turn": session.turn,
        "refs": dict(session.refs),
        "steps": [
            {
                "index": record["index"],
                "kind": record["kind"],
                "turn": record["turn"],
                "ok": not record["failures"],
                "failures": list(record["failures"]),
                "verdicts": [_verdict_snapshot(v) for v in record.get("verdicts") or []],
                "outcome": (_outcome_snapshot(record["outcome"])
                            if record.get("outcome") is not None else None),
            }
            for record in session.steps
        ],
        "verdicts": _verdict_counts(session.verdicts),
        "outcomes": [_outcome_snapshot(outcome) for outcome in session.outcomes],
        "checks": len(session.scenario.checks),
        "assertions": _assertion_count(session.scenario),
        "telemetry_rows": len(telemetry),
        "final_state": final_state,
    }
