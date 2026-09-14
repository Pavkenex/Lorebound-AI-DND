"""Focused tests for ``resolve_action`` (R2 deliverable #1: Intent -> outcome).

Covers the type mapping, store-backed eligibility, the effects hook, and
determinism.
"""
from __future__ import annotations

from doubles import FakeStore, ScriptedRng, insert_character, insert_npc

from engine.models import Delta, Intent
from engine.resolve import (
    DEFAULT_ATTACK_DC,
    DEFAULT_CHECK_DC,
    SeededRng,
    resolve_action,
)


def _world(**npc_overrides) -> FakeStore:
    store = FakeStore()
    insert_character(store, location_id="yard")
    npc_kwargs = {"name": "Marla", "location_id": "yard", "row_id": 3}
    npc_kwargs.update(npc_overrides)
    insert_npc(store, **npc_kwargs)
    return store


# --------------------------------------------------------------------------- #
# Intent -> mechanical work
# --------------------------------------------------------------------------- #

def test_skill_intent_resolves_a_check() -> None:
    outcome = resolve_action(
        Intent(kind="exploration", text="I search the crates", skill="investigation"),
        rng=ScriptedRng([14]),
        turn=4,
    )
    assert outcome.turn == 4
    assert outcome.kind == "check"
    assert outcome.label == "investigation check"
    assert outcome.check is not None
    assert outcome.check.request is not None
    assert outcome.check.request.dc == DEFAULT_CHECK_DC
    assert outcome.verdict_line == outcome.check.verdict_line
    assert any("default dc" in note for note in outcome.notes)


def test_explicit_dc_is_used_and_noted_absent() -> None:
    outcome = resolve_action(
        Intent(kind="action", text="I pick the lock", skill="lockpicking"),
        rng=ScriptedRng([11]),
        dc=15,
    )
    assert outcome.check is not None
    assert outcome.check.request is not None
    assert outcome.check.request.dc == 15
    assert not any("default dc" in note for note in outcome.notes)


def test_action_with_target_and_no_skill_is_an_attack() -> None:
    outcome = resolve_action(
        Intent(kind="action", text="I swing at Marla", target="npc:3"),
        rng=ScriptedRng([13]),
        store=_world(),
    )
    assert outcome.kind == "attack"
    assert outcome.label == "attack on npc:3"
    assert outcome.check is not None
    assert outcome.check.request is not None
    assert outcome.check.request.dc == DEFAULT_ATTACK_DC
    assert any("present and alive" in note for note in outcome.notes)


def test_action_with_no_skill_or_target_is_a_flat_check() -> None:
    outcome = resolve_action(Intent(kind="action", text="I climb the wall"), rng=ScriptedRng([16]))
    assert outcome.kind == "check"
    assert outcome.label == "flat check"
    assert outcome.check is not None
    assert outcome.check.modifier == 0


def test_dialogue_without_skill_has_no_mechanics() -> None:
    outcome = resolve_action(Intent(kind="dialogue", text="I greet the innkeeper"), rng=ScriptedRng([]))
    assert outcome.kind == "none"
    assert outcome.check is None
    assert outcome.effects == []


def test_meta_intents_never_roll_even_with_a_skill() -> None:
    outcome = resolve_action(
        Intent(kind="meta", text="save the game", skill="arcana"), rng=ScriptedRng([])
    )
    assert outcome.kind == "none"
    assert outcome.label == "meta"


def test_attack_without_a_store_still_resolves() -> None:
    outcome = resolve_action(
        Intent(kind="action", text="I jab at the shape", target="npc:3"), rng=ScriptedRng([9])
    )
    assert outcome.kind == "attack"
    assert outcome.check is not None


# --------------------------------------------------------------------------- #
# Store-backed eligibility
# --------------------------------------------------------------------------- #

def test_dead_target_blocks_the_action_without_rolling() -> None:
    store = _world(alive=False)
    rng = ScriptedRng([20])
    outcome = resolve_action(
        Intent(kind="action", text="I attack the corpse", target="npc:3"), rng=rng, store=store
    )
    assert outcome.kind == "blocked"
    assert outcome.check is None
    assert "dead" in outcome.verdict_line
    assert rng.calls == []  # no dice were rolled
    assert outcome.effects == []


def test_dead_target_blocks_social_checks_too() -> None:
    store = _world(alive=False)
    outcome = resolve_action(
        Intent(kind="action", text="I ask her about the shipment", skill="persuasion", target="Marla"),
        rng=ScriptedRng([]),
        store=store,
    )
    assert outcome.kind == "blocked"


def test_absent_target_blocks_the_action() -> None:
    store = _world(location_id="harbor")
    outcome = resolve_action(
        Intent(kind="action", text="I attack", target="npc:3"), rng=ScriptedRng([]), store=store
    )
    assert outcome.kind == "blocked"
    assert "not present" in outcome.verdict_line


def test_unknown_target_is_not_a_hard_block() -> None:
    outcome = resolve_action(
        Intent(kind="action", text="I kick the door", target="the north door"),
        rng=ScriptedRng([12]),
        store=_world(),
    )
    assert outcome.kind == "attack"
    assert any("not a known NPC" in note for note in outcome.notes)


def test_target_resolves_by_bare_id_and_by_name() -> None:
    store = _world()
    by_id = resolve_action(
        Intent(kind="action", text="strike", target="3"), rng=ScriptedRng([12]), store=store
    )
    by_name = resolve_action(
        Intent(kind="action", text="strike", target="Marla"), rng=ScriptedRng([12]), store=store
    )
    assert by_id.kind == by_name.kind == "attack"


def test_presence_gate_skips_when_locations_are_unset() -> None:
    store = FakeStore()
    insert_character(store, location_id="")
    insert_npc(store, name="Marla", location_id="harbor", row_id=3)
    outcome = resolve_action(
        Intent(kind="action", text="strike", target="npc:3"), rng=ScriptedRng([12]), store=store
    )
    assert outcome.kind == "attack"


# --------------------------------------------------------------------------- #
# Effects hook + determinism
# --------------------------------------------------------------------------- #

def test_effects_are_empty_by_default() -> None:
    outcome = resolve_action(
        Intent(kind="action", text="I attack", skill="athletics"), rng=ScriptedRng([15])
    )
    assert outcome.effects == []


def test_effect_rules_can_attach_suggested_deltas() -> None:
    def rule(intent: Intent, outcome) -> list[Delta]:
        if outcome.check is not None and outcome.check.band in {"failure", "critical_failure"}:
            return [Delta(kind="hp", target="player", data={"delta": -1, "cause": "strain"})]
        return []

    outcome = resolve_action(
        Intent(kind="action", text="I heave the boulder", skill="athletics"),
        rng=ScriptedRng([2]),
        effect_rules=[rule],
    )
    assert outcome.check is not None
    assert outcome.check.band == "failure"
    assert len(outcome.effects) == 1
    assert outcome.effects[0].kind == "hp"
    assert outcome.effects[0].data["delta"] == -1


def test_effect_rules_can_return_nothing() -> None:
    outcome = resolve_action(
        Intent(kind="action", text="I attack", skill="athletics"),
        rng=ScriptedRng([15]),
        effect_rules=[lambda intent, outcome: []],
    )
    assert outcome.effects == []


def test_same_seed_reproduces_the_same_outcome() -> None:
    intent = Intent(kind="action", text="I attack Marla", target="npc:3")
    first = resolve_action(intent, rng=SeededRng("turn-9"), store=_world(), turn=9)
    second = resolve_action(intent, rng=SeededRng("turn-9"), store=_world(), turn=9)
    assert first == second


def test_different_seeds_produce_different_rolls_across_a_turn_window() -> None:
    rolls = {
        resolve_action(Intent(kind="action", text="strike"), rng=SeededRng(seed)).check.roll
        for seed in range(6)
    }
    assert len(rolls) > 1
