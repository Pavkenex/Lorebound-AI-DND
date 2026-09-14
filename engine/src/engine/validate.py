"""State validator — the only writer of model-proposed state (spec §6).

Contract (R2 card):
- ``LEAD_TRANSITIONS`` is THE legal lead-stage machine. Allowed transitions:
    unheard -> rumored | abandoned
    rumored -> accepted | abandoned
    accepted -> in_progress | abandoned | failed
    in_progress -> complicated | resolved | failed | abandoned
    complicated -> in_progress | resolved | failed | abandoned
    resolved / failed / abandoned -> (terminal)
  No skipping: unheard -> resolved is ILLEGAL.
- ``Validator.validate(deltas, turn=...)`` returns a Verdict per delta:
    accepted | clamped (with clamped_to) | rejected (with note).
  Rules: kind must be known + required keys present; relationship category
  known, per-event |delta| clamped to config; mood deltas clamped to [-1,1]
  per event and total [-1,1]; currency conservation vs store (reject if
  insufficient); inventory_remove conservation; hp/stat range clamps;
  fact rate limit per turn (config.memory.fact_rate_limit_per_turn);
  lead transitions against the machine + store's current stage; dead NPCs
  (alive == 0) cannot receive mood/relationship/fact deltas sourced from them.
- ``Validator.commit(verdicts, turn=...)`` applies accepted+clamped deltas to
  the store (relationship -> ledger append; mood -> moods upsert; fact ->
  world_facts insert with contradiction check; lead -> stage update + history).
  Returns CommitReport. Rejected deltas are NEVER written.
- ``check_contradiction(statement)``: embedding/lexical similarity + numeric
  conflict heuristics against existing (esp. pinned) facts; returns conflicting
  fact ids. New facts that contradict an existing fact with ``contradicts``
  linkage are rejected; near-duplicates reinforce instead of duplicate.
"""
from __future__ import annotations

from typing import Any

from .config import EngineConfig
from .models import CommitReport, Delta, Verdict

LEAD_TRANSITIONS: dict[str, frozenset[str]] = {
    "unheard": frozenset({"rumored", "abandoned"}),
    "rumored": frozenset({"accepted", "abandoned"}),
    "accepted": frozenset({"in_progress", "failed", "abandoned"}),
    "in_progress": frozenset({"complicated", "resolved", "failed", "abandoned"}),
    "complicated": frozenset({"in_progress", "resolved", "failed", "abandoned"}),
    "resolved": frozenset(),
    "failed": frozenset(),
    "abandoned": frozenset(),
}


class Validator:
    def __init__(self, store: Any, config: EngineConfig | None = None) -> None:
        self.store = store
        self.config = config or EngineConfig()

    def validate(self, deltas: list[Delta], *, turn: int) -> list[Verdict]:
        raise NotImplementedError("R2 card implements validate")

    def commit(self, verdicts: list[Verdict], *, turn: int) -> CommitReport:
        raise NotImplementedError("R2 card implements commit")

    def check_contradiction(self, statement: str, *, exclude_fact_id: int | None = None) -> list[int]:
        """Fact ids that conflict with ``statement`` (empty = no conflict)."""
        raise NotImplementedError("R2 card implements check_contradiction")
