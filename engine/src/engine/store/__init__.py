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

Implementation notes (beyond the contract above):
- The connection runs in autocommit mode (``isolation_level=None``); explicit
  ``BEGIN``/``COMMIT``/``ROLLBACK`` are issued by ``transaction()``. SQLite does
  not nest transactions, so a ``transaction()`` opened while one is already
  active raises ``RuntimeError``; use a single flat transaction (the inner
  writes of a *body* exception roll back with it — no partial commits).
- ``get``/``update``/``delete`` address rows by the ``id`` column. Key/value
  tables without one (``meta``, ``provider_caps``) use ``upsert`` /
  ``find`` / ``sql`` / ``exec_sql`` instead.
- ``upsert`` returns the sqlite rowid of the inserted-or-updated row (a plain
  ``INSERT``'s ``lastrowid`` is stale on the DO UPDATE branch, so it re-selects)
  and requires ``conflict`` to be a UNIQUE/PRIMARY KEY column named in ``data``.
- ``where`` values of ``None`` compile to ``IS NULL`` (``col = NULL`` matches
  nothing in SQL). ``order_by`` accepts ``"col [ASC|DESC][, …]"`` with column
  names validated against the table. ``offset`` without ``limit`` compiles to
  ``LIMIT -1 OFFSET ?``. ``limit=0`` yields ``[]``.
- ``PRAGMA busy_timeout=5000`` lets a second ``Store`` on the same file wait for
  write locks instead of failing instantly (WAL allows concurrent readers).
- A file DB's parent directory is created if missing; ``":memory:"`` passes
  through unchanged (each in-memory Store gets its own private database).
- ``sql`` is the read escape hatch (statements that return rows; ``[]`` for
  statements that don't); ``exec_sql`` is the write escape hatch returning
  ``cursor.rowcount`` (-1 for statements that touch no rows, e.g. DDL).
- Identifiers are interpolated only after validation against
  ``sqlite_master``/``PRAGMA table_info``; every *value* is a bound parameter.
"""
from __future__ import annotations

import sqlite3
from collections.abc import Iterable, Iterator, Mapping, Sequence
from contextlib import contextmanager
from pathlib import Path
from typing import Any

SCHEMA_PATH = Path(__file__).with_name("schema.sql")

_ASC = "ASC"
_DESC = "DESC"


def _quote(name: str) -> str:
    """Quote an identifier for interpolation (callers validate it first)."""
    return '"' + name.replace('"', '""') + '"'


class Store:
    """Generic row API over the engine SQLite database (see module docstring)."""

    def __init__(self, db_path: str | Path, *, apply_migrations: bool = True) -> None:
        self._path = self._resolve_path(db_path)
        self._columns: dict[str, frozenset[str]] = {}
        self._closed = False
        self._conn = sqlite3.connect(self._path, check_same_thread=False, isolation_level=None)
        self._conn.row_factory = sqlite3.Row
        try:
            self._conn.execute("PRAGMA busy_timeout = 5000")
            self._conn.execute("PRAGMA journal_mode = WAL")
            self._conn.execute("PRAGMA foreign_keys = ON")
            if apply_migrations:
                self._apply_schema()
        except BaseException:
            self._conn.close()
            self._closed = True
            raise

    # -- lifecycle ---------------------------------------------------------
    def close(self) -> None:
        """Close the connection (idempotent). Any open transaction is rolled back."""
        if not self._closed:
            self._conn.close()
            self._closed = True

    @contextmanager
    def transaction(self) -> Iterator[None]:
        """Run a block in one SQLite transaction: commit on success, roll back on
        exception (including partial writes already made in the block).

        Not re-entrant: a ``transaction()`` opened while one is already active
        raises ``RuntimeError`` (SQLite has no nested transactions — savepoints
        are out of scope for this layer). Writes made *outside* any transaction
        auto-commit.
        """
        if self._conn.in_transaction:
            raise RuntimeError(
                "transaction() is not re-entrant: a transaction is already open; "
                "SQLite transactions do not nest — use one flat transaction"
            )
        self._conn.execute("BEGIN")
        try:
            yield
        except BaseException:
            if self._conn.in_transaction:
                self._conn.execute("ROLLBACK")
            raise
        else:
            self._conn.execute("COMMIT")

    # -- generic row API ---------------------------------------------------
    def insert(self, table: str, data: Mapping[str, Any]) -> int:
        """INSERT one row from ``data`` (column -> value). Returns the new rowid.

        An empty mapping inserts schema defaults (``INSERT … DEFAULT VALUES``).
        """
        columns = list(data)
        self._columns_of(table, columns)
        if columns:
            cols_sql = ", ".join(_quote(c) for c in columns)
            placeholders = ", ".join("?" for _ in columns)
            sql = f"INSERT INTO {_quote(table)} ({cols_sql}) VALUES ({placeholders})"
            params: list[Any] = [data[c] for c in columns]
        else:
            sql = f"INSERT INTO {_quote(table)} DEFAULT VALUES"
            params = []
        cur = self._conn.execute(sql, params)
        if cur.lastrowid is None:  # pragma: no cover - INSERT always sets it
            raise RuntimeError(f"INSERT into {table!r} did not produce a rowid")
        return int(cur.lastrowid)

    def get(self, table: str, row_id: int) -> dict | None:
        """Row by ``id`` as a raw column dict, or ``None`` when absent."""
        self._require_id_column(table)
        row = self._conn.execute(
            f"SELECT * FROM {_quote(table)} WHERE id = ?", (row_id,)
        ).fetchone()
        return dict(row) if row is not None else None

    def find(
        self,
        table: str,
        where: Mapping[str, Any] | None = None,
        *,
        order_by: str | None = None,
        limit: int | None = None,
        offset: int = 0,
    ) -> list[dict]:
        """Rows matching ``where`` (equality, AND-joined), raw column dicts.

        ``None`` values match ``IS NULL``. See module docstring for order_by /
        limit / offset semantics.
        """
        self._table_columns(table)
        where_sql, params = self._where_sql(table, where)
        order_sql = self._order_sql(table, order_by)
        limit_sql, limit_params = self._limit_sql(limit, offset)
        query = f"SELECT * FROM {_quote(table)}{where_sql}{order_sql}{limit_sql}"
        rows = self._conn.execute(query, [*params, *limit_params]).fetchall()
        return [dict(r) for r in rows]

    def find_one(
        self,
        table: str,
        where: Mapping[str, Any] | None = None,
        *,
        order_by: str | None = None,
    ) -> dict | None:
        """First row matching ``where`` (optionally ordered), or ``None``."""
        rows = self.find(table, where, order_by=order_by, limit=1)
        return rows[0] if rows else None

    def update(self, table: str, row_id: int, data: Mapping[str, Any]) -> bool:
        """UPDATE the row with ``id == row_id``. Returns True when a row changed."""
        self._require_id_column(table)
        columns = list(data)
        self._columns_of(table, columns)
        if not columns:
            raise ValueError(f"update of {table!r} needs at least one column to set")
        assignments = ", ".join(f"{_quote(c)} = ?" for c in columns)
        params: list[Any] = [data[c] for c in columns]
        cur = self._conn.execute(
            f"UPDATE {_quote(table)} SET {assignments} WHERE id = ?", [*params, row_id]
        )
        return cur.rowcount > 0

    def upsert(self, table: str, data: Mapping[str, Any], *, conflict: str) -> int:
        """INSERT … ON CONFLICT(``conflict`` column) DO UPDATE. Returns rowid.

        ``conflict`` must name a UNIQUE/PRIMARY KEY column of ``table`` and be
        present in ``data``; the returned rowid is stable across re-upserts of
        the same key. With ``conflict`` as the only column, colliding inserts
        are a no-op (``DO NOTHING``) but still return the existing rowid.
        """
        columns = list(data)
        self._columns_of(table, columns)
        if conflict not in columns:
            known = sorted(self._table_columns(table))
            raise ValueError(
                f"upsert conflict column {conflict!r} must be a column of {table!r} "
                f"present in data; known columns: {known}"
            )
        cols_sql = ", ".join(_quote(c) for c in columns)
        placeholders = ", ".join("?" for _ in columns)
        updates = [c for c in columns if c != conflict]
        if updates:
            set_sql = ", ".join(f"{_quote(c)} = excluded.{_quote(c)}" for c in updates)
            on_conflict = f"DO UPDATE SET {set_sql}"
        else:
            on_conflict = "DO NOTHING"
        sql = (
            f"INSERT INTO {_quote(table)} ({cols_sql}) VALUES ({placeholders}) "
            f"ON CONFLICT({_quote(conflict)}) {on_conflict}"
        )
        self._conn.execute(sql, [data[c] for c in columns])
        row = self._conn.execute(
            f"SELECT rowid AS row_id FROM {_quote(table)} WHERE {_quote(conflict)} = ?",
            (data[conflict],),
        ).fetchone()
        if row is None:  # defensive: the conflict key is in data, so this row exists
            raise RuntimeError(f"upsert into {table!r} could not resolve the stored rowid")
        return int(row["row_id"])

    def delete(self, table: str, row_id: int) -> bool:
        """DELETE the row with ``id == row_id``. Returns True when a row went."""
        self._require_id_column(table)
        cur = self._conn.execute(f"DELETE FROM {_quote(table)} WHERE id = ?", (row_id,))
        return cur.rowcount > 0

    def count(self, table: str, where: Mapping[str, Any] | None = None) -> int:
        """Number of rows matching ``where`` (all rows when ``where`` is empty)."""
        self._table_columns(table)
        where_sql, params = self._where_sql(table, where)
        row = self._conn.execute(
            f"SELECT COUNT(*) AS n FROM {_quote(table)}{where_sql}", params
        ).fetchone()
        return int(row["n"])

    # -- escape hatches (use sparingly; read-mostly) ------------------------
    def sql(self, query: str, params: Sequence[Any] = ()) -> list[dict]:
        """Read query, rows as dicts. ``[]`` for statements that return no rows."""
        cur = self._conn.execute(query, self._bind_params(params))
        if cur.description is None:
            return []
        return [dict(row) for row in cur.fetchall()]

    def exec_sql(self, query: str, params: Sequence[Any] = ()) -> int:
        """Write statement; returns rowcount (-1 when no rows are affected)."""
        cur = self._conn.execute(query, self._bind_params(params))
        return int(cur.rowcount)

    # -- internals ---------------------------------------------------------
    @staticmethod
    def _resolve_path(db_path: str | Path) -> str:
        if isinstance(db_path, str) and db_path == ":memory:":
            return db_path
        path = Path(db_path)
        if path.parent != Path("."):
            path.parent.mkdir(parents=True, exist_ok=True)
        return str(path)

    @staticmethod
    def _bind_params(params: Sequence[Any]) -> Sequence[Any]:
        if isinstance(params, str | bytes):
            raise ValueError("params must be a sequence of values, not a string")
        return params

    def _apply_schema(self) -> None:
        """Run schema.sql (idempotent DDL) on every connect."""
        self._conn.executescript(SCHEMA_PATH.read_text(encoding="utf-8"))

    def _columns_of(self, table: str, names: Iterable[str]) -> frozenset[str]:
        """Validate ``table`` (+ ``names`` keys) and return the table's columns."""
        known = self._table_columns(table)
        unknown = [c for c in names if c not in known]
        if unknown:
            raise ValueError(
                f"unknown column(s) {sorted(unknown)!r} for table {table!r}; "
                f"known columns: {sorted(known)}"
            )
        return known

    def _table_columns(self, table: str) -> frozenset[str]:
        if not isinstance(table, str):
            raise ValueError(f"table name must be a string, got {type(table).__name__}")
        cached = self._columns.get(table)
        if cached is not None:
            return cached
        row = self._conn.execute(
            "SELECT name FROM sqlite_master WHERE type = 'table' AND name = ?", (table,)
        ).fetchone()
        if row is None:
            known = sorted(
                r["name"]
                for r in self._conn.execute(
                    "SELECT name FROM sqlite_master WHERE type = 'table' ORDER BY name"
                )
            )
            raise ValueError(f"unknown table {table!r}; known tables: {known}")
        info = self._conn.execute(f"PRAGMA table_info({_quote(table)})").fetchall()
        columns = frozenset(r["name"] for r in info)
        self._columns[table] = columns
        return columns

    def _require_id_column(self, table: str) -> None:
        if "id" not in self._table_columns(table):
            raise ValueError(
                f"table {table!r} has no 'id' column; address its rows via "
                "find/find_one/upsert/sql/exec_sql instead"
            )

    def _where_sql(
        self, table: str, where: Mapping[str, Any] | None
    ) -> tuple[str, list[Any]]:
        if where is None:
            return "", []
        if not isinstance(where, Mapping):
            raise ValueError(
                f"where must be a mapping of column -> value, got {type(where).__name__}"
            )
        if not where:
            return "", []
        self._columns_of(table, where)
        parts: list[str] = []
        params: list[Any] = []
        for column, value in where.items():
            if value is None:
                parts.append(f"{_quote(column)} IS NULL")
            else:
                parts.append(f"{_quote(column)} = ?")
                params.append(value)
        return " WHERE " + " AND ".join(parts), params

    def _order_sql(self, table: str, order_by: str | None) -> str:
        if order_by is None:
            return ""
        if not isinstance(order_by, str):
            raise ValueError(f"order_by must be a string, got {type(order_by).__name__}")
        terms: list[str] = []
        for raw in order_by.split(","):
            bits = raw.split()
            if not bits or len(bits) > 2:
                raise ValueError(
                    f"invalid order_by term {raw.strip()!r}; expected 'column [ASC|DESC]'"
                )
            column = bits[0]
            direction = bits[1].upper() if len(bits) == 2 else _ASC
            if direction not in (_ASC, _DESC):
                raise ValueError(
                    f"invalid order direction {bits[1]!r}; expected ASC or DESC"
                )
            self._columns_of(table, {column: None})
            terms.append(f"{_quote(column)} {direction}")
        return " ORDER BY " + ", ".join(terms)

    @staticmethod
    def _limit_sql(limit: int | None, offset: int) -> tuple[str, list[Any]]:
        for name, value in (("limit", limit), ("offset", offset)):
            if value is None:
                continue
            if isinstance(value, bool) or not isinstance(value, int):
                raise ValueError(f"{name} must be an integer, got {value!r}")
            if value < 0:
                raise ValueError(f"{name} must be >= 0, got {value!r}")
        if limit is None:
            return (" LIMIT -1 OFFSET ?", [offset]) if offset else ("", [])
        if offset:
            return " LIMIT ? OFFSET ?", [limit, offset]
        return " LIMIT ?", [limit]


def connect(db_path: str | Path) -> Store:
    """Open (and migrate) a Store. Thin convenience wrapper."""
    return Store(db_path)
