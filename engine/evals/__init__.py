"""Eval harness + scenario suites (spec §9).

Contract (R6 card, extended by R8): scripted play sessions with expected state
outcomes, run against deterministic fake narrators; contradiction-injection
rigs; cross-model (fake) matrix; telemetry checks. ``python -m engine.evals``
is the entry point (see cli).
"""

from .harness import EvalReport, EvalResult, Scenario, run_all, run_e2e, run_scenarios

__all__ = ["EvalReport", "EvalResult", "Scenario", "run_all", "run_e2e", "run_scenarios"]
