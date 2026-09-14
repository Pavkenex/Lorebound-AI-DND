"""Context assembly + budget controller (spec §4).

Contract (R5 card). Per turn, build the prompt under a hard token budget with
the spec's priority order (highest first):
  1. system/ruleset (static, cacheable; mark for provider-side caching)
  2. current scene structured state (location, present NPCs, player) — always
  3. this turn's resolved mechanical outcome — always (never dropped)
  4. pinned facts relevant to scene — never dropped once included
  5. NPC memory for NPCs present, top-K by salience
  6. relevant lead state
  7. chronicle tail (last 12 structured / last 2-3 verbatim)
  8. saga digest (campaign by default; arc/session only when relevance flags)
Over budget: drop from the bottom first; truncate digest/lower-salience memory
before anything pinned. ``accounting`` records per-section token estimates,
the budget, and exactly what was dropped (telemetry for spec §9).
"""
from __future__ import annotations

from typing import Any

from .config import EngineConfig
from .models import AssembledPrompt, Intent, MechanicalOutcome
from .similarity import LexicalSimilarity, Similarity


class ContextAssembler:
    def __init__(self, store: Any, config: EngineConfig | None = None,
                 *, sim: Similarity | None = None, components: Any = None) -> None:
        """``components`` — a ``memory.MemoryBundle`` (or a test double with the
        same method surface; memory lands in parallel with context). When None,
        built from ``store`` lazily on first use."""
        self.store = store
        self.config = config or EngineConfig()
        self.sim = sim or LexicalSimilarity()
        self.components = components

    def estimate_tokens(self, text: str) -> int:
        """Deterministic heuristic (~4 chars/token); overridable per provider."""
        return max(1, (len(text) + 3) // 4)

    def assemble(self, *, turn: int, scene: dict,
                 mechanical: MechanicalOutcome | None, intent: Intent,
                 context_window: int | None = None,
                 relevant: dict | None = None) -> AssembledPrompt:
        """Build the full prompt for Pass B. ``scene`` carries cheap reads
        (location, present_npcs, player); retrieval of memory/facts/leads and
        all budget math happen here."""
        raise NotImplementedError("R5 card implements assemble")
