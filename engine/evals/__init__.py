"""Eval harness + scenario suites (spec §9).

Two suites, one entry point (``python -m evals`` from the ``engine/`` directory):

* **core** (``evals/scenarios/*.py``) — scripted scenarios that drive the real
  state layer — ``engine.store.Store`` + ``engine.resolve`` + ``engine.validate``
  — in throwaway SQLite DBs and assert exact state outcomes.
* **e2e** (``evals/scenarios/e2e/*.py``) — pipeline-level suites that drive the
  real turn loop (``engine.pipeline.Orchestrator.take_turn``) with scripted
  fake narrators (stub / native-tools / degraded transports): contradiction
  baits, death permanence, memory retrieval, the model matrix and the context
  budget. See :mod:`engine.evals.e2e`.
"""

import sys
from pathlib import Path

# ``python -m evals`` runs from engine/ without the pyproject pythonpath, so
# make the real engine package importable before harness.py imports it.
_SRC = Path(__file__).resolve().parents[1] / "src"
if str(_SRC) not in sys.path:
    sys.path.insert(0, str(_SRC))

from .e2e import (  # noqa: E402
    E2E_SCENARIOS_DIR,
    E2EScenario,
    load_e2e_scenarios,
    run_e2e_suite,
)
from .harness import (  # noqa: E402
    DEFAULT_SEED,
    SCENARIOS_DIR,
    EvalReport,
    EvalResult,
    EvalSession,
    Scenario,
    load_scenarios,
    run_all,
    run_e2e,
    run_scenarios,
)

__all__ = [
    "DEFAULT_SEED",
    "E2E_SCENARIOS_DIR",
    "SCENARIOS_DIR",
    "E2EScenario",
    "EvalReport",
    "EvalResult",
    "EvalSession",
    "Scenario",
    "load_e2e_scenarios",
    "load_scenarios",
    "run_all",
    "run_e2e",
    "run_e2e_suite",
    "run_scenarios",
]
