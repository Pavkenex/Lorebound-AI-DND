"""Focused tests for ``ContextAssembler.assemble`` (R5 deliverable: context.py).

Covers the spec §4 priority order, the scene/memory/lead retrieval surfaces,
determinism, accounting self-consistency, and the integration-skip guard
(``tests/test_context_integration.py`` targets the REAL memory components and
activates once R3 lands). Budget math and drop-order pressure live in
``tests/test_context_budget.py``.
"""
from __future__ import annotations

import json

import pytest
from doubles import FakeStore, insert_lead
from test_context_doubles import (
    ChronicleDouble,
    FactDouble,
    MemoryDouble,
    NPCMemoryDouble,
    SagaDouble,
    chronicle_entry,
    npc_entry,
    npc_read,
    pinned_fact,
    saga_row,
    scene_dict,
)

from engine.config import EngineConfig
from engine.context import DEFAULT_RULESET_TEXT, ContextAssembler
from engine.models import (
    AssembledPrompt,
    CheckRequest,
    CheckResult,
    Delta,
    Intent,
    MechanicalOutcome,
)

NPC = "npc:1"
INPUT = "I search the yard for the missing shipment"


def _assembler(store=None, components=None, config=None) -> ContextAssembler:
    return ContextAssembler(store, config or EngineConfig(), components=components)


def _outcome(*, verdict="SUCCESS", roll=17, effects=()) -> MechanicalOutcome:
    return MechanicalOutcome(
        turn=5, kind="check", label="searches the yard",
        check=CheckResult(
            request=CheckRequest(skill="perception", dc=12), roll=roll,
            modifier=2, total=roll + 2, band="success",
        ),
        effects=list(effects), verdict_line=verdict,
    )


def _section(prompt, name: str) -> str:
    return next((text for section, text in prompt.sections if section == name), "")


def _lead_store() -> FakeStore:
    """A store with one lead tied to the present NPC and two decoys."""
    store = FakeStore()
    related = insert_lead(store, title="The missing shipment", stage="in_progress")
    store.update("leads", related, {"related_npc_ids": json.dumps([NPC])})
    insert_lead(store, title="The smuggler's ledger", stage="unheard")
    distant = insert_lead(store, title="A quarrel between farmers", stage="rumored")
    store.update("leads", distant, {
        "stage_history": json.dumps([
            {"stage": "rumored", "turn": 2, "trigger": "a rumor spread through the yard"},
        ]),
    })
    return store


def _rich_memory() -> MemoryDouble:
    return MemoryDouble(
        npc_memory=NPCMemoryDouble({
            NPC: [(npc_entry(npc_id=NPC, statement="marla remembers the promise"), 0.8)],
        }),
        chronicle=ChronicleDouble([
            chronicle_entry(turn_id=4, actor="player", action_summary="the player searched the yard"),
        ]),
        saga=SagaDouble([saga_row(text="The campaign opened in the yard.")]),
        facts=FactDouble([pinned_fact(statement="The player promised to find the missing shipment")]),
    )


def _assembled(store=None, components=None, config=None, window=32000):
    scene = scene_dict(npcs=[npc_read(npc_id=NPC, name="Marla")])
    return _assembler(store, components, config).assemble(
        turn=5, scene=scene, mechanical=_outcome(),
        intent=Intent(kind="action", text=INPUT), context_window=window,
    )


# --------------------------------------------------------------------------- #
# priority order, system block, rendering
# --------------------------------------------------------------------------- #

def test_sections_follow_the_spec_priority_order() -> None:
    prompt = _assembled(_lead_store(), _rich_memory())
    assert [name for name, _ in prompt.sections] == [
        "scene", "mechanics", "pinned_facts", "npc_memory", "leads",
        "chronicle", "saga",
    ]
    assert prompt.text == "\n\n".join(section for _, section in prompt.sections)


def test_system_block_is_the_static_cacheable_block() -> None:
    prompt = _assembled(None, _rich_memory())
    assert prompt.system == DEFAULT_RULESET_TEXT
    assert prompt.system not in prompt.text
    assert prompt.accounting["cacheable"] == ["system"]
    assert all(name != "system" for name, _ in prompt.sections)

    with_ruleset = _assembler(None, _rich_memory()).assemble(
        turn=5, scene=scene_dict(ruleset="  House rules: roll high.  ", npcs=[npc_read(npc_id=NPC)]),
        mechanical=None, intent=Intent(kind="exploration", text=INPUT), context_window=32000,
    )
    assert with_ruleset.system == "House rules: roll high."


def test_scene_state_renders_location_npcs_and_player() -> None:
    scene = scene_dict(
        location={"name": "The yard", "description_static": "A dusty training yard.",
                  "connections": ["gate", "hall"]},
        npcs=[
            npc_read(npc_id=NPC, name="Marla", mood={"valence": -0.4, "arousal": 0.6},
                     disposition=-20),
            npc_read(npc_id="npc:2", name="Old Hob", alive=False),
        ],
        player={"name": "Player", "stats": {"hp": 4, "max_hp": 10, "currency": 25},
                "inventory": [{"item_id": "rope", "qty": 2}],
                "status_effects": [{"status": "poisoned"}]},
    )
    prompt = _assembler(None, _rich_memory()).assemble(
        turn=5, scene=scene, mechanical=None,
        intent=Intent(kind="exploration", text=INPUT), context_window=32000,
    )
    body = _section(prompt, "scene")
    assert "# Scene" in body
    assert "Location: The yard — A dusty training yard." in body
    assert "Exits: gate, hall" in body
    assert "Present NPCs:" in body
    assert "- Marla (mood -0.40/+0.60; disposition -20)" in body
    assert "- Old Hob (dead)" in body
    assert "Player: Player — hp 4/10, currency 25" in body
    assert "Status: poisoned" in body
    assert "Carrying: rope x2" in body


def test_mechanics_outcome_renders_check_and_effects() -> None:
    prompt = _assembled(None, _rich_memory())
    body = _section(prompt, "mechanics")
    assert "Outcome: check — searches the yard" in body
    assert "Verdict: SUCCESS" in body
    assert "Check: perception — roll 17 +2 = 19 vs DC 12 → success" in body

    with_effects = _assembler(None, _rich_memory()).assemble(
        turn=5, scene=scene_dict(npcs=[npc_read(npc_id=NPC)]),
        mechanical=_outcome(effects=[
            Delta(kind="hp", target="player", data={"delta": -3}, reason="grazed"),
        ]),
        intent=Intent(kind="action", text=INPUT), context_window=32000,
    )
    assert "- hp on player: delta=-3 (grazed)" in _section(with_effects, "mechanics")


def test_missing_mechanical_outcome_still_renders_the_section() -> None:
    prompt = _assembler(None, _rich_memory()).assemble(
        turn=5, scene=scene_dict(npcs=[npc_read(npc_id=NPC)]), mechanical=None,
        intent=Intent(kind="exploration", text=INPUT), context_window=32000,
    )
    assert "No mechanical resolution this turn." in _section(prompt, "mechanics")


# --------------------------------------------------------------------------- #
# pinned facts (§3.6 + §4 item 4)
# --------------------------------------------------------------------------- #

def test_pinned_facts_are_gated_by_min_pin_relevance() -> None:
    relevant = pinned_fact(statement="The player promised to find the missing shipment", fact_id=1)
    unrelated = pinned_fact(statement="The moon rises over the northern hills", fact_id=2)
    prompt = _assembled(None, MemoryDouble(facts=FactDouble([relevant, unrelated])))
    body = _section(prompt, "pinned_facts")
    assert relevant.statement in body
    assert unrelated.statement not in body


def test_pin_gate_threshold_comes_from_config() -> None:
    config = EngineConfig()
    config.memory.min_pin_relevance = 0.9
    fact = pinned_fact(statement="The player promised to find the missing shipment")
    prompt = _assembled(None, MemoryDouble(facts=FactDouble([fact])), config)
    assert _section(prompt, "pinned_facts") == ""


def test_relevant_hint_can_extend_scene_context_and_supply_pins() -> None:
    gate_caller = pinned_fact(statement="The gate is watched at night")
    memory_pin = pinned_fact(statement="marla never speaks of the promise")
    prompt = _assembler(None, MemoryDouble(facts=FactDouble([memory_pin]))).assemble(
        turn=5, scene=scene_dict(ruleset="Rules.", npcs=[npc_read(npc_id=NPC, name="Marla")]),
        mechanical=None, intent=Intent(kind="exploration", text="I wait by the gate"),
        context_window=32000, relevant={"scene_context": ["the gate at night"], "pinned": [gate_caller]},
    )
    assert gate_caller.statement in _section(prompt, "pinned_facts")
    assert memory_pin.statement not in prompt.text


def test_unknown_relevant_keys_are_ignored() -> None:
    prompt = _assembler(None, _rich_memory()).assemble(
        turn=5, scene=scene_dict(npcs=[npc_read(npc_id=NPC)]), mechanical=None,
        intent=Intent(kind="exploration", text=INPUT), context_window=32000,
        relevant={"nonsense": object()},
    )
    assert "scene" in [name for name, _ in prompt.sections]


# --------------------------------------------------------------------------- #
# NPC memory scoping (§3.1)
# --------------------------------------------------------------------------- #

def test_npc_memory_is_scoped_to_present_npcs_only() -> None:
    memory = MemoryDouble(npc_memory=NPCMemoryDouble({
        NPC: [(npc_entry(npc_id=NPC, statement="marla remembers the promise", entry_id=1), 0.9)],
        "npc:2": [(npc_entry(npc_id="npc:2", statement="hob remembers the ale", entry_id=2), 0.8)],
        "npc:9": [(npc_entry(npc_id="npc:9", statement="the ferryman watches the far bank",
                             entry_id=3), 0.99)],
    }))
    prompt = _assembler(None, memory).assemble(
        turn=5,
        scene=scene_dict(npcs=[npc_read(npc_id=NPC, name="Marla"),
                               npc_read(npc_id="npc:2", name="Old Hob")]),
        mechanical=None, intent=Intent(kind="exploration", text=INPUT), context_window=32000,
    )
    assert memory.npc_memory.queried() == [NPC, "npc:2"]
    body = _section(prompt, "npc_memory")
    assert "- Marla: [observed] marla remembers the promise" in body
    assert "- Old Hob: [observed] hob remembers the ale" in body
    assert "the ferryman watches the far bank" not in prompt.text


def test_npc_memory_respects_top_k_and_salience_order() -> None:
    config = EngineConfig()
    config.memory.npc_top_k = 2
    memory = MemoryDouble(npc_memory=NPCMemoryDouble({NPC: [
        (npc_entry(npc_id=NPC, statement="top salience memory", entry_id=1), 0.9),
        (npc_entry(npc_id=NPC, statement="middle salience memory", entry_id=2), 0.5),
        (npc_entry(npc_id=NPC, statement="bottom salience memory", entry_id=3), 0.1),
    ]}))
    prompt = _assembled(None, memory, config)
    assert memory.npc_memory.calls[0]["k"] == 2
    body = _section(prompt, "npc_memory")
    assert "top salience memory" in body
    assert "middle salience memory" in body
    assert "bottom salience memory" not in body
    assert body.index("top salience memory") < body.index("middle salience memory")


def test_npcs_without_an_id_are_not_queried() -> None:
    memory = MemoryDouble(npc_memory=NPCMemoryDouble({NPC: []}))
    prompt = _assembler(None, memory).assemble(
        turn=5, scene=scene_dict(npcs=[{"name": "Anonymous"}]), mechanical=None,
        intent=Intent(kind="exploration", text=INPUT), context_window=32000,
    )
    assert memory.npc_memory.calls == []
    assert _section(prompt, "npc_memory") == ""


# --------------------------------------------------------------------------- #
# leads (§4 item 6)
# --------------------------------------------------------------------------- #

def test_leads_related_to_a_present_npc_are_injected() -> None:
    prompt = _assembled(_lead_store(), _rich_memory())
    body = _section(prompt, "leads")
    assert "- The missing shipment — stage: in_progress (related: npc:1)" in body
    assert "smuggler" not in body
    assert "quarrel" not in body


def test_leads_relevant_to_the_turn_render_with_the_latest_trigger() -> None:
    store = _lead_store()
    rumored = insert_lead(store, title="The missing shipment", stage="rumored")
    store.update("leads", rumored, {
        "stage_history": json.dumps([
            {"stage": "unheard", "turn": 1, "trigger": "the player never heard of it"},
            {"stage": "rumored", "turn": 2, "trigger": "marla let slip about the missing shipment"},
        ]),
    })
    prompt = _assembled(store, _rich_memory())
    body = _section(prompt, "leads")
    assert "stage: rumored" in body
    assert "latest: marla let slip about the missing shipment" in body


def test_store_without_leads_table_contributes_no_lead_section() -> None:
    prompt = _assembled(None, _rich_memory())
    assert _section(prompt, "leads") == ""


# --------------------------------------------------------------------------- #
# retrieval arguments, determinism, accounting
# --------------------------------------------------------------------------- #

def test_memory_calls_receive_the_turn_and_scene_context() -> None:
    memory = _rich_memory()
    _assembler(None, memory).assemble(
        turn=7, scene=scene_dict(npcs=[npc_read(npc_id=NPC, name="Marla")]),
        mechanical=_outcome(), intent=Intent(kind="action", text=INPUT),
        context_window=32000, relevant={"scene_context": ["the locked crate"]},
    )
    call = memory.npc_memory.calls[0]
    assert call["turn"] == 7
    assert call["scene_context"][0] == INPUT
    assert "Marla" in call["scene_context"]
    assert "The yard" in call["scene_context"]
    assert "the locked crate" in call["scene_context"]
    assert memory.saga.calls[0]["turn"] == 7
    assert memory.saga.calls[0]["scene_context"] == call["scene_context"]
    assert memory.chronicle.calls == [EngineConfig().memory.chronicle_window]
    assert memory.facts.calls == 1


def test_identical_inputs_produce_identical_output() -> None:
    def run() -> AssembledPrompt:
        return _assembled(_lead_store(), _rich_memory())

    first, second = run(), run()
    assert first.text == second.text
    assert first.system == second.system
    assert first.sections == second.sections
    assert first.accounting == second.accounting

    assembler = _assembler(_lead_store(), _rich_memory())
    scene = scene_dict(npcs=[npc_read(npc_id=NPC, name="Marla")])
    kwargs = {"turn": 5, "scene": scene, "mechanical": _outcome(),
              "intent": Intent(kind="action", text=INPUT), "context_window": 32000}
    assert assembler.assemble(**kwargs) == assembler.assemble(**kwargs)


def test_accounting_is_self_consistent() -> None:
    assembler = _assembler(_lead_store(), _rich_memory())
    prompt = assembler.assemble(
        turn=5, scene=scene_dict(npcs=[npc_read(npc_id=NPC, name="Marla")]),
        mechanical=_outcome(), intent=Intent(kind="action", text=INPUT),
        context_window=32000,
    )
    accounting = prompt.accounting
    assert list(accounting["sections"]) == [
        "system", "content_policy", "scene", "mechanics", "pinned_facts",
        "npc_memory", "leads", "chronicle", "saga",
    ]
    assert accounting["sections"]["content_policy"] == 0  # no policy in this scene
    assert accounting["sections"]["system"] == assembler.estimate_tokens(prompt.system)
    for name, text in prompt.sections:
        assert accounting["sections"][name] == assembler.estimate_tokens(text)
    assert accounting["budget"]["used"] == sum(accounting["sections"].values())
    assert accounting["budget"]["remaining"] == (
        accounting["budget"]["total"] - accounting["budget"]["used"]
    )
    assert accounting["budget"]["used"] <= accounting["budget"]["total"]
    assert accounting["budget"]["over_budget"] is False
    assert accounting["dropped"] == []
    assert accounting["compensation"] == {"continuity": 0, "memory": 0}


def test_ledger_and_moods_are_never_touched() -> None:
    memory = MemoryDouble()
    with pytest.raises(AssertionError):
        memory.ledger.currents(npc_id=NPC, player_id="player", turn=1)
    with pytest.raises(AssertionError):
        memory.moods.current(npc_id=NPC, turn=1)
    assert _assembled(None, memory) is not None


# --------------------------------------------------------------------------- #
# integration guard (R3 lands in parallel)
# --------------------------------------------------------------------------- #

def test_integration_guard_matches_the_memory_state() -> None:
    from test_context_integration import _memory_ok

    from engine.memory import MemoryBundle
    from engine.store import Store

    store = Store(":memory:")
    try:
        MemoryBundle.build(store).chronicle.tail()
    except NotImplementedError:
        assert _memory_ok() is False
    else:
        assert _memory_ok() is True
    finally:
        store.close()
