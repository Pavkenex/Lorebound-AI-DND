"""Store — generic row API, transactions, migrations, concurrency (R1, spec §2).

The store is the single source of truth every other module talks to, so these
tests pin real behavior: bound params, equality-where semantics, pagination,
rowid returns, transaction rollback, idempotent migrations and multi-handle
safety.
"""
from __future__ import annotations

import sqlite3
import threading
from collections.abc import Callable

import pytest

from engine.store import Store, connect


@pytest.fixture()
def store(tmp_path):
    s = Store(tmp_path / "campaign.sqlite")
    try:
        yield s
    finally:
        s.close()


# --------------------------------------------------------------------------- #
# construction / connection
# --------------------------------------------------------------------------- #

def test_connect_wrapper_returns_store(tmp_path):
    s = connect(tmp_path / "wrapped.sqlite")
    try:
        assert isinstance(s, Store)
    finally:
        s.close()


def test_memory_store_works_and_is_private():
    a, b = Store(":memory:"), Store(":memory:")
    try:
        a.insert("world", {"seed": "a"})
        assert a.count("world") == 1
        assert b.count("world") == 0
    finally:
        a.close()
        b.close()


def test_file_db_parent_directory_is_created(tmp_path):
    s = Store(tmp_path / "nested" / "deeper" / "campaign.sqlite")
    try:
        assert s.count("world") == 0
        assert (tmp_path / "nested" / "deeper" / "campaign.sqlite").exists()
    finally:
        s.close()


def test_connection_pragmas(store):
    assert store.sql("PRAGMA journal_mode")[0]["journal_mode"] == "wal"
    assert store.sql("PRAGMA foreign_keys")[0]["foreign_keys"] == 1
    assert store.sql("PRAGMA busy_timeout")[0]["timeout"] == 5000


def test_close_is_idempotent_and_operations_after_close_fail(tmp_path):
    path = tmp_path / "close.sqlite"
    s = Store(path)
    s.insert("world", {"seed": "kept"})
    s.close()
    s.close()  # no error
    with pytest.raises(sqlite3.ProgrammingError):
        s.count("world")
    reopened = Store(path)
    try:
        assert reopened.count("world") == 1
    finally:
        reopened.close()


# --------------------------------------------------------------------------- #
# insert / get
# --------------------------------------------------------------------------- #

def test_insert_and_get_roundtrip_with_defaults(store):
    rid = store.insert("world", {"seed": "ravenford", "day": 3, "created_at": 123})
    assert isinstance(rid, int) and rid > 0
    assert store.get("world", rid) == {
        "id": rid,
        "seed": "ravenford",
        "day": 3,
        "hour": 8,          # schema default
        "minute": 0,        # schema default
        "active_scene_id": "",
        "created_at": 123,
    }
    assert store.get("world", rid + 1000) is None


def test_insert_returns_increasing_rowids(store):
    first = store.insert("world_facts", {"statement": "one"})
    second = store.insert("world_facts", {"statement": "two"})
    assert second > first


def test_insert_empty_mapping_uses_schema_defaults(store):
    rid = store.insert("world", {})
    row = store.get("world", rid)
    assert row is not None
    assert (row["day"], row["hour"], row["minute"]) == (1, 8, 0)


def test_insert_rejects_unknown_table_and_column(store):
    with pytest.raises(ValueError, match="unknown table"):
        store.insert("ghosts", {"name": "x"})
    with pytest.raises(ValueError, match="unknown column"):
        store.insert("world", {"seed": "ok", "not_a_column": 1})
    assert store.count("world") == 0  # rejected insert wrote nothing


# --------------------------------------------------------------------------- #
# find / find_one
# --------------------------------------------------------------------------- #

def _seed_characters(store: Store) -> tuple[int, int, int]:
    a = store.insert("characters", {"name": "A"})
    b = store.insert("characters", {"name": "B", "location_id": "loc-1"})
    c = store.insert("characters", {"name": "C", "location_id": "loc-1"})
    return a, b, c


def test_find_all_where_and_anded_conditions(store):
    a, b, c = _seed_characters(store)
    assert [r["id"] for r in store.find("characters", order_by="id")] == [a, b, c]
    assert [r["id"] for r in store.find("characters", {"location_id": "loc-1"})] == [b, c]
    assert [r["id"] for r in store.find("characters", {"location_id": "loc-1", "name": "C"})] == [c]
    assert [r["id"] for r in store.find("characters", {})] == [a, b, c]  # empty where == all
    assert store.find("characters", {"location_id": "nowhere"}) == []


def test_find_where_none_compiles_to_is_null(store):
    with_text = store.insert("chronicle", {"action_summary": "spoke", "verbatim_text": "hi"})
    without = store.insert("chronicle", {"action_summary": "walked"})
    assert [r["id"] for r in store.find("chronicle", {"verbatim_text": None})] == [without]
    assert [r["id"] for r in store.find("chronicle", {"verbatim_text": "hi"})] == [with_text]


def test_find_rejects_bad_where_and_order_by(store):
    with pytest.raises(ValueError, match="unknown column"):
        store.find("characters", {"nope": 1})
    with pytest.raises(ValueError, match="mapping"):
        store.find("characters", ["name"])
    with pytest.raises(ValueError, match="unknown column"):
        store.find("characters", order_by="nope")
    with pytest.raises(ValueError, match="order direction"):
        store.find("characters", order_by="name SIDEWAYS")
    with pytest.raises(ValueError, match="invalid order_by term"):
        store.find("characters", order_by="name DESC,")
    with pytest.raises(ValueError, match="order_by must be a string"):
        store.find("characters", order_by=7)


def test_find_order_limit_offset(store):
    rids = [
        store.insert("world_facts", {"statement": f"fact {i}", "established_turn": i})
        for i in range(1, 6)
    ]
    desc = store.find("world_facts", order_by="established_turn DESC")
    assert [r["established_turn"] for r in desc] == [5, 4, 3, 2, 1]
    multi = store.find("world_facts", order_by="pinned ASC, established_turn DESC", limit=2)
    assert [r["established_turn"] for r in multi] == [5, 4]
    page = store.find("world_facts", order_by="id", limit=2, offset=2)
    assert [r["id"] for r in page] == rids[2:4]
    tail = store.find("world_facts", order_by="id", offset=4)
    assert [r["id"] for r in tail] == rids[4:]
    assert store.find("world_facts", limit=0) == []
    assert store.find("world_facts", order_by="id", offset=99) == []


def test_find_rejects_bad_limit_and_offset(store):
    with pytest.raises(ValueError, match="limit must be an integer"):
        store.find("world_facts", limit="2")
    with pytest.raises(ValueError, match="limit must be >= 0"):
        store.find("world_facts", limit=-1)
    with pytest.raises(ValueError, match="offset must be >= 0"):
        store.find("world_facts", offset=-1)
    with pytest.raises(ValueError, match="offset must be an integer"):
        store.find("world_facts", offset=True)


def test_find_one(store):
    assert store.find_one("leads") is None
    first = store.insert("leads", {"title": "old", "stage": "unheard"})
    second = store.insert("leads", {"title": "new", "stage": "rumored"})
    assert store.find_one("leads")["id"] == first
    assert store.find_one("leads", order_by="id DESC")["id"] == second
    assert store.find_one("leads", {"stage": "rumored"})["id"] == second
    assert store.find_one("leads", {"stage": "resolved"}) is None


# --------------------------------------------------------------------------- #
# update / delete / count
# --------------------------------------------------------------------------- #

def test_update_returns_whether_a_row_changed(store):
    rid = store.insert("world", {"seed": "before"})
    assert store.update("world", rid, {"seed": "after", "day": 9}) is True
    row = store.get("world", rid)
    assert (row["seed"], row["day"]) == ("after", 9)
    assert store.update("world", rid + 1000, {"seed": "ghost"}) is False
    assert store.count("world") == 1  # update never inserts


def test_update_rejects_empty_data_and_unknown_column(store):
    rid = store.insert("world", {"seed": "x"})
    with pytest.raises(ValueError, match="at least one column"):
        store.update("world", rid, {})
    with pytest.raises(ValueError, match="unknown column"):
        store.update("world", rid, {"nope": 1})


def test_delete_returns_whether_a_row_went(store):
    rid = store.insert("npc_memory", {"npc_id": "marla", "statement": "x"})
    assert store.delete("npc_memory", rid) is True
    assert store.delete("npc_memory", rid) is False
    assert store.get("npc_memory", rid) is None
    assert store.count("npc_memory") == 0


def test_count_all_and_where(store):
    assert store.count("npcs") == 0
    store.insert("npcs", {"name": "Marla", "alive": 1})
    store.insert("npcs", {"name": "Gerd", "alive": 0})
    store.insert("npcs", {"name": "Toma", "alive": 1})
    assert store.count("npcs") == 3
    assert store.count("npcs", {"alive": 1}) == 2
    assert store.count("npcs", {"alive": 0, "name": "Gerd"}) == 1
    assert store.count("npcs", {"name": "Nobody"}) == 0
    with pytest.raises(ValueError, match="unknown column"):
        store.count("npcs", {"nope": 1})


# --------------------------------------------------------------------------- #
# escape hatches
# --------------------------------------------------------------------------- #

def test_sql_returns_row_dicts_and_binds_params(store):
    store.insert("world_facts", {"statement": "a", "source": "player", "tags": "[]"})
    store.insert("world_facts", {"statement": "b", "source": "narrator", "tags": "[]"})
    rows = store.sql("SELECT source, COUNT(*) AS n FROM world_facts GROUP BY source")
    assert sorted(rows, key=lambda r: r["source"]) == [
        {"source": "narrator", "n": 1},
        {"source": "player", "n": 1},
    ]
    bound = store.sql("SELECT * FROM world_facts WHERE source = ?", ("player",))
    assert [r["statement"] for r in bound] == ["a"]
    assert store.sql("UPDATE world_facts SET source = source") == []  # no rows to return


def test_sql_rejects_string_params(store):
    with pytest.raises(ValueError, match="sequence of values"):
        store.sql("SELECT * FROM world_facts WHERE source = ?", "player")


def test_exec_sql_returns_rowcount(store):
    for i in range(3):
        store.insert("world_facts", {"statement": f"f{i}"})
    assert store.exec_sql("UPDATE world_facts SET source = ?", ("system",)) == 3
    assert store.exec_sql("UPDATE world_facts SET source = ? WHERE id = ?", ("x", -1)) == 0
    assert store.exec_sql("DELETE FROM world_facts WHERE id = ?", (1,)) == 1
    assert store.exec_sql("CREATE TABLE one_off (a INTEGER)") == -1  # DDL touches no rows
    assert store.count("world_facts") == 2


# --------------------------------------------------------------------------- #
# validation
# --------------------------------------------------------------------------- #

UNKNOWN_TABLE_CALLS: list[Callable[[Store], object]] = [
    lambda s: s.insert("ghosts", {"name": "x"}),
    lambda s: s.get("ghosts", 1),
    lambda s: s.find("ghosts"),
    lambda s: s.find_one("ghosts"),
    lambda s: s.update("ghosts", 1, {"name": "x"}),
    lambda s: s.upsert("ghosts", {"name": "x"}, conflict="name"),
    lambda s: s.delete("ghosts", 1),
    lambda s: s.count("ghosts"),
]


@pytest.mark.parametrize("call", UNKNOWN_TABLE_CALLS)
def test_unknown_table_raises_value_error_everywhere(store, call):
    with pytest.raises(ValueError, match="unknown table"):
        call(store)


def test_key_value_tables_reject_id_addressing(store):
    store.insert("meta", {"key": "turn", "value": "3"})
    for call in (
        lambda: store.get("meta", 1),
        lambda: store.update("meta", 1, {"value": "4"}),
        lambda: store.delete("meta", 1),
    ):
        with pytest.raises(ValueError, match="no 'id' column"):
            call()
    assert store.find_one("meta", {"key": "turn"})["value"] == "3"


def test_values_are_bound_never_interpolated(store):
    hostile = "'; DROP TABLE world; --"
    rid = store.insert("world", {"seed": hostile})
    assert store.get("world", rid)["seed"] == hostile
    assert store.find("world", {"seed": hostile})[0]["id"] == rid
    assert store.find("world", {"seed": "' OR '1'='1"}) == []
    assert store.count("world") == 1  # table survived


# --------------------------------------------------------------------------- #
# upsert
# --------------------------------------------------------------------------- #

def test_upsert_insert_then_update_returns_stable_rowid(store):
    first = store.upsert("moods", {"npc_id": "marla", "valence": 0.5, "arousal": 0.1},
                         conflict="npc_id")
    second = store.upsert("moods", {"npc_id": "marla", "valence": -0.4, "arousal": 0.9},
                          conflict="npc_id")
    assert first == second
    assert store.count("moods") == 1
    row = store.find_one("moods", {"npc_id": "marla"})
    assert (row["valence"], row["arousal"]) == (-0.4, 0.9)


def test_upsert_conflict_only_columns_is_a_noop_on_collision(store):
    store.insert("meta", {"key": "schema_version", "value": "1"})
    rid = store.upsert("meta", {"key": "schema_version"}, conflict="key")
    assert store.count("meta") == 1
    assert store.find_one("meta", {"key": "schema_version"})["value"] == "1"
    rowid = store.sql("SELECT rowid AS rid FROM meta WHERE key = ?", ("schema_version",))[0]["rid"]
    assert rowid == rid


def test_upsert_rejects_bad_conflict_columns(store):
    with pytest.raises(ValueError, match="unknown column"):
        store.upsert("moods", {"npc_id": "x", "nope": 1}, conflict="npc_id")
    with pytest.raises(ValueError, match="conflict column"):
        store.upsert("moods", {"npc_id": "x"}, conflict="valence")
    with pytest.raises(ValueError, match="conflict column"):
        store.upsert("moods", {"npc_id": "x", "valence": 0.0}, conflict="not_a_column")


def test_upsert_requires_a_real_unique_constraint(store):
    store.insert("characters", {"name": "dup"})
    with pytest.raises(sqlite3.OperationalError, match="ON CONFLICT clause does not match"):
        store.upsert("characters", {"name": "dup", "location_id": "loc"}, conflict="name")


# --------------------------------------------------------------------------- #
# transactions
# --------------------------------------------------------------------------- #

def test_transaction_commits_on_success(store):
    with store.transaction():
        a = store.insert("world_facts", {"statement": "inside a"})
        b = store.insert("world_facts", {"statement": "inside b"})
    assert store.count("world_facts") == 2
    assert store.get("world_facts", a)["statement"] == "inside a"
    assert store.get("world_facts", b)["statement"] == "inside b"


def test_transaction_rollback_leaves_no_partial_writes(store):
    store.insert("world_facts", {"statement": "before"})
    with pytest.raises(RuntimeError, match="boom"):
        with store.transaction():
            store.insert("world_facts", {"statement": "never"})
            store.insert("world_facts", {"statement": "either"})
            raise RuntimeError("boom")
    assert [r["statement"] for r in store.find("world_facts")] == ["before"]


def test_transaction_rollback_covers_update_and_delete(store):
    rid = store.insert("npcs", {"name": "Marla", "location_id": "loc-1"})
    with pytest.raises(ValueError):
        with store.transaction():
            store.update("npcs", rid, {"location_id": "loc-9"})
            store.insert("npcs", {"name": "Ghost"})
            store.delete("npcs", rid)
            raise ValueError("abort")
    rows = store.find("npcs")
    assert len(rows) == 1
    assert (rows[0]["id"], rows[0]["location_id"]) == (rid, "loc-1")


def test_transaction_is_not_reentrant(store):
    with store.transaction():
        store.insert("world_facts", {"statement": "outer"})
        with pytest.raises(RuntimeError, match="not re-entrant"):
            with store.transaction():
                store.insert("world_facts", {"statement": "inner"})
        store.insert("world_facts", {"statement": "outer 2"})
    assert store.count("world_facts") == 2  # outer transaction still committed cleanly


# --------------------------------------------------------------------------- #
# migrations / multi-handle
# --------------------------------------------------------------------------- #

def test_migrations_idempotent_on_reopen_and_data_preserved(tmp_path):
    path = tmp_path / "reopen.sqlite"
    first = Store(path)
    rid = first.insert("world", {"seed": "persisted", "day": 12})
    first.close()

    second = Store(path)  # re-applies schema.sql
    assert second.get("world", rid)["seed"] == "persisted"
    assert second.count("world") == 1
    second.insert("world", {"seed": "second session"})
    second.close()

    third = Store(path)  # apply again, still no error, both rows there
    assert third.count("world") == 2
    assert third.sql("SELECT COUNT(*) AS n FROM sqlite_master WHERE type = 'table'")[0]["n"] == 16
    third.close()


def test_apply_migrations_false_skips_schema(tmp_path):
    path = tmp_path / "bare.sqlite"
    bare = Store(path, apply_migrations=False)
    try:
        assert bare.sql("SELECT name FROM sqlite_master WHERE type = 'table'") == []
        with pytest.raises(ValueError, match="unknown table"):
            bare.count("world")
    finally:
        bare.close()

    migrated = Store(path)  # same file, now migrated
    try:
        assert migrated.count("world") == 0
    finally:
        migrated.close()


def test_two_handles_on_one_file_see_each_others_writes(tmp_path):
    path = tmp_path / "shared.sqlite"
    a = Store(path)
    b = Store(path)
    try:
        rid = a.insert("leads", {"title": "from a"})
        assert b.get("leads", rid)["title"] == "from a"
        b.update("leads", rid, {"stage": "rumored"})
        assert a.get("leads", rid)["stage"] == "rumored"
        b.insert("leads", {"title": "from b"})
        assert a.count("leads") == 2
    finally:
        a.close()
        b.close()


def test_concurrent_writes_from_two_threads_do_not_corrupt(tmp_path):
    path = tmp_path / "threads.sqlite"
    Store(path).close()  # migrate once up front
    per_thread = 15
    errors: list[BaseException] = []
    barrier = threading.Barrier(2, timeout=10)

    def writer(tag: str) -> None:
        local = Store(path)
        try:
            barrier.wait()
            with local.transaction():
                for i in range(per_thread):
                    local.insert("world_facts", {"statement": f"{tag}-{i}", "source": tag})
        except BaseException as exc:  # noqa: BLE001 - surfaced below
            errors.append(exc)
        finally:
            local.close()

    threads = [threading.Thread(target=writer, args=(tag,)) for tag in ("a", "b")]
    for t in threads:
        t.start()
    for t in threads:
        t.join(timeout=30)
    assert errors == []
    assert all(not t.is_alive() for t in threads)

    check = Store(path)
    try:
        assert check.count("world_facts") == 2 * per_thread
        assert check.count("world_facts", {"source": "a"}) == per_thread
        assert check.count("world_facts", {"source": "b"}) == per_thread
        assert check.sql("PRAGMA integrity_check")[0]["integrity_check"] == "ok"
    finally:
        check.close()
