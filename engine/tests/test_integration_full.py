"""Cross-module integration over the merged engine (I2).

Each card's own suite covers its subsystem — often against doubles. This module
chains the REAL implementations across the merged tree, so the seams no single
card owns are exercised end to end:

* ``ContextAssembler.run`` with NO test double — ``components=None`` makes the
  assembler build the real ``memory.MemoryBundle`` from the store, and only what
  store + memory really return reaches the prompt;
* ``providers.jsonproto`` -> ``Delta`` -> ``Validator`` -> store -> memory ->
  context: the Pass B envelope decoded from a native tool call is validated and
  committed, then read back through the relationship ledger and a fresh
  assembly (clamp, conservation reject and unknown-kind drop included);
* the ``evals`` harness over the merged tree — the shipped suite runs green
  in-process and its boundary-clamps rows carry memory's read-time decay, i.e.
  the harness really ran the merged memory subsystem, not a stub;
* budget accounting over real sections — a wide window injects every section
  with nothing dropped, and a tight window sheds the lowest-priority content
  (continuity first) while mechanics + pinned facts always survive.

Deterministic and offline: the provider envelope is a hand-built tool call, no
transport or network is involved.
"""
from __future__ import annotations

import json
import math
from collections.abc import Iterator
from dataclasses import dataclass

import pytest

from engine.config import EngineConfig
from engine.context import ContextAssembler
from engine.memory import MemoryBundle
from engine.models import (
    NPC,
    Character,
    CheckRequest,
    CheckResult,
    Intent,
    Lead,
    MechanicalOutcome,
    ToolCall,
    to_row,
)
from engine.providers.jsonproto import PROPOSE_DELTAS_TOOL_NAME, proposals_from_tool_calls
from engine.store import Store
from engine.validate import Validator
from evals.harness import run_all

INPUT = "I ask Marla about the missing shipment"

# One "slow" relationship decay step per turn (config.memory.relationship_decay_per_turn).
_SLOW_DECAY = 0.01
# boundary-clamps drives Tam's meter to +100 in one turn and probes again one
# turn later: the clamp applies the decayed slack, not 0.0 (spec §3.5).
_TAM_SLACK = round(100 - round(90 + 10 * math.exp(-_SLOW_DECAY), 6), 6)

# The Pass B envelope as a model would send it through the native tool path.
ENVELOPE: dict = {
    "narration": "Marla's wariness thins the moment the oath is honoured.",
    "npc_dialogue": [{"npc_id": "npc:1", "name": "Marla", "text": "The oath holds."}],
    "deltas": [
        {"kind": "relationship", "target": "npc:1", "reason": "kept the oath",
         "data": {"category": "trust", "delta": 40}},
        {"kind": "currency", "target": "", "reason": "paid the ferryman",
         "data": {"amount": -5}},
        {"kind": "currency", "target": "", "reason": "bribed the customs house",
         "data": {"amount": -999}},
        {"kind": "mood", "target": "npc:1", "reason": "relieved",
         "data": {"valence_delta": 0.3, "arousal_delta": -0.1}},
        {"kind": "lead_transition", "target": "lead:1", "reason": "delivered",
         "data": {"new_stage": "resolved", "justification": "the shipment came home"}},
        {"kind": "weather", "target": "", "data": {"rain": True}},
    ],
}


@dataclass
class World:
    """A real store plus the row ids the scene dict refers to."""

    store: Store
    marla: int


@pytest.fixture()
def world(tmp_path) -> Iterator[World]:
    """Real store + real memory content: two NPCs, the player, one open lead."""
    store = Store(tmp_path / "engine.db")
    marla = store.insert(
        "npcs", to_row(NPC(name="Marla", location_id="yard", disposition_base=90.0))
    )
    store.insert("npcs", to_row(NPC(name="Hob", location_id="yard")))
    ferryman = store.insert("npcs", to_row(NPC(name="The ferryman", location_id="river")))
    store.insert("characters", to_row(Character(
        name="Player", location_id="yard",
        stats={"hp": 9, "max_hp": 10, "currency": 25},
        inventory=[], status_effects=[],
    )))
    store.insert("leads", to_row(Lead(
        title="The missing shipment", stage="in_progress",
        related_npc_ids=[f"npc:{marla}"],
    )))
    memory = MemoryBundle.build(store)
    memory.npc_memory.add(npc_id=f"npc:{marla}", turn=3,
                          statement="the player promised to find the missing shipment")
    memory.npc_memory.add(npc_id=f"npc:{ferryman}", turn=4,
                          statement="the ferryman watches the far bank")
    memory.chronicle.append(turn_id=4, actor="player",
                            action_summary="the player searched the yard",
                            mechanical_result="success")
    memory.facts.add(statement="The player promised to find the missing shipment",
                     turn=3, source="player", pinned=True)
    try:
        yield World(store=store, marla=marla)
    finally:
        store.close()


def _scene(world: World) -> dict:
    return {
        "ruleset": "Rules: narrate the resolved outcome.",
        "location": {"name": "The yard", "description_static": "A dusty training yard.",
                     "connections": ["gate", "hall"]},
        "present_npcs": [{"id": f"npc:{world.marla}", "name": "Marla",
                          "mood": {"valence": 0.2, "arousal": -0.1}, "disposition": 100}],
        "player": {"name": "Player", "stats": {"hp": 9, "max_hp": 10, "currency": 25},
                   "inventory": [], "status_effects": []},
    }


def _outcome() -> MechanicalOutcome:
    check = CheckResult(
        request=CheckRequest(skill="presence", dc=12), roll=15, modifier=3,
        total=18, band="success", verdict_line="SUCCESS — 18 vs DC 12",
    )
    return MechanicalOutcome(turn=5, kind="check", label="reads Marla's mood",
                             check=check, verdict_line="SUCCESS — 18 vs DC 12")


def _assemble(world: World, *, turn: int = 5, window: int | None = 32000):
    return ContextAssembler(world.store, EngineConfig()).assemble(
        turn=turn, scene=_scene(world), mechanical=_outcome(),
        intent=Intent(kind="dialogue", text=INPUT), context_window=window,
    )


def _section(prompt, name: str) -> str:
    return next((body for section, body in prompt.sections if name == section), "")


# --------------------------------------------------------------------------- #
# 1. context assembly over the REAL memory components (no doubles)
# --------------------------------------------------------------------------- #

def test_context_assembly_builds_the_real_memory_bundle(world: World) -> None:
    """``components=None`` must reach the real MemoryBundle, not a double."""
    assembler = ContextAssembler(world.store, EngineConfig())
    assert assembler.components is None  # nothing was injected up front

    prompt = assembler.assemble(
        turn=5, scene=_scene(world), mechanical=_outcome(),
        intent=Intent(kind="dialogue", text=INPUT), context_window=32000,
    )
    assert isinstance(assembler.components, MemoryBundle)  # built from the store

    # every section below was produced by a real component reading the store
    assert "SUCCESS — 18 vs DC 12" in _section(prompt, "mechanics")
    assert "The player promised to find the missing shipment" in _section(prompt, "pinned_facts")
    assert "the player promised to find the missing shipment" in _section(prompt, "npc_memory")
    assert "the player searched the yard" in _section(prompt, "chronicle")
    assert "The missing shipment — stage: in_progress" in _section(prompt, "leads")
    # per-NPC scoping across the context/memory seam: the absent ferryman is out
    assert "ferryman" not in prompt.text


# --------------------------------------------------------------------------- #
# 2. jsonproto -> Delta -> Validator -> store -> memory -> context
# --------------------------------------------------------------------------- #

def test_native_tool_call_round_trips_into_store_memory_and_context(world: World) -> None:
    notes: list[str] = []
    proposals = proposals_from_tool_calls(
        [ToolCall(id="call_1", name=PROPOSE_DELTAS_TOOL_NAME, arguments=ENVELOPE)],
        notes=notes,
    )
    assert proposals.narration == ENVELOPE["narration"]
    assert [delta.kind for delta in proposals.deltas] == [
        "relationship", "currency", "currency", "mood", "lead_transition"]
    assert [delta.target for delta in proposals.deltas[:2]] == [f"npc:{world.marla}", ""]
    assert len(notes) == 1 and "weather" in notes[0]  # unknown kind dropped, not crashed

    validator = Validator(world.store, EngineConfig())
    verdicts = validator.validate(list(proposals.deltas), turn=5)
    assert [verdict.kind for verdict in verdicts] == [
        "clamped", "accepted", "rejected", "accepted", "accepted"]
    report = validator.commit(verdicts, turn=5)
    assert (len(report.accepted), len(report.clamped), len(report.rejected)) == (3, 1, 1)
    assert "conservation" in report.rejected[0].note
    assert report.clamped[0].clamped_to == 10.0  # 90 anchor -> +10 fits under ±100

    # the store holds the APPLIED values: the proposal (+40) never lands,
    # and the rejected overspend left the balance untouched
    ledger = world.store.find("relationship_ledger", {"npc_id": f"npc:{world.marla}"})
    assert [row["delta"] for row in ledger] == [10.0]
    assert [row["turn"] for row in ledger] == [5]
    stats = json.loads(world.store.find_one("characters", order_by="id")["stats"])
    assert stats["currency"] == 20 and stats["hp"] == 9
    assert world.store.find_one("leads", order_by="id")["stage"] == "resolved"

    # memory reads what validator + store wrote: the decayed meter at turn 6
    memory = MemoryBundle.build(world.store)
    currents = memory.ledger.currents(
        npc_id=f"npc:{world.marla}", player_id="player", turn=6
    )
    assert currents["trust"] == pytest.approx(
        90.0 + 10.0 * math.exp(-_SLOW_DECAY), abs=1e-6
    )

    # context reads the committed state back out (store -> memory -> prompt)
    prompt = _assemble(world, turn=6)
    assert "The missing shipment — stage: resolved" in _section(prompt, "leads")
    assert "the shipment came home" in _section(prompt, "leads")


# --------------------------------------------------------------------------- #
# 3. the evals harness over the merged tree
# --------------------------------------------------------------------------- #

def test_evals_harness_runs_green_over_the_merged_tree() -> None:
    report = run_all()
    assert report.ok, report.failure_lines()
    required = {"boundary-clamps", "conservation", "contradiction-bait",
                "dead-npc-lock", "lead-gates"}
    assert required <= {result.name for result in report.results}

    # boundary-clamps' second relationship probe clamps to the DECAYED slack:
    # proof the harness path exercised memory's read-time decay (spec §3.5),
    # not the pre-merge stub whose meter never faded.
    snapshot = next(result.snapshot for result in report.results
                    if result.name == "boundary-clamps")
    by_npc: dict[str, list[float]] = {}
    for row in snapshot["final_state"]["relationship_ledger"]:
        by_npc.setdefault(row["npc_id"], []).append(row["delta"])
    assert by_npc["npc:1"] == [10.0, _TAM_SLACK]


# --------------------------------------------------------------------------- #
# 4. budget accounting with real sections
# --------------------------------------------------------------------------- #

def test_budget_accounting_over_real_sections(world: World) -> None:
    prompt = _assemble(world, window=32000)
    accounting = prompt.accounting
    assert accounting["budget"]["over_budget"] is False
    assert accounting["dropped"] == []  # a 32k window holds this turn in full
    assert accounting["budget"]["used"] == sum(accounting["sections"].values())
    assert accounting["cacheable"] == ["system"]
    for name in ("system", "scene", "mechanics", "pinned_facts",
                 "npc_memory", "leads", "chronicle"):
        assert accounting["sections"][name] > 0, name
    assert [name for name, _ in prompt.sections][:3] == ["scene", "mechanics", "pinned_facts"]


def test_budget_sheds_the_lowest_priority_sections_first(world: World) -> None:
    memory = MemoryBundle.build(world.store)
    for turn in range(5, 20):
        memory.chronicle.append(
            turn_id=turn, actor="player",
            action_summary=("the player crossed the yard, argued with the gatekeeper "
                            f"about the manifest and searched the wagon again ({turn})"),
            mechanical_result="success",
        )
    prompt = _assemble(world, window=260)
    accounting = prompt.accounting
    dropped = [entry["section"] for entry in accounting["dropped"]]
    assert dropped, "a 260-token window must shed lower-priority content"
    assert dropped[0] == "chronicle"  # continuity is the first group to shed
    assert "mechanics" not in dropped and "pinned_facts" not in dropped
    assert accounting["budget"]["used"] <= accounting["budget"]["total"]
    # the non-negotiables are still on the page, from their real sources
    assert "SUCCESS — 18 vs DC 12" in _section(prompt, "mechanics")
    assert "The player promised to find the missing shipment" in _section(prompt, "pinned_facts")
