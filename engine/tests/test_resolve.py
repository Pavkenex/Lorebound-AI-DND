"""Focused tests for dice + check resolution (R2 deliverable #1: resolve.py).

Exact-value dice tests use ``ScriptedRng`` (a fixed sequence) instead of seed
hunting; ``SeededRng`` is exercised separately for determinism.
"""
from __future__ import annotations

import pytest
from doubles import ScriptedRng

from engine.models import CheckRequest, OutcomeBand
from engine.resolve import (
    DEFAULT_SKILL_MODIFIERS,
    SeededRng,
    canonical_expression,
    parse_expression,
    resolve_check,
    roll_expression,
)


def _req(skill: str = "athletics", dc: int = 10, **kwargs) -> CheckRequest:
    return CheckRequest(skill=skill, dc=dc, **kwargs)


# --------------------------------------------------------------------------- #
# Expression parsing
# --------------------------------------------------------------------------- #

@pytest.mark.parametrize(("expr", "expected"), [
    ("2d6+3", (2, 6, 3, None)),
    ("1d20", (1, 20, 0, None)),
    ("d20", (1, 20, 0, None)),
    ("3d8-2", (3, 8, -2, None)),
    ("2d6 + 3", (2, 6, 3, None)),
    ("2d6 - 2", (2, 6, -2, None)),
    ("d20 adv", (1, 20, 0, "adv")),
    ("1d20+3 DIS", (1, 20, 3, "dis")),
    ("advantage 2d6", (2, 6, 0, "adv")),
    ("1d20adv", (1, 20, 0, "adv")),
    ("1d20-2dis", (1, 20, -2, "dis")),
    ("100d1000+1000", (100, 1000, 1000, None)),
])
def test_parse_expression_accepts(expr: str, expected: tuple[int, int, int, str | None]) -> None:
    assert parse_expression(expr) == expected


@pytest.mark.parametrize("expr", [
    "", "   ", "d", "2d", "d0", "1d1", "0d6", "101d6", "1d1001", "2d6+", "2d6++3",
    "2d6 adv dis", "1d20 kh1", "1d20 keep-highest", "2d6+2000", "2d6 3", "adv",
    "2d6+3-2", "1d20 dis dis", "two-d6",
])
def test_parse_expression_rejects(expr: str) -> None:
    with pytest.raises(ValueError):
        parse_expression(expr)


def test_roll_expression_exact_values() -> None:
    result = roll_expression("2d6+3", ScriptedRng([4, 2]))
    assert result.rolls == [4, 2]
    assert result.dropped == []
    assert result.modifier == 3
    assert result.total == 9
    assert result.expression == "2d6+3"


def test_roll_expression_normalizes_notation() -> None:
    assert roll_expression("2d6 + 3", ScriptedRng([1, 1])).expression == "2d6+3"
    assert roll_expression("d20 ADV", ScriptedRng([3, 9])).expression == "1d20 adv"
    assert canonical_expression(3, 8, -2, "dis") == "3d8-2 dis"


def test_roll_expression_advantage_keeps_best() -> None:
    result = roll_expression("1d20 adv", ScriptedRng([4, 17]))
    assert (result.rolls, result.dropped) == ([17], [4])
    assert result.total == 17


def test_roll_expression_disadvantage_keeps_worst() -> None:
    result = roll_expression("1d20 dis", ScriptedRng([4, 17]))
    assert (result.rolls, result.dropped) == ([4], [17])
    assert result.total == 4


def test_roll_expression_advantage_tie_keeps_first_pool() -> None:
    rng = ScriptedRng([10, 10])
    result = roll_expression("1d20 adv", rng)
    assert result.rolls == [10]
    assert result.dropped == [10]
    assert [a for a, _b in rng.calls] == [1, 1]


def test_roll_expression_multi_dice_advantage_keeps_better_pool() -> None:
    result = roll_expression("2d6 adv", ScriptedRng([1, 1, 6, 6]))
    assert (result.rolls, result.dropped, result.total) == ([6, 6], [1, 1], 12)
    result = roll_expression("2d6+1 adv", ScriptedRng([1, 1, 6, 6]))
    assert result.total == 13
    assert result.expression == "2d6+1 adv"


def test_roll_expression_with_seeded_rng_is_deterministic() -> None:
    first = [roll_expression("3d6+2", SeededRng("campaign-1")) for _ in range(3)]
    second = [roll_expression("3d6+2", SeededRng("campaign-1")) for _ in range(3)]
    assert [r.rolls for r in first] == [r.rolls for r in second]
    for result in first:
        assert len(result.rolls) == 3
        assert all(1 <= die <= 6 for die in result.rolls)
        assert result.total == sum(result.rolls) + 2


# --------------------------------------------------------------------------- #
# Checks + outcome bands
# --------------------------------------------------------------------------- #

@pytest.mark.parametrize(("roll", "dc", "modifier", "expected"), [
    (7, 10, 0, OutcomeBand.FAILURE),            # margin -3
    (9, 10, 0, OutcomeBand.FAILURE),            # margin -1  (boundary)
    (10, 10, 0, OutcomeBand.SUCCESS_AT_COST),   # margin  0  (boundary)
    (11, 10, 0, OutcomeBand.SUCCESS_AT_COST),   # margin  1
    (12, 10, 0, OutcomeBand.SUCCESS_AT_COST),   # margin  2  (boundary)
    (13, 10, 0, OutcomeBand.SUCCESS),           # margin  3  (boundary)
    (19, 10, 0, OutcomeBand.SUCCESS),
    (20, 30, -9, OutcomeBand.CRITICAL),         # nat-20 wins with margin -19
    (1, 1, 5, OutcomeBand.CRITICAL_FAILURE),    # nat-1 wins with margin +5
])
def test_resolve_check_bands(roll: int, dc: int, modifier: int, expected: OutcomeBand) -> None:
    result = resolve_check(_req(dc=dc), rng=ScriptedRng([roll]), modifier=modifier)
    assert result.band == expected.value
    assert result.roll == roll
    assert result.total == roll + modifier
    assert result.modifier == modifier


def test_resolve_check_natural_twenty_is_unconditional() -> None:
    result = resolve_check(_req(dc=99), rng=ScriptedRng([20]), modifier=0)
    assert result.band == OutcomeBand.CRITICAL.value
    assert result.request is not None
    assert result.total - result.request.dc < -2  # deeply negative margin, still CRITICAL


def test_resolve_check_uses_skill_table() -> None:
    result = resolve_check(_req(skill="athletics", dc=10), rng=ScriptedRng([8]))
    assert result.modifier == DEFAULT_SKILL_MODIFIERS["athletics"] == 2
    assert result.total == 10
    assert result.band == OutcomeBand.SUCCESS_AT_COST.value


def test_resolve_check_attribute_fallback_and_unknown_skill() -> None:
    assert resolve_check(_req(skill="", attribute="stealth"), rng=ScriptedRng([5])).modifier == 2
    assert resolve_check(_req(skill="basket-weaving"), rng=ScriptedRng([5])).modifier == 0


def test_resolve_check_explicit_modifier_overrides_table() -> None:
    result = resolve_check(_req(skill="athletics"), rng=ScriptedRng([5]), modifier=-3)
    assert result.modifier == -3
    assert result.total == 2


def test_resolve_check_advantage_and_disadvantage() -> None:
    advantage = resolve_check(_req(advantage=True), rng=ScriptedRng([3, 18]))
    disadvantage = resolve_check(_req(disadvantage=True), rng=ScriptedRng([3, 18]))
    assert advantage.roll == 18
    assert disadvantage.roll == 3


def test_resolve_check_advantage_plus_disadvantage_cancels() -> None:
    rng = ScriptedRng([12])
    result = resolve_check(_req(advantage=True, disadvantage=True), rng=rng)
    assert result.roll == 12
    assert len(rng.calls) == 1
    assert "cancel" in result.verdict_line.lower()


def test_resolve_check_verdict_line_is_single_factual_line() -> None:
    lines = {
        band.value: resolve_check(_req(dc=10), rng=ScriptedRng([roll]), modifier=modifier).verdict_line
        for band, (roll, modifier) in {
            OutcomeBand.CRITICAL: (20, 0),
            OutcomeBand.CRITICAL_FAILURE: (1, 0),
            OutcomeBand.FAILURE: (5, 0),
            OutcomeBand.SUCCESS_AT_COST: (12, 0),
            OutcomeBand.SUCCESS: (15, 0),
        }.items()
    }
    assert all(line and "\n" not in line for line in lines.values())
    assert "CRITICAL SUCCESS" in lines[OutcomeBand.CRITICAL.value]
    assert "CRITICAL FAILURE" in lines[OutcomeBand.CRITICAL_FAILURE.value]
    assert "SUCCESS AT A COST" in lines[OutcomeBand.SUCCESS_AT_COST.value]
    assert "FAILURE" in lines[OutcomeBand.FAILURE.value]
    assert "does not succeed" in lines[OutcomeBand.FAILURE.value]
    assert "SUCCESS" in lines[OutcomeBand.SUCCESS.value]
    assert "margin 2" in lines[OutcomeBand.SUCCESS_AT_COST.value]


def test_resolve_check_records_the_request() -> None:
    req = _req(skill="persuasion", dc=14, why="talk down the guard")
    result = resolve_check(req, rng=ScriptedRng([17]))
    assert result.request is req
    assert result.band == OutcomeBand.SUCCESS.value


def test_resolve_check_deterministic_with_seed() -> None:
    req = _req(skill="insight", dc=12, advantage=True)
    first = resolve_check(req, rng=SeededRng("turn-7"))
    second = resolve_check(req, rng=SeededRng("turn-7"))
    assert (first.roll, first.total, first.band) == (second.roll, second.total, second.band)
