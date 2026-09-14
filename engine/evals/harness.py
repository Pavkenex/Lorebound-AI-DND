"""Scenario DSL + runner (spec §9).

Scenario shape (JSON or Python dict):
    {
      "name": "kill-then-silence",
      "description": "...",
      "world": {...fixture or null for demo...},
      "turns": ["I attack the bandit", ...]        # player inputs
      "checks": [                                   # assertions on state
        {"expr": "npc['bandit'].alive == False", "msg": "bandit died"},
        ...
      ],
      "options": {"seed": 7, "narrator": "scripted:..."}
    }

Checks are evaluated with ``eval`` against a controlled namespace:
``session`` (PlaySession), ``state`` (state_view dict), ``store``, ``facts``,
``memories``, ``ledger``, ``turn``. Keep the mini-language boringly simple.
Scenario files: any ``*.json`` / ``*.py`` (``SCENARIO = {...}``) under
``evals/scenarios/``.

Runner contract:
- ``run_scenarios(paths, *, seed=None, db_dir=None, narrator=None) -> EvalReport``
  runs each scenario in a THROWAWAY sqlite DB (default: tempdir), asserts the
  checks, collects telemetry rows, never touches the network.
- ``run_all(root=None)`` discovers and runs every scenario file.
- ``run_e2e(root=None)`` runs the pipeline-level suites (R8).
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Optional


@dataclass
class Scenario:
    name: str = ""
    description: str = ""
    world: dict | None = None
    turns: list = field(default_factory=list)
    checks: list = field(default_factory=list)
    options: dict = field(default_factory=dict)


@dataclass
class EvalResult:
    name: str = ""
    passed: bool = False
    failures: list = field(default_factory=list)
    snapshot: dict = field(default_factory=dict)


@dataclass
class EvalReport:
    total: int = 0
    passed: int = 0
    failed: int = 0
    results: list = field(default_factory=list)  # list[EvalResult]
    telemetry: list = field(default_factory=list)

    @property
    def ok(self) -> bool:
        return self.failed == 0 and self.total > 0


def load_scenarios(paths: list | None = None, *, root: str | None = None) -> list[Scenario]:
    raise NotImplementedError("R6 card implements load_scenarios")


def run_scenarios(paths: list | None = None, *, seed: Any = None,
                  db_dir: str | None = None, narrator: Any = None) -> EvalReport:
    raise NotImplementedError("R6 card implements run_scenarios")


def run_all(*, root: str | None = None, **kwargs: Any) -> EvalReport:
    raise NotImplementedError("R6 card implements run_all")


def run_e2e(*, root: str | None = None, **kwargs: Any) -> EvalReport:
    """Pipeline-level end-to-end suites (implemented by R8)."""
    raise NotImplementedError("R8 card implements run_e2e")


_ = Optional
