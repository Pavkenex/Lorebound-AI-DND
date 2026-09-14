"""Contradiction-heuristic tests (R2 deliverable #2: validate.py).

The heuristic is deliberately conservative: a statement is only a conflict when
it is *about the same thing* (similarity + content-token overlap gates) AND
carries a contradiction marker (differing anchored number, a negation flip, or
an antonym pair). Pinned facts get lower gates, i.e. they are protected harder.
"""
from __future__ import annotations

import pytest
from doubles import FakeStore, insert_fact

from engine.config import EngineConfig
from engine.similarity import LexicalSimilarity
from engine.validate import (
    ConflictPolicy,
    Validator,
    conflict_reason,
)


def _setup() -> tuple[FakeStore, Validator]:
    store = FakeStore()
    return store, Validator(store, EngineConfig())


# --------------------------------------------------------------------------- #
# check_contradiction surface
# --------------------------------------------------------------------------- #

def test_contradiction_returns_ids_and_details_agree() -> None:
    store, validator = _setup()
    insert_fact(store, statement="Marla is dead", pinned=True, row_id=5)
    insert_fact(store, statement="The mill pond freezes in winter", row_id=6)
    assert validator.check_contradiction("Marla is alive") == [5]
    details = validator.contradiction_details("Marla is alive")
    assert details == [(5, "Marla is dead", "antonym pair 'alive'/'dead'")]


def test_unrelated_statements_are_not_conflicts() -> None:
    store, validator = _setup()
    insert_fact(store, statement="The north gate is locked at night", pinned=True)
    assert validator.check_contradiction("Marla is alive") == []
    assert validator.check_contradiction("The mill pond freezes in winter") == []


def test_exclude_fact_id_skips_that_fact() -> None:
    store, validator = _setup()
    row_id = insert_fact(store, statement="Marla is dead", pinned=True)
    assert validator.check_contradiction("Marla is alive") == [row_id]
    assert validator.check_contradiction("Marla is alive", exclude_fact_id=row_id) == []


def test_empty_store_has_no_conflicts() -> None:
    _store, validator = _setup()
    assert validator.check_contradiction("Marla is alive") == []


def test_validator_without_a_store_has_no_conflicts() -> None:
    assert Validator(None, EngineConfig()).check_contradiction("anything") == []


# --------------------------------------------------------------------------- #
# Similarity / overlap gates
# --------------------------------------------------------------------------- #

def test_shared_subject_alone_is_not_a_conflict() -> None:
    reason = conflict_reason(
        "Marla is alive and well in Ravenford",
        "Marla guards the north gate",
        pinned=False,
    )
    assert reason is None


def test_near_duplicate_is_not_a_conflict() -> None:
    reason = conflict_reason(
        "The silver bell rings only at dawn",
        "The silver bell rings only at dawn",
        pinned=True,
    )
    assert reason is None


def test_similar_but_unrelated_claim_is_not_a_conflict() -> None:
    # same subject, same shape, no marker: not our business to call it a conflict
    reason = conflict_reason(
        "The north gate is barred after dark",
        "The north gate is painted green after the harvest",
        pinned=True,
    )
    assert reason is None


def test_marker_without_subject_overlap_is_not_a_conflict() -> None:
    reason = conflict_reason(
        "Marla is alive",
        "The drawbridge is dead weight for the portcullis",
        pinned=True,
    )
    assert reason is None


def test_pinned_facts_are_protected_more_aggressively() -> None:
    statement = "the vault door is locked on tuesday evening"
    existing = "the vault door is unlocked in the morning light"
    assert conflict_reason(statement, existing, pinned=True) is not None
    assert conflict_reason(statement, existing, pinned=False) is None


def test_policy_knobs_are_honoured() -> None:
    statement, existing = "the vault door is locked on tuesday evening", "the vault door is unlocked in the morning light"
    strict = ConflictPolicy(overlap_floor=0.2)
    lenient = ConflictPolicy(overlap_floor=0.9)
    assert conflict_reason(statement, existing, pinned=False, policy=strict) is not None
    assert conflict_reason(statement, existing, pinned=False, policy=lenient) is None


def test_policy_is_used_by_the_validator() -> None:
    store, validator = _setup()
    validator.policy = ConflictPolicy(overlap_floor=0.2)
    insert_fact(store, statement="the vault door is unlocked in the morning light", row_id=1)
    assert validator.check_contradiction("the vault door is locked on tuesday evening") == [1]


def test_custom_similarity_scorer_is_used() -> None:
    class _NeverSimilar(LexicalSimilarity):
        def score(self, a: str, b: str) -> float:
            return 0.0

    store, validator = _setup()
    validator.sim = _NeverSimilar()
    insert_fact(store, statement="Marla is dead", pinned=True, row_id=1)
    assert validator.check_contradiction("Marla is alive") == []


# --------------------------------------------------------------------------- #
# Markers
# --------------------------------------------------------------------------- #

@pytest.mark.parametrize(("statement", "existing", "expected_fragment"), [
    ("Marla is alive", "Marla is dead", "antonym"),
    ("the north gate is unlocked after dark", "the north gate is locked after dark", "antonym"),
    ("the ruined bridge of the old road is intact", "the ruined bridge of the old road is destroyed", "antonym"),
    ("the guard of the watchtower is not at his post", "the guard of the watchtower is at his post", "negation"),
    ("the frozen mill pond of the valley never freezes", "the frozen mill pond of the valley freezes", "negation"),
    ("the brass ledger of the counting house lists 12 debts", "the brass ledger of the counting house lists 4 debts", "numeric"),
    ("the brass ledger of the counting house lists twelve debts", "the brass ledger of the counting house lists 4 debts", "numeric"),
])
def test_each_marker_fires(statement: str, existing: str, expected_fragment: str) -> None:
    reason = conflict_reason(statement, existing, pinned=True)
    assert reason is not None and expected_fragment in reason


def test_negation_flip_needs_one_sided_negation() -> None:
    assert conflict_reason("the bridge is not safe", "the bridge is safe", pinned=False) is not None
    assert conflict_reason("the bridge is not safe", "the bridge is not safe", pinned=False) is None


def test_numeric_conflict_needs_the_same_anchor() -> None:
    assert conflict_reason("Marla has 3 guards", "Marla has 5 guards", pinned=False) is not None
    assert conflict_reason("Marla has 3 guards", "the pond holds 5 fish", pinned=False) is None


def test_same_number_is_not_a_conflict() -> None:
    assert conflict_reason("Marla has 3 guards", "Marla keeps 3 guards", pinned=False) is None


def test_antonym_pairs_are_directional() -> None:
    assert conflict_reason("Marla is dead", "Marla is alive", pinned=False) is not None
    assert conflict_reason("Marla is alive", "Marla is dead", pinned=False) is not None
