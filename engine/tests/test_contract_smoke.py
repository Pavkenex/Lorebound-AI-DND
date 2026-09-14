"""Scaffold smoke test: modules import and the frozen surface exists.

This is the build's "hello, health" check — it must stay green in every
worker's tree (it pins the contracts, not the implementations).
"""
from __future__ import annotations

import importlib
import pathlib

ROOT = pathlib.Path(__file__).resolve().parents[1]
SRC = ROOT / "src"


def test_modules_import() -> None:
    for mod in [
        "engine",
        "engine.models",
        "engine.config",
        "engine.similarity",
        "engine.resolve",
        "engine.validate",
        "engine.memory",
        "engine.context",
        "engine.pipeline",
        "engine.play",
        "engine.cli",
        "engine.providers",
        "engine.providers.jsonproto",
    ]:
        importlib.import_module(mod)


def test_evals_import() -> None:
    importlib.import_module("evals.harness")


def test_store_surface() -> None:
    from engine.store import Store, connect

    assert callable(connect)
    for name in [
        "insert", "get", "find", "find_one", "update", "upsert", "delete",
        "count", "sql", "exec_sql", "transaction", "close",
    ]:
        assert callable(getattr(Store, name)), f"Store.{name} missing"


def test_schema_has_tables() -> None:
    sql = (SRC / "engine" / "store" / "schema.sql").read_text()
    for table in [
        "world", "locations", "characters", "npcs", "leads", "world_facts",
        "turn_log", "npc_memory", "chronicle", "relationship_ledger", "moods",
        "saga_levels", "telemetry", "meta", "provider_caps",
    ]:
        assert f"CREATE TABLE IF NOT EXISTS {table}" in sql, f"missing table {table}"


def test_delta_contract() -> None:
    from engine.models import DELTA_KINDS, DELTA_REQUIRED, Delta, ProposalSet

    assert DELTA_KINDS == set(DELTA_REQUIRED)
    d = Delta(kind="mood", target="npc:1", data={"valence_delta": -0.2, "arousal_delta": 0.1})
    ps = ProposalSet(narration="hi", deltas=[d])
    assert ps.deltas[0].data["valence_delta"] == -0.2


def test_row_helpers_roundtrip() -> None:
    from engine.models import NPC, from_row, to_row

    npc = NPC(id=5, name="Marla", personality={"tone": "warm"}, alive=False)
    row = to_row(npc)
    assert "id" not in row
    assert row["alive"] == 0
    assert row["personality"] == '{"tone": "warm"}'
    back = from_row(NPC, {"id": 5, **row})
    assert back.name == "Marla"
    assert back.personality == {"tone": "warm"}
    assert back.alive is False


def test_similarity() -> None:
    from engine.similarity import LexicalSimilarity

    sim = LexicalSimilarity()
    assert sim.score("the promise of gold", "gold promise") > 0.3
    assert sim.score("cats", "quantum") == 0.0
    assert 0.0 <= sim.score("a broken vow", "the vow was broken") <= 1.0


def test_seeded_rng_deterministic() -> None:
    from engine.resolve import SeededRng

    a = [SeededRng("seed-1").randint(1, 20) for _ in range(1)]
    b = [SeededRng("seed-1").randint(1, 20) for _ in range(1)]
    assert a == b


def test_lead_transitions_contract() -> None:
    from engine.validate import LEAD_TRANSITIONS

    assert "resolved" in LEAD_TRANSITIONS
    assert LEAD_TRANSITIONS["resolved"] == frozenset()
    assert "resolved" not in LEAD_TRANSITIONS["unheard"]
