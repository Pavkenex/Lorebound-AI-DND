"""Playable session + deterministic stub narrator (spec §1, §10.1).

The stub narrator makes the engine fully playable with NO model and no key:
it narrates from the resolved mechanical outcome + scene state via templates
(same ``ProposalSet`` envelope as live models; emits no state deltas by
default). ``PlaySession`` is the thin player-facing wrapper the CLI uses.
"""
from __future__ import annotations

from typing import Any

from .config import EngineConfig
from .models import AssembledPrompt, ProposalSet, TurnResult


class StubNarrator:
    """NarratorRunner-compatible, deterministic, offline (used by --stub, tests, evals)."""

    def narrate(self, *, prompt: AssembledPrompt, turn: int,
                retry_note: str | None = None) -> ProposalSet:
        raise NotImplementedError("R7 card implements StubNarrator.narrate")


class PlaySession:
    """One campaign session bound to a SQLite DB.

    ``start()`` seeds the world (from ``world`` dict or the demo fixture) when
    the DB is empty and is idempotent on an existing campaign.
    """

    @classmethod
    def start(cls, *, db_path: str, world: dict | None = None,
              config: EngineConfig | None = None,
              narrator: Any = None) -> PlaySession:
        raise NotImplementedError("R7 card implements PlaySession.start")

    def act(self, player_input: str) -> TurnResult:
        raise NotImplementedError("R7 card implements PlaySession.act")

    def state_view(self) -> dict:
        """Small JSON-able snapshot: turn, location, HP, inventory, top leads."""
        raise NotImplementedError("R7 card implements PlaySession.state_view")

    def close(self) -> None:
        raise NotImplementedError("R7 card implements PlaySession.close")


def demo_world() -> dict:
    """Tiny starter world for play/tests (R7 may move content to fixtures/)."""
    raise NotImplementedError("R7 card implements demo_world")
