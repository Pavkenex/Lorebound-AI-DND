"""Turn orchestrator + multi-pass generation (spec §1, §5).

Contract (R7 card). ``Orchestrator.take_turn`` runs the full lifecycle:
  1. classify intent (rules-first; §1)
  2. Pass A: mechanics resolution (resolve.py) — before any model call
  3. context assembly (context.py)
  4. Pass B: narrator (live adapter or stub) -> ProposalSet
  5. Pass C: validate + commit (validate.py); memory subsystem updates
     (chronicle append, mood decay, saga maybe-summarize)
  6. Pass D: consistency check; on contradiction either regenerate Pass B
     with an injected correction note or patch the sentence — bounded retries
  7. append TurnLog + telemetry; return TurnResult
The model never adjudicates success and never writes state directly.
"""
from __future__ import annotations

from typing import Any, Protocol

from .config import EngineConfig
from .models import AssembledPrompt, CommitReport, Intent, ProposalSet, TurnResult


class NarratorRunner(Protocol):
    """Anything that turns an assembled prompt into a ProposalSet: a live
    provider adapter wrapper, the StubNarrator, or an eval fake."""

    def narrate(self, *, prompt: AssembledPrompt, turn: int,
                retry_note: str | None = None) -> ProposalSet:
        ...


class Orchestrator:
    def __init__(self, store: Any, config: EngineConfig | None = None,
                 narrator: NarratorRunner | None = None, *,
                 rng: Any = None, sim: Any = None) -> None:
        self.store = store
        self.config = config or EngineConfig()
        self.narrator = narrator
        self.rng = rng
        self.sim = sim

    def classify_intent(self, text: str) -> Intent:
        """dialogue | action | exploration | meta — rules-based baseline."""
        raise NotImplementedError("R7 card implements classify_intent")

    def take_turn(self, *, player_input: str, turn: int) -> TurnResult:
        raise NotImplementedError("R7 card implements take_turn")

    def consistency_check(self, *, turn: int, narration: str,
                          report: CommitReport) -> tuple[bool, list[str]]:
        """Pass D: returns (clean, problems). Scans narration against pinned
        facts + recently committed deltas; cheap, deterministic-first."""
        raise NotImplementedError("R7 card implements consistency_check")


def default_ruleset_text() -> str:
    """Static, cacheable system/ruleset block (spec §4 priority #1)."""
    raise NotImplementedError("R7 card implements default_ruleset_text")
