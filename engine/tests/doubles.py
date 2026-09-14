"""In-memory test doubles for the R2 focused tests.

``FakeStore`` — minimal stand-in for ``engine.store.Store`` (R2 card note)
implementing only the surface the validator/resolver use — ``insert``,
``get``, ``find``, ``find_one``, ``update``, ``upsert``, ``delete``,
``count``, ``transaction``, ``close`` — with the REAL store's semantics:

* row dicts are RAW column dicts (callers encode/decode JSON columns via
  ``models.to_row`` / ``models.from_row``);
* bad table/column names raise ``ValueError`` (like the real store's
  ``PRAGMA table_info`` validation);
* writes auto-commit, or participate in ``transaction()`` and roll back
  together when an exception escapes the block;
* ``COLUMNS`` (below) mirrors ``store/schema.sql`` so a typo in either the
  validator or its tests surfaces as a failure instead of silently passing.

``ScriptedRng`` — deterministic ``RngSource`` returning a fixed sequence, so
dice assertions are exact (no seed hunting).

The R2 card requires the validator's focused tests to run against a fake
while the real store lands in parallel; ``tests/test_validate_integration.py``
exercises the real ``engine.store.Store`` once it exists.
"""
from __future__ import annotations

import copy
from collections.abc import Iterator, Mapping, Sequence
from contextlib import contextmanager
from pathlib import Path
from typing import Any

# table -> ordered column names (mirrors store/schema.sql)
COLUMNS: dict[str, tuple[str, ...]] = {
    "world": ("id", "seed", "day", "hour", "minute", "active_scene_id", "created_at"),
    "locations": ("id", "name", "description_static", "connections", "flags"),
    "characters": (
        "id", "name", "stats", "inventory", "location_id", "status_effects",
        "known_facts", "journal",
    ),
    "npcs": ("id", "name", "personality", "disposition_base", "location_id", "alive", "schedule"),
    "leads": (
        "id", "title", "stage", "stage_history", "known_by", "related_npc_ids",
        "related_fact_ids",
    ),
    "world_facts": (
        "id", "statement", "pinned", "established_turn", "source", "tags", "contradicts",
    ),
    "turn_log": (
        "id", "turn", "actor", "raw_action", "mechanical_resolution", "narration_text",
        "state_deltas_applied", "created_at",
    ),
    "npc_memory": (
        "id", "npc_id", "turn_established", "statement", "type", "sentiment",
        "decay_rate", "reinforced_count", "last_referenced_turn",
    ),
    "chronicle": (
        "id", "turn_id", "actor", "action_summary", "mechanical_result",
        "consequence_oneliner", "verbatim_text",
    ),
    "relationship_ledger": (
        "id", "npc_id", "player_id", "turn", "delta", "reason", "category", "decay_class",
    ),
    "moods": (
        "id", "npc_id", "valence", "arousal", "baseline_valence", "baseline_arousal",
        "last_updated_turn",
    ),
    "saga_levels": ("id", "level", "scope_id", "text", "created_turn", "references"),
    "telemetry": (
        "id", "turn", "provider", "model", "prompt_tokens", "completion_tokens",
        "budget_alloc", "dropped", "notes",
    ),
    "meta": ("key", "value"),
    "provider_caps": ("provider_key", "caps", "probed_at"),
}


class FakeStore:
    """In-memory row store with the same shape as ``engine.store.Store``."""

    def __init__(self, db_path: str | Path = ":memory:") -> None:
        self.db_path = str(db_path)
        self._rows: dict[str, dict[int, dict]] = {name: {} for name in COLUMNS}
        self._seq: dict[str, int] = dict.fromkeys(COLUMNS, 0)
        self.write_count = 0
        self.transaction_count = 0

    # -- internals ---------------------------------------------------------
    def _table(self, table: str) -> dict[int, dict]:
        if table not in COLUMNS:
            raise ValueError(f"unknown table: {table!r}")
        return self._rows[table]

    def _check_columns(self, table: str, data: Mapping[str, Any]) -> None:
        unknown = set(data) - set(COLUMNS[table])
        if unknown:
            raise ValueError(f"unknown column(s) for {table}: {sorted(unknown)}")

    # -- lifecycle ---------------------------------------------------------
    def close(self) -> None:  # pragma: no cover - trivial
        pass

    @contextmanager
    def transaction(self) -> Iterator[None]:
        self.transaction_count += 1
        snapshot = copy.deepcopy(self._rows)
        try:
            yield
        except BaseException:
            self._rows = snapshot
            raise

    # -- generic row API ---------------------------------------------------
    def insert(self, table: str, data: Mapping[str, Any]) -> int:
        table_rows = self._table(table)
        self._check_columns(table, data)
        self._seq[table] += 1
        row_id = self._seq[table]
        row = dict(data)
        row["id"] = row_id
        table_rows[row_id] = row
        self.write_count += 1
        return row_id

    def get(self, table: str, row_id: int) -> dict | None:
        row = self._table(table).get(int(row_id))
        return copy.deepcopy(row) if row is not None else None

    def find(
        self,
        table: str,
        where: Mapping[str, Any] | None = None,
        *,
        order_by: str | None = None,
        limit: int | None = None,
        offset: int = 0,
    ) -> list[dict]:
        rows = [copy.deepcopy(r) for r in self._table(table).values()]
        if where:
            self._check_columns(table, where)
            rows = [r for r in rows if all(r.get(k) == v for k, v in where.items())]
        if order_by:
            parts = order_by.split()
            col = parts[0]
            if col not in COLUMNS[table]:
                raise ValueError(f"unknown order_by column: {order_by!r}")
            reverse = len(parts) > 1 and parts[1].upper() == "DESC"
            rows.sort(key=lambda r: (r.get(col) is None, r.get(col)), reverse=reverse)
        else:
            rows.sort(key=lambda r: r.get("id", 0))
        return rows[max(0, offset):] if limit is None else rows[max(0, offset):max(0, offset) + limit]

    def find_one(
        self,
        table: str,
        where: Mapping[str, Any] | None = None,
        *,
        order_by: str | None = None,
    ) -> dict | None:
        rows = self.find(table, where, order_by=order_by, limit=1)
        return rows[0] if rows else None

    def update(self, table: str, row_id: int, data: Mapping[str, Any]) -> bool:
        table_rows = self._table(table)
        self._check_columns(table, data)
        row = table_rows.get(int(row_id))
        if row is None:
            return False
        row.update({k: v for k, v in data.items()})
        self.write_count += 1
        return True

    def upsert(self, table: str, data: Mapping[str, Any], *, conflict: str) -> int:
        table_rows = self._table(table)
        self._check_columns(table, data)
        if conflict not in COLUMNS[table]:
            raise ValueError(f"unknown conflict column for {table}: {conflict!r}")
        if conflict not in data:
            raise ValueError(f"upsert on {table} needs the conflict column {conflict!r} in data")
        for row_id, row in table_rows.items():
            if row.get(conflict) == data[conflict]:
                row.update(dict(data))
                self.write_count += 1
                return row_id
        return self.insert(table, data)

    def delete(self, table: str, row_id: int) -> bool:
        row = self._table(table).pop(int(row_id), None)
        if row is not None:
            self.write_count += 1
        return row is not None

    def count(self, table: str, where: Mapping[str, Any] | None = None) -> int:
        return len(self.find(table, where))

    # -- test helpers -------------------------------------------------------
    def snapshot(self) -> dict[str, dict[int, dict]]:
        """Deep copy of all rows (for 'validate wrote nothing' assertions)."""
        return copy.deepcopy(self._rows)

    def rows(self, table: str) -> list[dict]:
        return self.find(table)

    def reserve(self, table: str, row_id: int) -> None:
        """Make the NEXT insert into ``table`` return ``row_id``.

        Lets a test place a row at a specific id (e.g. ``npc:7``) so target
        strings like ``"npc:7"`` resolve the way real campaigns' ids do.
        """
        self._table(table)
        if row_id > self._seq[table]:
            self._seq[table] = int(row_id) - 1


class ScriptedRng:
    """Deterministic ``RngSource``: returns a fixed sequence, range-checked."""

    def __init__(self, values: Sequence[int]) -> None:
        self._values = list(values)
        self.calls: list[tuple[int, int]] = []

    def randint(self, a: int, b: int) -> int:
        self.calls.append((a, b))
        if not self._values:
            raise AssertionError(f"ScriptedRng exhausted after {len(self.calls)} draws")
        value = self._values.pop(0)
        if not a <= value <= b:
            raise AssertionError(f"ScriptedRng value {value} outside [{a}, {b}]")
        return value

    @property
    def remaining(self) -> list[int]:
        return list(self._values)


# --------------------------------------------------------------------------- #
# Seeding helpers (rows are encoded with models.to_row, like real callers)
# --------------------------------------------------------------------------- #

def insert_character(store: FakeStore, *, name: str = "Player", stats: dict | None = None,
                     inventory: list | None = None, location_id: str = "yard",
                     status_effects: list | None = None, row_id: int | None = None) -> int:
    from engine.models import Character, to_row

    if row_id is not None:
        store.reserve("characters", row_id)
    char = Character(
        name=name,
        stats={"hp": 10, "max_hp": 10, "currency": 25} if stats is None else stats,
        inventory=[] if inventory is None else inventory,
        location_id=location_id,
        status_effects=[] if status_effects is None else status_effects,
    )
    return store.insert("characters", to_row(char))


def insert_npc(store: FakeStore, *, name: str = "Marla", alive: bool = True,
               location_id: str = "yard", personality: dict | None = None,
               disposition_base: float = 0.0, row_id: int | None = None) -> int:
    from engine.models import NPC, to_row

    if row_id is not None:
        store.reserve("npcs", row_id)
    npc = NPC(
        name=name,
        personality={} if personality is None else personality,
        disposition_base=disposition_base,
        location_id=location_id,
        alive=alive,
    )
    return store.insert("npcs", to_row(npc))


def insert_lead(store: FakeStore, *, title: str = "The missing shipment",
                stage: str = "unheard", stage_history: list | None = None,
                row_id: int | None = None) -> int:
    from engine.models import Lead, to_row

    if row_id is not None:
        store.reserve("leads", row_id)
    lead = Lead(title=title, stage=stage, stage_history=[] if stage_history is None else stage_history)
    return store.insert("leads", to_row(lead))


def insert_fact(store: FakeStore, *, statement: str, pinned: bool = False,
                established_turn: int = 0, source: str = "narrator",
                tags: list | None = None, contradicts: list | None = None,
                row_id: int | None = None) -> int:
    from engine.models import WorldFact, to_row

    if row_id is not None:
        store.reserve("world_facts", row_id)
    fact = WorldFact(
        statement=statement,
        pinned=pinned,
        established_turn=established_turn,
        source=source,
        tags=[] if tags is None else tags,
        contradicts=[] if contradicts is None else contradicts,
    )
    return store.insert("world_facts", to_row(fact))


def insert_mood(store: FakeStore, *, npc_id: str, valence: float = 0.0, arousal: float = 0.0,
                baseline_valence: float = 0.0, baseline_arousal: float = 0.0,
                last_updated_turn: int = 0) -> int:
    from engine.models import MoodState, to_row

    mood = MoodState(
        npc_id=npc_id,
        valence=valence,
        arousal=arousal,
        baseline_valence=baseline_valence,
        baseline_arousal=baseline_arousal,
        last_updated_turn=last_updated_turn,
    )
    return store.insert("moods", to_row(mood))


_ = (Sequence, Any)
