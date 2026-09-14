"""State store — the single source of truth (spec §2).

The ONLY module that opens SQLite. Everyone else goes through ``Store``'s
generic row API: dict-in / dict-out, keys == column names, JSON columns coded
via ``models.to_row`` / ``models.from_row`` (callers use those helpers).

Implementation contract (R1 card):
- ``Store.__init__`` opens the DB (``sqlite3``, ``check_same_thread=False``),
  sets ``PRAGMA journal_mode=WAL``, ``PRAGMA foreign_keys=ON``, and applies
  ``schema.sql`` idempotently on every connect (``apply_migrations=False`` skips).
- All write methods auto-commit unless inside ``transaction()``; inside a
  transaction they participate and roll back together on exception.
- ``where`` mappings match columns by equality (AND). Values are bound params.
- Bad table/column names raise ``ValueError`` (validated against ``PRAGMA
  table_info``), never SQL string interpolation of values.
- ``insert`` returns the new integer rowid; ``get``/``find`` decode JSON columns
  per ``models.JSON_FIELDS`` is the CALLER's job via ``from_row`` (this layer
  returns raw column dicts).
"""
from __future__ import annotations

import sqlite3  # noqa: F401  (kept for the implementing card's reference)
from collections.abc import Iterator, Mapping, Sequence
from contextlib import contextmanager
from pathlib import Path
from typing import Any

SCHEMA_PATH = Path(__file__).with_name("schema.sql")


class Store:
    """Generic row API over the engine SQLite database (see module docstring)."""

    def __init__(self, db_path: str | Path, *, apply_migrations: bool = True) -> None:
        raise NotImplementedError("R1 card implements Store")

    # -- lifecycle ---------------------------------------------------------
    def close(self) -> None:
        raise NotImplementedError

    @contextmanager
    def transaction(self) -> Iterator[None]:
        raise NotImplementedError

    # -- generic row API ---------------------------------------------------
    def insert(self, table: str, data: Mapping[str, Any]) -> int:
        raise NotImplementedError

    def get(self, table: str, row_id: int) -> dict | None:
        raise NotImplementedError

    def find(
        self,
        table: str,
        where: Mapping[str, Any] | None = None,
        *,
        order_by: str | None = None,
        limit: int | None = None,
        offset: int = 0,
    ) -> list[dict]:
        raise NotImplementedError

    def find_one(
        self,
        table: str,
        where: Mapping[str, Any] | None = None,
        *,
        order_by: str | None = None,
    ) -> dict | None:
        raise NotImplementedError

    def update(self, table: str, row_id: int, data: Mapping[str, Any]) -> bool:
        raise NotImplementedError

    def upsert(self, table: str, data: Mapping[str, Any], *, conflict: str) -> int:
        """INSERT … ON CONFLICT(``conflict`` column) DO UPDATE. Returns rowid."""
        raise NotImplementedError

    def delete(self, table: str, row_id: int) -> bool:
        raise NotImplementedError

    def count(self, table: str, where: Mapping[str, Any] | None = None) -> int:
        raise NotImplementedError

    # -- escape hatches (use sparingly; read-mostly) ------------------------
    def sql(self, query: str, params: Sequence[Any] = ()) -> list[dict]:
        """Read query, rows as dicts."""
        raise NotImplementedError

    def exec_sql(self, query: str, params: Sequence[Any] = ()) -> int:
        """Write statement; returns rowcount."""
        raise NotImplementedError


def connect(db_path: str | Path) -> Store:
    """Open (and migrate) a Store. Thin convenience wrapper."""
    return Store(db_path)
