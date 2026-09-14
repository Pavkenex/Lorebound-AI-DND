"""Integration: ``ContextAssembler`` over the REAL store + memory subsystem.

R3 (``memory.py``) lands in parallel with R5, so this module skips while every
memory method still raises ``NotImplementedError`` (the R5-era stub). It
activates by itself once the parallel work merges — ``_memory_ok()`` probes the
real ``MemoryBundle``, and ``tests/test_context.py`` asserts that the guard
matches the live state of ``memory.py`` in both worlds.
"""
from __future__ import annotations

from collections.abc import Iterator
from dataclasses import dataclass

import pytest

from engine.config import EngineConfig
from engine.context import ContextAssembler
from engine.memory import MemoryBundle
from engine.models import NPC, AssembledPrompt, Intent, Lead, MechanicalOutcome, to_row
from engine.store import Store

INPUT = "I search the yard for the missing shipment"


def _memory_ok() -> bool:
    """True once ``memory.MemoryBundle`` is implemented (not the parallel stub)."""
    store = Store(":memory:")
    try:
        MemoryBundle.build(store).chronicle.tail()
    except NotImplementedError:
        return False
    return True


pytestmark = pytest.mark.skipif(
    not _memory_ok(),
    reason="memory lands in parallel (R3) — this module activates after the merge",
)


@dataclass
class World:
    """A seeded store plus the row ids the scene dict refers to."""

    store: Store
    marla: int
    hob: int
    ferryman: int


@pytest.fixture()
def world(tmp_path) -> Iterator[World]:
    """Two NPCs in the yard, one elsewhere; memories, a lead, pins, chronicle."""
    store = Store(tmp_path / "engine.db")
    marla = store.insert("npcs", to_row(NPC(name="Marla", location_id="yard",
                                            disposition_base=-5.0)))
    hob = store.insert("npcs", to_row(NPC(name="Hob", location_id="yard")))
    ferryman = store.insert("npcs", to_row(NPC(name="The ferryman", location_id="river")))
    store.insert("leads", to_row(Lead(
        title="The missing shipment", stage="in_progress",
        related_npc_ids=[f"npc:{marla}"],
    )))

    memory = MemoryBundle.build(store)
    memory.npc_memory.add(
        npc_id=f"npc:{marla}", turn=3,
        statement="the player promised to find the missing shipment",
    )
    memory.npc_memory.add(npc_id=f"npc:{hob}", turn=4, statement="the player paid for the ale")
    memory.npc_memory.add(
        npc_id=f"npc:{ferryman}", turn=4,
        statement="the ferryman watches the far bank",
    )
    memory.chronicle.append(
        turn_id=4, actor="player", action_summary="the player searched the yard",
        mechanical_result="success",
    )
    memory.facts.add(statement="The player promised to find the missing shipment",
                     turn=3, source="player", pinned=True)
    memory.facts.add(statement="Hob brews the best ale in the yard",
                     turn=1, source="narrator", pinned=False)
    try:
        yield World(store=store, marla=marla, hob=hob, ferryman=ferryman)
    finally:
        store.close()


def _scene(world: World) -> dict:
    return {
        "ruleset": "Rules: narrate the resolved outcome.",
        "location": {"name": "The yard", "description_static": "A dusty training yard.",
                     "connections": ["gate", "hall"]},
        "present_npcs": [
            {"id": f"npc:{world.marla}", "name": "Marla",
             "mood": {"valence": -0.3, "arousal": 0.5}, "disposition": -20},
            {"id": f"npc:{world.hob}", "name": "Hob"},
        ],
        "player": {"name": "Player", "stats": {"hp": 10, "max_hp": 10, "currency": 25},
                   "inventory": [], "status_effects": []},
    }


def _outcome() -> MechanicalOutcome:
    return MechanicalOutcome(turn=5, kind="none", label="searches the yard",
                             verdict_line="nothing mechanical this turn")


def _assemble(world: World, window: int | None = 32000) -> AssembledPrompt:
    return ContextAssembler(world.store, EngineConfig()).assemble(
        turn=5, scene=_scene(world), mechanical=_outcome(),
        intent=Intent(kind="action", text=INPUT), context_window=window,
    )


def _section(prompt: AssembledPrompt, name: str) -> str:
    return next((body for section, body in prompt.sections if name == section), "")


def test_assemble_over_the_real_store_and_memory(world: World) -> None:
    prompt = _assemble(world)
    text = prompt.text
    assert prompt.system == "Rules: narrate the resolved outcome."
    # per-NPC memory, scoped by presence in the scene
    assert "the player promised to find the missing shipment" in _section(prompt, "npc_memory")
    assert "the player paid for the ale" in _section(prompt, "npc_memory")
    assert "the ferryman watches the far bank" not in text
    # pinned facts only (§3.6): the unpinned fact stays retrievable, not injected
    assert "The player promised to find the missing shipment" in _section(prompt, "pinned_facts")
    assert "Hob brews the best ale in the yard" not in text
    # chronicle tail + lead state come from the real store
    assert "the player searched the yard" in _section(prompt, "chronicle")
    assert "The missing shipment — stage: in_progress" in _section(prompt, "leads")
    assert [name for name, _ in prompt.sections][:3] == ["scene", "mechanics", "pinned_facts"]


def test_real_store_assembly_is_deterministic_and_accounted(world: World) -> None:
    first = _assemble(world)
    second = ContextAssembler(world.store, EngineConfig()).assemble(
        turn=5, scene=_scene(world), mechanical=_outcome(),
        intent=Intent(kind="action", text=INPUT), context_window=32000,
    )
    assert second.text == first.text
    assert second.system == first.system
    assert second.sections == first.sections

    accounting = first.accounting
    assembler = ContextAssembler(world.store, EngineConfig())
    assert accounting["sections"]["system"] == assembler.estimate_tokens(first.system)
    for name, text in first.sections:
        assert accounting["sections"][name] == assembler.estimate_tokens(text)
    assert accounting["budget"]["used"] == sum(accounting["sections"].values())
    assert accounting["budget"]["over_budget"] is False
    assert accounting["dropped"] == []  # a 32k window holds this turn in full


def test_real_store_assembly_degrades_under_a_tight_window(world: World) -> None:
    prompt = _assemble(world, window=140)
    accounting = prompt.accounting
    assert accounting["budget"]["used"] <= accounting["budget"]["total"]
    assert "mechanics" in [name for name, _ in prompt.sections]
    assert _section(prompt, "pinned_facts") != ""  # non-negotiable
    assert accounting["dropped"], "a 140-token window must shed lower-priority content"
    assert accounting["dropped"][0]["section"] == "saga"
