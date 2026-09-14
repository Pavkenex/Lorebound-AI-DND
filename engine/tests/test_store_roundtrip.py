"""All 15 schema tables round-trip through Store + models.to_row/from_row.

Pins the §2 data contract: every table accepts a real dataclass row dict, the
raw column dict comes back with JSON-encoded text / 0-1 bool coding, and
``models.from_row`` decodes it back to an equal dataclass instance.
"""
from __future__ import annotations

import dataclasses
import json
import sqlite3

import pytest

from engine import models
from engine.store import Store

# --------------------------------------------------------------------------- #
# table inventory (frozen by schema.sql)
# --------------------------------------------------------------------------- #

TABLE_COLUMNS: dict[str, set[str]] = {
    "world": {"id", "seed", "day", "hour", "minute", "active_scene_id", "created_at"},
    "locations": {"id", "name", "description_static", "connections", "flags"},
    "characters": {
        "id", "name", "stats", "inventory", "location_id", "status_effects",
        "known_facts", "journal",
    },
    "npcs": {"id", "name", "personality", "disposition_base", "location_id", "alive", "schedule"},
    "leads": {
        "id", "title", "stage", "stage_history", "known_by", "related_npc_ids",
        "related_fact_ids",
    },
    "world_facts": {
        "id", "statement", "pinned", "established_turn", "source", "tags", "contradicts",
    },
    "turn_log": {
        "id", "turn", "actor", "raw_action", "mechanical_resolution", "narration_text",
        "state_deltas_applied", "created_at",
    },
    "npc_memory": {
        "id", "npc_id", "turn_established", "statement", "type", "sentiment", "decay_rate",
        "reinforced_count", "last_referenced_turn",
    },
    "chronicle": {
        "id", "turn_id", "actor", "action_summary", "mechanical_result",
        "consequence_oneliner", "verbatim_text",
    },
    "relationship_ledger": {
        "id", "npc_id", "player_id", "turn", "delta", "reason", "category", "decay_class",
    },
    "moods": {
        "id", "npc_id", "valence", "arousal", "baseline_valence", "baseline_arousal",
        "last_updated_turn",
    },
    "saga_levels": {"id", "level", "scope_id", "text", "created_turn", "references"},
    "telemetry": {
        "id", "turn", "provider", "model", "prompt_tokens", "completion_tokens",
        "budget_alloc", "dropped", "notes",
    },
    "meta": {"key", "value"},
    "provider_caps": {"provider_key", "caps", "probed_at"},
}


@pytest.fixture()
def store(tmp_path):
    s = Store(tmp_path / "roundtrip.sqlite")
    try:
        yield s
    finally:
        s.close()


def test_schema_sql_applies_standalone_and_reapplies_cleanly():
    """Regression guard: schema.sql must parse on its own (it is the data contract)."""
    from engine.store import SCHEMA_PATH

    raw = SCHEMA_PATH.read_text(encoding="utf-8")
    conn = sqlite3.connect(":memory:")
    try:
        conn.executescript(raw)
        conn.executescript(raw)  # idempotent
        tables = {
            r[0] for r in conn.execute("SELECT name FROM sqlite_master WHERE type = 'table'")
        }
    finally:
        conn.close()
    assert set(TABLE_COLUMNS) <= tables


def test_exactly_the_15_contract_tables_exist(store):
    found = {
        r["name"]
        for r in store.sql("SELECT name FROM sqlite_master WHERE type = 'table'")
        if not r["name"].startswith("sqlite_")
    }
    assert found == set(TABLE_COLUMNS)
    for table, columns in TABLE_COLUMNS.items():
        info = store.sql(f"PRAGMA table_info({table})")
        assert {r["name"] for r in info} == columns, f"column drift in {table}"
        assert store.count(table) == 0


# --------------------------------------------------------------------------- #
# dataclass round-trips
# --------------------------------------------------------------------------- #

ENTITY_CASES: list[tuple[str, type, object]] = [
    (
        "world",
        models.World,
        models.World(seed="ravenford-7", day=2, hour=9, minute=30,
                     active_scene_id="scene-market", created_at=1_700_000_001),
    ),
    (
        "locations",
        models.Location,
        models.Location(
            name="Miller's Rest",
            description_static="A low stone mill above the ford.",
            connections=[{"to": "loc-2", "kind": "door", "locked": False}],
            flags={"lit": True, "visited": False},
        ),
    ),
    (
        "characters",
        models.Character,
        models.Character(
            name="The Wanderer",
            stats={"might": 3, "wits": 2, "grace": 1},
            inventory=[{"item_id": "sword", "qty": 1, "flags": {"worn": True}}],
            location_id="loc-1",
            status_effects=["wounded"],
            known_facts=["fact-1", "fact-2"],
            journal=["day one: the ford was cold"],
        ),
    ),
    (
        "npcs",
        models.NPC,
        models.NPC(
            name="Marla",
            personality={"traits": ["guarded"], "tone": "dry", "baseline_valence": -0.1},
            disposition_base=15.5,
            location_id="loc-1",
            alive=False,
            schedule={"morning": "kitchen", "night": "locked lodge"},
        ),
    ),
    (
        "leads",
        models.Lead,
        models.Lead(
            title="The Missing Shipment",
            stage=models.LeadStage.IN_PROGRESS.value,
            stage_history=[{"stage": "rumored", "turn": 1, "trigger": "tavern tale"}],
            known_by=["player"],
            related_npc_ids=["marla"],
            related_fact_ids=["fact-3"],
        ),
    ),
    (
        "world_facts",
        models.WorldFact,
        models.WorldFact(
            statement="Marla was promised the ledger.",
            pinned=True,
            established_turn=4,
            source="narrator",
            tags=["promise", "marla"],
            contradicts=["fact-9"],
        ),
    ),
    (
        "turn_log",
        models.TurnLog,
        models.TurnLog(
            turn=7,
            actor="player",
            raw_action="search the mill",
            mechanical_resolution={"kind": "check", "skill": "investigation", "band": "success"},
            narration_text="The dust gave up a ledger.",
            state_deltas_applied=[{"kind": "fact", "target": ""}],
            created_at=1_700_000_777,
        ),
    ),
    (
        "npc_memory",
        models.NPCMemoryEntry,
        models.NPCMemoryEntry(
            npc_id="marla",
            turn_established=3,
            statement="The player asked about the travelers.",
            type=models.MemoryType.KINDNESS.value,
            sentiment=0.75,
            decay_rate=0.15,
            reinforced_count=2,
            last_referenced_turn=6,
        ),
    ),
    (
        "chronicle",
        models.ChronicleEntry,
        models.ChronicleEntry(
            turn_id=2,
            actor="player",
            action_summary="asked the smith about the road",
            mechanical_result="success",
            consequence_oneliner="the smith talked, warily",
            verbatim_text=None,  # nullable: only recent turns keep prose
        ),
    ),
    (
        "relationship_ledger",
        models.RelationshipDelta,
        models.RelationshipDelta(
            npc_id="marla",
            player_id="player",
            turn=5,
            delta=-12.5,
            reason="lied about the shipment",
            category=models.RelationshipCategory.TRUST.value,
            decay_class="slow",
        ),
    ),
    (
        "moods",
        models.MoodState,
        models.MoodState(
            npc_id="marla",
            valence=-0.4,
            arousal=0.7,
            baseline_valence=0.1,
            baseline_arousal=0.2,
            last_updated_turn=6,
        ),
    ),
    (
        "saga_levels",
        models.SagaRow,
        models.SagaRow(
            level=models.SagaLevel.SESSION.value,
            scope_id="session-1",
            text="The player settled the ford dispute.",
            created_turn=30,
            references=["fact-1", "arc-1"],
        ),
    ),
    (
        "telemetry",
        models.TelemetryRow,
        models.TelemetryRow(
            turn=4,
            provider="openai",
            model="gpt-x",
            prompt_tokens=1200,
            completion_tokens=300,
            budget_alloc={"system": 400, "memory": 250},
            dropped=["saga_detail"],
            notes={"repaired": True},
        ),
    ),
]


@pytest.mark.parametrize(
    ("table", "cls", "obj"), ENTITY_CASES, ids=[case[0] for case in ENTITY_CASES]
)
def test_entity_roundtrip_through_store(store, table, cls, obj):
    rid = store.insert(table, models.to_row(obj))
    row = store.get(table, rid)
    assert row is not None, f"{table}: row vanished"
    assert row["id"] == rid
    back = models.from_row(cls, row)
    assert back == dataclasses.replace(obj, id=rid)


@pytest.mark.parametrize(
    ("table", "cls", "obj"), ENTITY_CASES, ids=[case[0] for case in ENTITY_CASES]
)
def test_json_columns_are_stored_as_json_text(store, table, cls, obj):
    rid = store.insert(table, models.to_row(obj))
    row = store.get(table, rid)
    assert row is not None
    for field_name in models.JSON_FIELDS.get(cls, frozenset()):
        raw = row[field_name]
        assert isinstance(raw, str), f"{table}.{field_name} not stored as text"
        assert json.loads(raw) == getattr(obj, field_name), f"{table}.{field_name} JSON drift"


def test_json_field_inventory_matches_entity_cases():
    covered = {cls for _table, cls, _obj in ENTITY_CASES}
    assert set(models.JSON_FIELDS) <= covered, "a JSON-bearing dataclass has no case above"


def test_bool_columns_use_sqlite_int_coding(store):
    npc = models.NPC(name="Gerd", alive=False)
    npc_id = store.insert("npcs", models.to_row(npc))
    fact = models.WorldFact(statement="The ford is impassable in spring.", pinned=True)
    fact_id = store.insert("world_facts", models.to_row(fact))

    assert store.get("npcs", npc_id)["alive"] == 0
    assert store.get("world_facts", fact_id)["pinned"] == 1
    assert store.find("npcs", {"alive": 0})[0]["id"] == npc_id
    assert store.find("world_facts", {"pinned": 1})[0]["id"] == fact_id

    assert models.from_row(models.NPC, store.get("npcs", npc_id)).alive is False
    assert models.from_row(models.WorldFact, store.get("world_facts", fact_id)).pinned is True


def test_nullable_verbatim_text_roundtrips_both_ways(store):
    recent = store.insert("chronicle", models.to_row(models.ChronicleEntry(
        turn_id=9, action_summary="vow", verbatim_text="I will bring her back.",
    )))
    old = store.insert("chronicle", models.to_row(models.ChronicleEntry(
        turn_id=1, action_summary="arrived",
    )))
    assert store.get("chronicle", recent)["verbatim_text"] == "I will bring her back."
    assert store.get("chronicle", old)["verbatim_text"] is None
    assert models.from_row(models.ChronicleEntry, store.get("chronicle", old)).verbatim_text is None


def test_mood_upsert_is_idempotent_on_npc_id(store):
    marla = models.MoodState(npc_id="marla", valence=0.5, arousal=0.2, baseline_valence=0.1)
    first = store.upsert("moods", models.to_row(marla), conflict="npc_id")
    with pytest.raises(sqlite3.IntegrityError):
        store.insert("moods", models.to_row(marla))  # npc_id is UNIQUE by schema

    marla.valence = -0.6
    marla.last_updated_turn = 7
    second = store.upsert("moods", models.to_row(marla), conflict="npc_id")
    assert second == first
    assert store.count("moods") == 1
    current = models.from_row(models.MoodState, store.get("moods", first))
    assert current.valence == -0.6
    assert current.last_updated_turn == 7
    assert current.baseline_valence == 0.1


def test_key_value_tables_roundtrip_via_upsert(store):
    store.upsert("meta", {"key": "schema_version", "value": "1"}, conflict="key")
    store.upsert("meta", {"key": "schema_version", "value": "2"}, conflict="key")
    store.upsert("provider_caps", {"provider_key": "openai:gpt-x",
                                   "caps": json.dumps({"native_tools": True}),
                                   "probed_at": 42}, conflict="provider_key")
    assert store.count("meta") == 1
    assert store.find_one("meta", {"key": "schema_version"})["value"] == "2"
    caps = store.find_one("provider_caps", {"provider_key": "openai:gpt-x"})
    assert json.loads(caps["caps"]) == {"native_tools": True}
    assert caps["probed_at"] == 42
