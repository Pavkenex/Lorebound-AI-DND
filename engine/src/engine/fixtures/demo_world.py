"""Fixture worlds for the engine: the demo campaign seed and the seeding API.

Row shape matches the eval harness convention (``evals/harness.py``
``_FIXTURE_MODELS``): ``{table: [ {model kwargs}, … ]}``. A ``ref`` key is
tolerated and ignored here; the harness binds refs to row ids for scenarios
that reference rows by name.

``seed_world`` is the only writer: tables it does not know raise, unknown row
keys raise (typos must not be swallowed), and every row is encoded through the
frozen models so the fixture can never drift from the schema.
"""
from __future__ import annotations

import importlib.util
import json
from collections.abc import Mapping
from pathlib import Path
from typing import Any

from ..models import (
    NPC,
    Character,
    ChronicleEntry,
    Lead,
    Location,
    MoodState,
    NPCMemoryEntry,
    RelationshipDelta,
    SagaRow,
    TelemetryRow,
    TurnLog,
    World,
    WorldFact,
    to_row,
)

# table -> model: the fixture encoder (identical set to evals/harness.py).
FIXTURE_MODELS: dict[str, type] = {
    "world": World,
    "locations": Location,
    "characters": Character,
    "npcs": NPC,
    "leads": Lead,
    "world_facts": WorldFact,
    "turn_log": TurnLog,
    "npc_memory": NPCMemoryEntry,
    "chronicle": ChronicleEntry,
    "relationship_ledger": RelationshipDelta,
    "moods": MoodState,
    "saga_levels": SagaRow,
    "telemetry": TelemetryRow,
}

DEMO_WORLD: dict[str, list[dict]] = {
    "world": [
        {"seed": "saltmarsh-demo", "day": 1, "hour": 8, "minute": 0,
         "active_scene_id": "yard"},
    ],
    "locations": [
        {
            "name": "The Salt Gate yard",
            "description_static": (
                "Packed earth the colour of old bone, ringed by salt-crusted "
                "wall. Cart ruts run to the gatehouse; a rope well sits in the "
                "north corner."
            ),
            "connections": ["gatehouse", "market", "well"],
            "flags": {"slug": "yard"},
        },
        {
            "name": "The Salt Gate gatehouse",
            "description_static": (
                "Two storeys of grey stone straddling the coast road. Clerks "
                "tally cargo in a lamplit room above the arch; the portcullis "
                "chain is older than anyone living."
            ),
            "connections": ["yard", "market"],
            "flags": {"slug": "gatehouse"},
        },
        {
            "name": "The Salt Market",
            "description_static": (
                "Three rows of stalls under oiled canvas, every table loaded "
                "with salt in cones, bricks and sacks. Prices are shouted, "
                "never written."
            ),
            "connections": ["yard", "well"],
            "flags": {"slug": "market"},
        },
        {
            "name": "The Old Well",
            "description_static": (
                "A waist-high ring of dry stone behind the yard, its mouth cold "
                "even at noon. The water tastes of iron, and nobody draws after "
                "dusk."
            ),
            "connections": ["yard", "market"],
            "flags": {"slug": "well"},
        },
    ],
    "characters": [
        {
            "name": "Rell",
            "stats": {"hp": 11, "max_hp": 11, "might": 2, "wits": 3,
                      "nerve": 2, "currency": 6},
            "inventory": [
                {"item_id": "coil of rope", "qty": 1, "flags": {}},
                {"item_id": "gate token", "qty": 1,
                 "flags": {"stamped": "salt-gate"}},
            ],
            "location_id": "yard",
            "status_effects": [],
            "known_facts": [],
            "journal": [],
        },
    ],
    "npcs": [
        {
            "name": "Marla Quist",
            "personality": {
                "traits": ["watchful", "dry", "fair"],
                "tone": "plain",
                "speech_pattern": "short sentences, no titles, asks one question at a time",
                "baseline_valence": 0.15,
                "baseline_arousal": -0.15,
            },
            "disposition_base": 12.0,
            "location_id": "yard",
            "alive": True,
            "schedule": {"dawn": "yard", "noon": "gatehouse"},
        },
        {
            "name": "Hob Fen",
            "personality": {
                "traits": ["nervous", "talkative", "rule-bound"],
                "tone": "hurried",
                "speech_pattern": "over-explains, repeats the last word of the question",
                "baseline_valence": -0.10,
                "baseline_arousal": 0.25,
            },
            "disposition_base": 0.0,
            "location_id": "gatehouse",
            "alive": True,
            "schedule": {},
        },
        {
            "name": "Sera Vane",
            "personality": {
                "traits": ["sharp", "courteous", "merciless in margins"],
                "tone": "warm until it costs money",
                "speech_pattern": "prices everything, never states a debt twice",
                "baseline_valence": 0.0,
                "baseline_arousal": 0.10,
            },
            "disposition_base": -8.0,
            "location_id": "market",
            "alive": True,
            "schedule": {"dusk": "yard"},
        },
        {
            "name": "Tam",
            "personality": {
                "traits": ["cheerful", "restless", "twelve"],
                "tone": "bright",
                "speech_pattern": "runs words together, asks about the sea",
                "baseline_valence": 0.40,
                "baseline_arousal": 0.20,
            },
            "disposition_base": 18.0,
            "location_id": "well",
            "alive": True,
            "schedule": {},
        },
        {
            "name": "Grunn Sallow",
            "personality": {
                "traits": ["patient", "scarred", "paid to be certain"],
                "tone": "flat",
                "speech_pattern": "one clause, then he waits",
                "baseline_valence": -0.30,
                "baseline_arousal": -0.05,
            },
            "disposition_base": -25.0,
            "location_id": "market",
            "alive": True,
            "schedule": {},
        },
    ],
    "leads": [
        {
            "title": "The missing salt shipment",
            "stage": "accepted",
            "stage_history": [{"stage": "unheard", "turn": 0},
                              {"stage": "accepted", "turn": 0}],
            "known_by": ["npc:1"],
            "related_npc_ids": ["npc:1", "npc:3"],
            "related_fact_ids": [],
        },
        {
            "title": "The gatehouse ledger",
            "stage": "rumored",
            "stage_history": [{"stage": "unheard", "turn": 0},
                              {"stage": "rumored", "turn": 0}],
            "known_by": ["npc:2"],
            "related_npc_ids": ["npc:2"],
            "related_fact_ids": [],
        },
        {
            "title": "What the well keeps",
            "stage": "unheard",
            "stage_history": [{"stage": "unheard", "turn": 0}],
            "known_by": [],
            "related_npc_ids": ["npc:4"],
            "related_fact_ids": [],
        },
    ],
    "world_facts": [
        {
            "statement": "Marla's brother Dain is dead — drowned at the Salt "
                         "Gate three winters ago.",
            "pinned": True,
            "established_turn": 0,
            "source": "npc:1",
            "tags": ["personal", "anchor"],
        },
        {
            "statement": "Salt leaves the gate only against a stamped gate token.",
            "pinned": False,
            "established_turn": 0,
            "source": "npc:2",
            "tags": ["rules"],
        },
        {
            "statement": "Sera Vane holds the market's salt contracts and pays "
                         "Grunn to collect what those contracts are owed.",
            "pinned": False,
            "established_turn": 0,
            "source": "npc:3",
            "tags": ["politics"],
        },
        {
            "statement": "The old well's water is never drawn after dusk.",
            "pinned": False,
            "established_turn": 0,
            "source": "npc:4",
            "tags": ["superstition"],
        },
        {
            "statement": "Rell owes Marla two silver for the room above the stable.",
            "pinned": False,
            "established_turn": 0,
            "source": "npc:1",
            "tags": ["personal", "debt"],
        },
    ],
}


def demo_world() -> dict[str, list[dict]]:
    """Deep copy of the demo campaign seed (mutating it must not poison others)."""
    return {
        table: [{key: _copy(value) for key, value in row.items()} for row in rows]
        for table, rows in DEMO_WORLD.items()
    }


def _copy(value: Any) -> Any:
    if isinstance(value, dict):
        return {key: _copy(item) for key, item in value.items()}
    if isinstance(value, list):
        return [_copy(item) for item in value]
    return value


def seed_world(store: Any, world: Mapping[str, Any] | None = None) -> dict[str, int]:
    """Insert a fixture world into an empty store; returns rows per table.

    Raises on an unknown table or an unknown row key so fixture typos fail
    loudly instead of silently seeding a half-configured campaign.
    """
    payload = demo_world() if world is None else world
    if not isinstance(payload, Mapping):
        raise ValueError("world must be a mapping of table -> [rows]")
    counts: dict[str, int] = {}
    for table, rows in payload.items():
        model = FIXTURE_MODELS.get(table)
        if model is None:
            raise ValueError(
                f"unknown table {table!r}; known: {sorted(FIXTURE_MODELS)}"
            )
        if not isinstance(rows, list):
            raise ValueError(f"{table} must be a list of rows")
        known = {field.name for field in model.__dataclass_fields__.values()}
        inserted = 0
        for index, row in enumerate(rows, start=1):
            if not isinstance(row, Mapping):
                raise ValueError(f"{table}[{index}]: row must be a mapping of model kwargs")
            values = {key: _copy(value) for key, value in row.items() if key != "ref"}
            unknown = sorted(set(values) - known)
            if unknown:
                raise ValueError(
                    f"{table}[{index}]: unknown field(s) {unknown} for table "
                    f"{table!r}; known: {sorted(known)}"
                )
            store.insert(table, to_row(model(**values)))
            inserted += 1
        counts[table] = inserted
    return counts


def load_world(source: Any = None) -> dict:
    """Resolve a world fixture: None/``"demo"`` → demo; path → JSON or Python.

    A ``.py`` file must define ``WORLD`` (or ``DEMO_WORLD``); a ``.json`` file
    must hold the table → rows mapping directly. Unknown sources raise.
    """
    if source is None or (isinstance(source, str) and source.strip().lower() == "demo"):
        return demo_world()
    if isinstance(source, Mapping):
        return {table: [dict(row) for row in rows] for table, rows in source.items()}
    if isinstance(source, str):
        path = Path(source)
        if not path.is_file():
            raise ValueError(f"world fixture not found: {source!r}")
        if path.suffix == ".json":
            data = json.loads(path.read_text())
        else:
            spec = importlib.util.spec_from_file_location("_lorebound_world", path)
            if spec is None or spec.loader is None:  # pragma: no cover - path checked
                raise ValueError(f"cannot import a world from {source!r}")
            module = importlib.util.module_from_spec(spec)
            spec.loader.exec_module(module)
            data = getattr(module, "WORLD", None) or getattr(module, "DEMO_WORLD", None)
            if data is None:
                raise ValueError(f"{source!r} defines neither WORLD nor DEMO_WORLD")
        if not isinstance(data, Mapping):
            raise ValueError(f"{source!r}: world must be a mapping of table -> [rows]")
        return {table: [dict(row) for row in rows] for table, rows in data.items()}
    raise ValueError(f"cannot load a world from {type(source).__name__}")
