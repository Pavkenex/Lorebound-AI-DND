"""Memory subsystem — six stores + world facts (spec §3).

Contract (R3 card). All persistence goes through ``Store``'s generic row API;
scoring/decay logic is deterministic and lives here. Salience is recomputed
lazily at retrieval time (spec §3.1), never stored as truth.

Store classes:
- NPCMemoryStore  — per-NPC entries + salience retrieval (top-K per NPC present)
- Chronicle       — structured short-term tail (~12) + verbatim last 2-3 turns
- RelationshipLedger — append-only deltas + decayed current values + reasons
- MoodTracker     — transient valence/arousal, half-life decay (spec §3.7)
- SagaDigest      — hierarchical session -> arc -> campaign, archived, injectable
- FactStore       — world facts: pinned, auto-pin rules, contradiction scan

Auto-pin (spec §3.6): promises, deaths, betrayals, and explicit
player-established facts get ``pinned=True`` via ``auto_pin_worthy``.
"""
from __future__ import annotations

from typing import Any

from .config import EngineConfig
from .models import (
    ChronicleEntry,
    MemoryType,
    MoodState,
    NPCMemoryEntry,
    SagaRow,
    WorldFact,
)
from .similarity import LexicalSimilarity, Similarity


class NPCMemoryStore:
    def __init__(self, store: Any, config: EngineConfig | None = None,
                 sim: Similarity | None = None) -> None:
        self.store = store
        self.config = config or EngineConfig()
        self.sim = sim or LexicalSimilarity()

    def add(self, *, npc_id: str, turn: int, statement: str,
            type: str = MemoryType.OBSERVED.value, sentiment: float = 0.0,
            decay_rate: float | None = None) -> int:
        raise NotImplementedError("R3 card implements NPCMemoryStore.add")

    def score(self, entry: NPCMemoryEntry, *, scene_context: list[str], turn: int) -> float:
        """Spec §3.1 formula: w1*recency + w2*|sentiment| + w3*relevance
        + w4*log1p(reinforced) - w5*decay(turns_since_established, decay_rate)."""
        raise NotImplementedError("R3 card implements NPCMemoryStore.score")

    def retrieve(self, *, npc_id: str, scene_context: list[str], turn: int,
                 k: int | None = None) -> list[tuple[NPCMemoryEntry, float]]:
        """Top-K entries for THIS npc by salience; updates last_referenced_turn
        for returned entries. K defaults to config.memory.npc_top_k."""
        raise NotImplementedError("R3 card implements NPCMemoryStore.retrieve")

    def reinforce(self, *, npc_id: str, statement: str, turn: int) -> int:
        """Bump reinforced_count for existing entries matching ``statement``
        (returns how many were reinforced). Used when a fact is confirmed again."""
        raise NotImplementedError("R3 card implements NPCMemoryStore.reinforce")

    def decay_rate_for(self, type: str) -> float:
        return self.config.salience.decay_rates.get(type, 0.15)


class Chronicle:
    def __init__(self, store: Any, config: EngineConfig | None = None) -> None:
        self.store = store
        self.config = config or EngineConfig()

    def append(self, *, turn_id: int, actor: str, action_summary: str,
               mechanical_result: str = "", consequence_oneliner: str = "",
               verbatim_text: str | None = None) -> int:
        """Append one structured entry; enforces the verbatim window."""
        raise NotImplementedError("R3 card implements Chronicle.append")

    def tail(self, *, n: int | None = None) -> list[ChronicleEntry]:
        """Newest-last chronological window (default config.memory.chronicle_window)."""
        raise NotImplementedError("R3 card implements Chronicle.tail")

    def enforce_verbatim_window(self) -> None:
        """Null out verbatim_text older than the last N entries (spec §3.2)."""
        raise NotImplementedError("R3 card implements Chronicle.enforce_verbatim_window")


class RelationshipLedger:
    def __init__(self, store: Any, config: EngineConfig | None = None) -> None:
        self.store = store
        self.config = config or EngineConfig()

    def append_delta(self, *, npc_id: str, player_id: str, turn: int, delta: float,
                     reason: str, category: str, decay_class: str | None = None) -> int:
        """Append-only; decay_class defaults from category+f magnitude."""
        raise NotImplementedError("R3 card implements RelationshipLedger.append_delta")

    def currents(self, *, npc_id: str, player_id: str, turn: int) -> dict[str, float]:
        """Per-category + "total" decayed current values, anchored at the NPC's
        disposition_base (= sum of decayed ledger deltas + base anchor)."""
        raise NotImplementedError("R3 card implements RelationshipLedger.currents")

    def describe(self, *, npc_id: str, player_id: str, turn: int) -> str:
        """Reason-based one-line relationship description (audit trail, spec §3.5)."""
        raise NotImplementedError("R3 card implements RelationshipLedger.describe")


class MoodTracker:
    def __init__(self, store: Any, config: EngineConfig | None = None) -> None:
        self.store = store
        self.config = config or EngineConfig()

    def ensure(self, *, npc_id: str, baseline_valence: float = 0.0,
               baseline_arousal: float = 0.0) -> None:
        raise NotImplementedError("R3 card implements MoodTracker.ensure")

    def apply(self, *, npc_id: str, valence_delta: float, arousal_delta: float,
              turn: int, cause: str = "") -> None:
        """Decay to now, then apply the spike; clamps [-1, 1]; persists."""
        raise NotImplementedError("R3 card implements MoodTracker.apply")

    def current(self, *, npc_id: str, turn: int) -> MoodState:
        """Decayed view (computed, not persisted) of the NPC's mood state."""
        raise NotImplementedError("R3 card implements MoodTracker.current")


class SagaDigest:
    def __init__(self, store: Any, config: EngineConfig | None = None,
                 summarizer: Any = None) -> None:
        """``summarizer`` is an injectable callable(texts) -> str; the default
        deterministic builder needs no model (spec §3.3 notes the pass SHOULD
        use a cheap model when available — that is wired by R7, not required)."""
        self.store = store
        self.config = config or EngineConfig()
        self.summarizer = summarizer

    def maybe_summarize(self, *, turn: int, force: bool = False) -> list[SagaRow]:
        """Session-level summary every ~config.memory.session_summary_turns turns;
        arc/campaign rollups when a lower level completes. Returns new rows."""
        raise NotImplementedError("R3 card implements SagaDigest.maybe_summarize")

    def level_text(self, level: str) -> str | None:
        """Latest text for a level (campaign | arc | session)."""
        raise NotImplementedError("R3 card implements SagaDigest.level_text")

    def archive_of(self, level: str) -> list[SagaRow]:
        """ALL rows at a level — archive, newest last (never discarded)."""
        raise NotImplementedError("R3 card implements SagaDigest.archive_of")

    def injectable(self, *, scene_context: list[str], turn: int) -> list[SagaRow]:
        """Campaign digest by default; arc/session detail only when the scene
        scores as referencing that specific content (spec §3.3)."""
        raise NotImplementedError("R3 card implements SagaDigest.injectable")


class FactStore:
    def __init__(self, store: Any, config: EngineConfig | None = None,
                 sim: Similarity | None = None) -> None:
        self.store = store
        self.config = config or EngineConfig()
        self.sim = sim or LexicalSimilarity()

    def add(self, *, statement: str, turn: int, source: str = "", tags: list | None = None,
            pinned: bool | None = None) -> int:
        """Insert a fact. ``pinned=None`` -> auto-pin decision via auto_pin_worthy.
        Near-duplicates reinforce instead of duplicating (see spec §3.6)."""
        raise NotImplementedError("R3 card implements FactStore.add")

    def pinned_facts(self) -> list[WorldFact]:
        raise NotImplementedError("R3 card implements FactStore.pinned_facts")

    def retrieve(self, *, scene_context: list[str], turn: int,
                 limit: int | None = None) -> list[WorldFact]:
        """Pinned facts above min relevance ALWAYS eligible; others ranked."""
        raise NotImplementedError("R3 card implements FactStore.retrieve")

    def contradiction_scan(self, statement: str, *, exclude_fact_id: int | None = None) -> list[int]:
        """Ids of existing facts conflicting with ``statement`` (spec §3.6)."""
        raise NotImplementedError("R3 card implements FactStore.contradiction_scan")


def auto_pin_worthy(statement: str, *, kind: str | None = None) -> bool:
    """Promises, deaths, betrayals, explicit player-establishments -> True (§3.6)."""
    raise NotImplementedError("R3 card implements auto_pin_worthy")
