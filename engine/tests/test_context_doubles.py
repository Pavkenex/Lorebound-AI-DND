"""Test doubles for the R5 context tests (memory lands in parallel).

``MemoryDouble`` implements the ``memory.MemoryBundle`` *method surface* that
``context.ContextAssembler`` consumes, with call recording so tests can assert
scoping (only PRESENT NPCs are ever retrieved) and the exact keyword arguments
context passes down:

* ``npc_memory.retrieve(npc_id=…, scene_context=…, turn=…, k=…)``
* ``chronicle.tail(n=…)``
* ``saga.injectable(scene_context=…, turn=…)``
* ``facts.pinned_facts()``

``ledger`` / ``moods`` are loud placeholders: context must render the cheap
per-NPC reads the caller put in ``scene`` instead of walking the store.

``tests/test_context_integration.py`` exercises the real ``MemoryBundle`` once
R3's implementation lands (it skips while ``memory.py`` is still a stub).
"""
from __future__ import annotations

from typing import Any

from engine.models import (
    ChronicleEntry,
    MemoryType,
    NPCMemoryEntry,
    SagaLevel,
    SagaRow,
    WorldFact,
)


class _Unused:
    """Loud placeholder for a component context must not touch."""

    def __init__(self, name: str) -> None:
        self._name = name

    def __getattr__(self, item: str) -> Any:
        raise AssertionError(f"context must not call {self._name}.{item}")


class NPCMemoryDouble:
    """``NPCMemoryStore`` surface: entries per NPC, top-K truncation, call log."""

    def __init__(self, entries_by_npc: dict[str, list] | None = None) -> None:
        self.entries_by_npc = {
            key: list(value) for key, value in (entries_by_npc or {}).items()
        }
        self.calls: list[dict] = []

    def retrieve(self, *, npc_id: str, scene_context: list[str], turn: int,
                 k: int | None = None) -> list[tuple[NPCMemoryEntry, float]]:
        self.calls.append({
            "npc_id": npc_id,
            "scene_context": list(scene_context),
            "turn": turn,
            "k": k,
        })
        entries = list(self.entries_by_npc.get(npc_id, []))
        return entries if k is None else entries[: max(0, int(k))]

    def queried(self) -> list[str]:
        """NPC ids retrieved, in call order (scoping assertions)."""
        return [call["npc_id"] for call in self.calls]


class ChronicleDouble:
    """``Chronicle`` surface: ``tail`` window + call log."""

    def __init__(self, entries: list[ChronicleEntry] | None = None) -> None:
        self.entries = list(entries or [])
        self.calls: list[int | None] = []

    def tail(self, *, n: int | None = None) -> list[ChronicleEntry]:
        self.calls.append(n)
        if n is None:
            return list(self.entries)
        return list(self.entries)[-int(n):] if n > 0 else []


class SagaDouble:
    """``SagaDigest`` surface: ``injectable`` rows + call log."""

    def __init__(self, rows: list[SagaRow] | None = None) -> None:
        self.rows = list(rows or [])
        self.calls: list[dict] = []

    def injectable(self, *, scene_context: list[str], turn: int) -> list[SagaRow]:
        self.calls.append({"scene_context": list(scene_context), "turn": turn})
        return list(self.rows)


class FactDouble:
    """``FactStore`` surface: pinned facts + call counter."""

    def __init__(self, pinned: list[WorldFact] | None = None) -> None:
        self.pinned = list(pinned or [])
        self.calls = 0

    def pinned_facts(self) -> list[WorldFact]:
        self.calls += 1
        return list(self.pinned)


class MemoryDouble:
    """Test double for ``engine.memory.MemoryBundle`` (same method surface)."""

    def __init__(self, *, npc_memory: NPCMemoryDouble | None = None,
                 chronicle: ChronicleDouble | None = None,
                 saga: SagaDouble | None = None,
                 facts: FactDouble | None = None) -> None:
        self.npc_memory = npc_memory or NPCMemoryDouble()
        self.chronicle = chronicle or ChronicleDouble()
        self.saga = saga or SagaDouble()
        self.facts = facts or FactDouble()
        self.ledger = _Unused("ledger")
        self.moods = _Unused("moods")

    @classmethod
    def build(cls, store: Any, config: Any = None, sim: Any = None) -> MemoryDouble:
        """Mirrors ``MemoryBundle.build``; tests supply the contents directly."""
        return cls()


# --------------------------------------------------------------------------- #
# Content factories (dataclasses from the frozen models module)
# --------------------------------------------------------------------------- #

def npc_entry(*, npc_id: str = "npc:1", statement: str = "the player was kind",
              type: str = MemoryType.OBSERVED.value, sentiment: float = 0.0,
              entry_id: int = 0, turn_established: int = 1) -> NPCMemoryEntry:
    return NPCMemoryEntry(
        id=entry_id, npc_id=npc_id, turn_established=turn_established,
        statement=statement, type=type, sentiment=sentiment,
    )


def chronicle_entry(*, turn_id: int = 1, actor: str = "player",
                    action_summary: str = "the player looked around",
                    mechanical_result: str = "", consequence_oneliner: str = "",
                    verbatim_text: str | None = None,
                    entry_id: int = 0) -> ChronicleEntry:
    return ChronicleEntry(
        id=entry_id, turn_id=turn_id, actor=actor, action_summary=action_summary,
        mechanical_result=mechanical_result,
        consequence_oneliner=consequence_oneliner, verbatim_text=verbatim_text,
    )


def saga_row(*, level: str = SagaLevel.CAMPAIGN.value, text: str = "the saga so far",
             scope_id: str = "campaign", references: list | None = None,
             row_id: int = 0, created_turn: int = 0) -> SagaRow:
    return SagaRow(
        id=row_id, level=level, scope_id=scope_id, text=text,
        created_turn=created_turn, references=[] if references is None else references,
    )


def pinned_fact(*, statement: str = "the player promised to find the shipment",
                fact_id: int = 1, pinned: bool = True,
                tags: list | None = None) -> WorldFact:
    return WorldFact(
        id=fact_id, statement=statement, pinned=pinned, established_turn=1,
        source="narrator", tags=[] if tags is None else tags,
    )


def scene_dict(*, ruleset: str | None = None, location: dict | str | None = None,
               npcs: list | None = None, player: dict | None = None,
               content_policy: str | None = None) -> dict:
    """A minimal valid ``scene`` dict (the shape context documents)."""
    scene: dict = {
        "location": location if location is not None else {
            "name": "The yard", "description_static": "A dusty training yard.",
            "connections": ["gate", "hall"],
        },
        "present_npcs": [] if npcs is None else list(npcs),
        "player": player if player is not None else {
            "name": "Player", "stats": {"hp": 10, "max_hp": 10, "currency": 25},
            "inventory": [{"item_id": "rope", "qty": 2}],
            "status_effects": [],
        },
    }
    if ruleset is not None:
        scene["ruleset"] = ruleset
    if content_policy is not None:
        scene["content_policy"] = content_policy
    return scene


def npc_read(*, npc_id: str = "npc:1", name: str = "Marla", alive: bool = True,
             mood: dict | None = None, disposition: float | None = None) -> dict:
    """A per-NPC cheap read as the caller puts it in ``scene["present_npcs"]``."""
    entry: dict = {"id": npc_id, "name": name, "alive": alive}
    if mood is not None:
        entry["mood"] = mood
    if disposition is not None:
        entry["disposition"] = disposition
    return entry
