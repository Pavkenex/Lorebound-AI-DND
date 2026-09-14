"""Eval harness + scenario suites (spec §9).

Core suite (this card): scripted scenarios that drive the real state layer —
``engine.store.Store`` + ``engine.resolve`` + ``engine.validate`` — in
throwaway SQLite DBs and assert exact state outcomes. Pipeline-level e2e
suites (fake narrators, contradiction injection, cross-model matrix) are R8's
``run_e2e``. Entry point: ``python -m evals`` from the ``engine/`` directory.
"""

import sys
from pathlib import Path

# ``python -m evals`` runs from engine/ without the pyproject pythonpath, so
# make the real engine package importable before harness.py imports it.
_SRC = Path(__file__).resolve().parents[1] / "src"
if str(_SRC) not in sys.path:
    sys.path.insert(0, str(_SRC))

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
    "SCENARIOS_DIR",
    "EvalReport",
    "EvalResult",
    "EvalSession",
    "Scenario",
    "load_scenarios",
    "run_all",
    "run_e2e",
    "run_scenarios",
]
