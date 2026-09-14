"""Engine bridge (phase 2 P2): lifecycle, one-turn wrapper, runtime-key flow.

Offline: real SQLite for the app rows and the engine campaign files (a tmp
ENGINE_DATA_DIR monkeypatched into settings), stub narrators for the happy
paths, and a scripted adapter / a hostile loopback provider for the key-path
proofs (docs/INTEGRATION_PLAN.md §3–§4).
"""
from __future__ import annotations

import json
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any, ClassVar
from uuid import uuid4

import engine
import engine.play as engine_play
import pytest
from engine.models import ChatResponse, ProviderCaps, ToolCall
from engine.store import Store
from sqlalchemy import create_engine, event
from sqlalchemy.orm import Session, sessionmaker
from sqlalchemy.pool import StaticPool

from app.core.config import settings
from app.core.database import Base
from app.modules.auth.models import User
from app.modules.campaign.models import Campaign
from app.modules.engine import bridge, paths
from app.modules.engine.models import EngineConnectionRow
from app.modules.engine.paths import EnginePathError

test_engine = create_engine(
    "sqlite://", connect_args={"check_same_thread": False}, poolclass=StaticPool
)
TestingSession = sessionmaker(bind=test_engine, autoflush=False, autocommit=False)
Base.metadata.create_all(bind=test_engine)

#: Distinctive runtime key: any leak is greppable.
RUNTIME_KEY = "sk-p2-4f8a1c9e-never-stored"


@pytest.fixture(autouse=True)
def engine_data_dir(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    """Every test runs against a throwaway ENGINE_DATA_DIR (read at call time)."""
    target = tmp_path / "engine_data"
    monkeypatch.setattr(settings, "ENGINE_DATA_DIR", str(target))
    return target


@pytest.fixture()
def db():
    session = TestingSession()
    try:
        yield session
    finally:
        session.close()


def _user(db: Session, email: str) -> User:
    user = User(email=email, password_hash="x", display_name=email.split("@")[0])
    db.add(user)
    db.commit()
    db.refresh(user)
    return user


def _store(db_file: Path) -> Store:
    return Store(db_file)


def _data_dir_text(data_dir: Path) -> str:
    """Every byte under ENGINE_DATA_DIR (db + -wal/-shm), best-effort decoded."""
    if not data_dir.exists():
        return ""
    return "\n".join(
        path.read_bytes().decode("utf-8", "ignore")
        for path in sorted(data_dir.rglob("*"))
        if path.is_file()
    )


def _db_text(db: Session) -> str:
    """Every value in every table of the app DB, flattened (Postgres stand-in)."""
    chunks: list[str] = []
    for table in Base.metadata.sorted_tables:
        for row in db.execute(table.select()).fetchall():
            chunks.extend(str(value) for value in row)
    return "\n".join(chunks)


# --------------------------------------------------------------------------- #
# Campaign lifecycle
# --------------------------------------------------------------------------- #


def test_create_campaign_seeds_the_engine_db(db: Session, engine_data_dir: Path):
    user = _user(db, "bridge-create@example.com")
    campaign = bridge.create_campaign(db, user, "Pilot run", "demo")

    assert campaign.owner_user_id == user.id
    assert campaign.seed_key == "engine"
    row = bridge.get_engine_campaign(db, campaign.id)
    assert row is not None
    assert (row.world, row.engine_version) == ("demo", engine.__version__)
    assert row.last_turn_at is None

    db_file = paths.campaign_db_path(campaign.id)
    assert db_file.is_file()
    assert db_file.parent == engine_data_dir
    store = _store(db_file)
    try:
        assert store.count("world") == 1
        assert store.count("characters") == 1
        assert store.count("npcs") >= 1
        assert store.find_one("characters")["name"] == "Rell"
    finally:
        store.close()


def test_reseeding_an_existing_campaign_adds_nothing(db: Session, engine_data_dir: Path):
    user = _user(db, "bridge-reseed@example.com")
    campaign = bridge.create_campaign(db, user, "Reseed")
    bridge.take_turn(db, user.id, campaign.id, "I check the rope at my belt")
    db_file = paths.campaign_db_path(campaign.id)

    store = _store(db_file)
    try:
        before = store.count("turn_log")
    finally:
        store.close()
    assert before == 1

    bridge._seed_campaign_db(campaign.id, "demo")  # idempotent: store is not empty

    store = _store(db_file)
    try:
        assert store.count("world") == 1
        assert store.count("turn_log") == 1
        assert store.count("characters") == 1
    finally:
        store.close()


def test_unknown_world_is_rejected(db: Session, engine_data_dir: Path):
    user = _user(db, "bridge-world@example.com")
    with pytest.raises(bridge.EngineBridgeError) as excinfo:
        bridge.create_campaign(db, user, "Nope", "saltmarsh")
    assert excinfo.value.code == "unknown_world"
    assert bridge.get_engine_campaign(db, "00000000-0000-0000-0000-000000000000") is None


# --------------------------------------------------------------------------- #
# Stub turns
# --------------------------------------------------------------------------- #


def test_three_stub_turns_advance_the_campaign(db: Session, engine_data_dir: Path):
    user = _user(db, "bridge-turns@example.com")
    campaign = bridge.create_campaign(db, user, "Turns")
    row = bridge.get_engine_campaign(db, campaign.id)

    actions = (
        "I take in the yard and the smell of salt on the wind",
        "I count the coins in my purse",
        "I rest a while by the rope well",
    )
    for index, action in enumerate(actions, start=1):
        payload = bridge.take_turn(db, user.id, campaign.id, action)
        assert payload["turn"] == index
        assert payload["narration"].strip()
        assert isinstance(payload["dialogue"], list)
        assert all(set(entry) == {"npc_id", "name", "text"} for entry in payload["dialogue"])
        assert isinstance(payload["mechanics"]["verdict_line"], str)
        assert isinstance(payload["suggestions"], list)
        assert payload["capability"] == {
            "provider": "stub",
            "model": "stub",
            "mode": "stub",
            "native_tools": False,
            "degraded": False,
        }
        assert payload["state"]["turn"] == index
        assert isinstance(payload["system_lines"], list)

    db.refresh(row)
    assert row.last_turn_at is not None

    store = _store(paths.campaign_db_path(campaign.id))
    try:
        assert store.count("turn_log") == 3
    finally:
        store.close()

    snapshot = bridge.get_state(campaign.id)
    assert snapshot["turn"] == 3
    assert snapshot["location"]["id"] == "yard"
    assert snapshot["hp"] == 11
    assert [npc["name"] for npc in snapshot["present_npcs"]] == ["Marla Quist"]


def test_suggestions_are_open_known_leads(db: Session, engine_data_dir: Path):
    user = _user(db, "bridge-suggest@example.com")
    campaign = bridge.create_campaign(db, user, "Leads")
    payload = bridge.take_turn(db, user.id, campaign.id, "I look around the yard")
    assert "The missing salt shipment" in payload["suggestions"]  # accepted
    assert "The gatehouse ledger" in payload["suggestions"]  # rumored
    assert "What the well keeps" not in payload["suggestions"]  # unheard: not a move yet


def test_get_state_never_creates_a_campaign(db: Session, engine_data_dir: Path):
    with pytest.raises(bridge.EngineBridgeError) as excinfo:
        bridge.get_state(str(uuid4()))
    assert excinfo.value.code == "no_campaign"
    assert list(engine_data_dir.rglob("*.db")) == []


def test_take_turn_needs_an_engine_campaign(db: Session, engine_data_dir: Path):
    user = _user(db, "bridge-legacy@example.com")
    legacy = Campaign(owner_user_id=user.id, name="Legacy run", seed_key="hollow_crown")
    db.add(legacy)
    db.commit()

    with pytest.raises(bridge.EngineBridgeError) as excinfo:
        bridge.take_turn(db, user.id, legacy.id, "I look around")
    assert excinfo.value.code == "not_an_engine_campaign"


# --------------------------------------------------------------------------- #
# Runtime-key flow (plan §4 — the hard rules)
# --------------------------------------------------------------------------- #


class _ScriptedAdapter:
    """Fake live adapter: canary tool call, then the Pass B delta envelope."""

    name = "openai"

    def __init__(self, cfg: Any, api_key: str | None = None) -> None:
        self.cfg = cfg
        self.api_key = api_key
        self.caps = ProviderCaps(native_tools=True)

    def capabilities(self) -> ProviderCaps:
        return self.caps

    def secrets(self) -> tuple[str, ...]:
        return (self.api_key,) if self.api_key else ()

    def complete(self, request: Any) -> ChatResponse:
        names = {tool["function"]["name"] for tool in (request.tools or [])}
        if "report_capability" in names:
            return ChatResponse(
                text="",
                tool_calls=[ToolCall(name="report_capability", arguments={"ok": True})],
            )
        return ChatResponse(
            text="",
            tool_calls=[
                ToolCall(
                    name="propose_state_deltas",
                    arguments={
                        "narration": "The yard holds its breath while gulls wheel over the gate.",
                        "npc_dialogue": [
                            {"npc_id": "npc:1", "name": "Marla Quist", "text": "Mind the ruts."}
                        ],
                        "deltas": [],
                    },
                )
            ],
        )


def test_live_provider_without_a_key_asks_to_connect(db: Session, engine_data_dir: Path):
    user = _user(db, "bridge-nokey@example.com")
    campaign = bridge.create_campaign(db, user, "No key")
    bridge.save_connection(
        db, user.id, provider="openai", model="gpt-4o-mini",
        base_url="https://api.example.test/v1",
    )

    with pytest.raises(bridge.EngineBridgeError) as excinfo:
        bridge.take_turn(db, user.id, campaign.id, "I look around the yard")
    assert excinfo.value.code == "connect_your_ai"
    assert str(excinfo.value) == "connect_your_ai"
    assert excinfo.value.detail == ""

    with pytest.raises(bridge.EngineBridgeError, match="connect_your_ai"):
        bridge.take_turn(db, user.id, campaign.id, "I look around", provider_key="   ")

    store = _store(paths.campaign_db_path(campaign.id))
    try:
        assert store.count("turn_log") == 0  # nothing ran, nothing spent
    finally:
        store.close()


def test_runtime_key_reaches_the_adapter_and_persists_nowhere(
    db: Session, engine_data_dir: Path, monkeypatch: pytest.MonkeyPatch
):
    user = _user(db, "bridge-key@example.com")
    campaign = bridge.create_campaign(db, user, "Key path")
    bridge.save_connection(
        db, user.id, provider="openai", model="gpt-4o-mini",
        base_url="https://api.example.test/v1",
    )

    seen: dict[str, Any] = {}

    def fake_build_adapter(cfg: Any, *, api_key: str | None = None) -> _ScriptedAdapter:
        seen["cfg"] = cfg
        seen["key"] = api_key
        return _ScriptedAdapter(cfg, api_key)

    monkeypatch.setattr(bridge, "build_adapter", fake_build_adapter)

    payload = bridge.take_turn(db, user.id, campaign.id, "I greet Marla", provider_key=RUNTIME_KEY)

    # The key reached the adapter as a runtime argument, with the stored prefs.
    assert seen["key"] == RUNTIME_KEY
    assert (seen["cfg"].name, seen["cfg"].api_mode) == ("openai", "openai")
    assert seen["cfg"].model == "gpt-4o-mini"
    assert seen["cfg"].base_url == "https://api.example.test/v1"

    # The live path produced a real payload on the scripted envelope.
    assert payload["narration"].startswith("The yard holds its breath")
    assert payload["dialogue"] == [
        {"npc_id": "npc:1", "name": "Marla Quist", "text": "Mind the ruts."}
    ]
    assert payload["capability"] == {
        "provider": "openai",
        "model": "gpt-4o-mini",
        "mode": "live",
        "native_tools": True,
        "degraded": False,
    }

    # PROOF (plan §4): the key is in neither the response, nor a campaign file,
    # nor any app-DB row — including the capability cache the probe wrote.
    assert RUNTIME_KEY not in json.dumps(payload)
    assert RUNTIME_KEY not in _data_dir_text(engine_data_dir)
    assert RUNTIME_KEY not in _db_text(db)

    store = _store(paths.campaign_db_path(campaign.id))
    try:
        cached = store.find_one("provider_caps")
    finally:
        store.close()
    assert cached is not None
    assert RUNTIME_KEY not in json.dumps(cached, default=str)
    assert cached["provider_key"] == "openai|gpt-4o-mini|https://api.example.test/v1|openai"


class _EchoAuthHandler(BaseHTTPRequestHandler):
    """Hostile provider: answers 401 and echoes the Authorization header back."""

    received: ClassVar[list[str]] = []

    def do_POST(self) -> None:
        length = int(self.headers.get("content-length") or 0)
        self.rfile.read(length)
        authorization = str(self.headers.get("authorization") or "")
        type(self).received.append(authorization)
        body = json.dumps({"error": {"message": f"invalid api key: {authorization}"}}).encode()
        self.send_response(401)
        self.send_header("content-type", "application/json")
        self.send_header("content-length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, *args: Any) -> None:  # keep the test output clean
        return


def test_provider_failure_is_scrubbed(db: Session, engine_data_dir: Path):
    _EchoAuthHandler.received = []
    server = ThreadingHTTPServer(("127.0.0.1", 0), _EchoAuthHandler)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    try:
        user = _user(db, "bridge-fail@example.com")
        campaign = bridge.create_campaign(db, user, "Failing provider")
        bridge.save_connection(
            db, user.id, provider="openai", model="gpt-4o-mini", timeout_s=10,
            base_url=f"http://127.0.0.1:{server.server_port}/v1",
        )

        with pytest.raises(bridge.EngineBridgeError) as excinfo:
            bridge.take_turn(db, user.id, campaign.id, "I look around", provider_key=RUNTIME_KEY)

        assert excinfo.value.code == "provider_error"
        assert RUNTIME_KEY not in str(excinfo.value)
        assert RUNTIME_KEY not in (excinfo.value.detail or "")
        assert "***" in (excinfo.value.detail or "")  # the engine's redaction marker
        # ...and it really was sent: the provider saw it in the auth header.
        assert any(RUNTIME_KEY in header for header in _EchoAuthHandler.received)

        assert RUNTIME_KEY not in _data_dir_text(engine_data_dir)
        assert RUNTIME_KEY not in _db_text(db)
    finally:
        server.shutdown()
        server.server_close()


def test_connection_table_has_no_key_shaped_column():
    columns = set(EngineConnectionRow.__table__.columns.keys())
    assert columns == {"user_id", "provider", "base_url", "model", "timeout_s", "updated_at"}
    assert not any(
        word in column
        for column in columns
        for word in ("key", "token", "secret", "password", "auth", "credential")
    )


# --------------------------------------------------------------------------- #
# Connection prefs CRUD
# --------------------------------------------------------------------------- #


def test_connection_row_creation_is_race_safe(db: Session, engine_data_dir: Path):
    """Two concurrent first turns: the loser re-reads instead of failing."""
    user = _user(db, "bridge-race@example.com")
    assert db.get(EngineConnectionRow, user.id) is None  # the row does not exist yet

    racer_done = {"value": False}

    def create_the_row_first(session: Session) -> None:
        if racer_done["value"]:
            return
        racer_done["value"] = True
        other = TestingSession()  # a second request inserting between read and commit
        try:
            other.add(EngineConnectionRow(user_id=user.id))
            other.commit()
        finally:
            other.close()

    event.listen(db, "before_commit", create_the_row_first)
    try:
        row = bridge.connection_settings(db, user.id)  # its INSERT loses the race
    finally:
        event.remove(db, "before_commit", create_the_row_first)

    assert racer_done["value"] is True
    assert row.user_id == user.id
    assert (row.provider, row.base_url, row.model, row.timeout_s) == ("stub", "", "", 30)


def test_connection_prefs_crud(db: Session, engine_data_dir: Path):
    user = _user(db, "bridge-prefs@example.com")

    row = bridge.connection_settings(db, user.id)
    assert (row.provider, row.base_url, row.model, row.timeout_s) == ("stub", "", "", 30)
    assert bridge.connection_settings(db, user.id) is row  # same row, not a second one

    saved = bridge.save_connection(
        db, user.id, provider="OPENAI", base_url=" https://api.example.test/v1 ",
        model=" gpt-4o-mini ", timeout_s="45",
    )
    assert (saved.provider, saved.base_url, saved.model, saved.timeout_s) == (
        "openai", "https://api.example.test/v1", "gpt-4o-mini", 45,
    )

    db.expire_all()  # prove it persisted, not just edited in the identity map
    again = bridge.connection_settings(db, user.id)
    assert (again.provider, again.model, again.timeout_s) == ("openai", "gpt-4o-mini", 45)

    view = bridge.connection_view(again)
    assert set(view) == {"provider", "base_url", "model", "timeout_s", "updated_at"}
    assert view["updated_at"]

    for kwargs in (
        {"provider": "carrier-pigeon"},
        {"provider": "anthropic"},
        {"timeout_s": 0},
        {"timeout_s": 10_000},
        {"timeout_s": "soon"},
        {"model": "m" * 201},
    ):
        with pytest.raises(bridge.EngineBridgeError):
            bridge.save_connection(db, user.id, **kwargs)

    db.rollback()
    db.expire_all()
    kept = bridge.connection_settings(db, user.id)
    assert (kept.provider, kept.model, kept.timeout_s) == ("openai", "gpt-4o-mini", 45)


# --------------------------------------------------------------------------- #
# Campaign id validation (no traversal)
# --------------------------------------------------------------------------- #


def test_bad_campaign_ids_cannot_reach_the_filesystem(engine_data_dir: Path):
    for bad in (
        "../../etc/passwd",
        "..",
        "not-a-uuid",
        "",
        "a" * 35,
        "a" * 37,
        "0123456789abcdef0123456789abcdef/",
        "a" * 35 + ".",
    ):
        with pytest.raises(EnginePathError):
            paths.campaign_db_path(bad)

    with pytest.raises(bridge.EngineBridgeError) as excinfo:
        bridge.get_state("../../etc/passwd")
    assert excinfo.value.code == "bad_campaign_id"
    assert not (engine_data_dir.parent / "etc").exists()
    assert not any(engine_data_dir.parent.rglob("passwd"))


def test_take_turn_rejects_a_bad_id(db: Session, engine_data_dir: Path):
    user = _user(db, "bridge-badid@example.com")
    with pytest.raises(bridge.EngineBridgeError) as excinfo:
        bridge.take_turn(db, user.id, "../../../etc/passwd", "hello")
    assert excinfo.value.code == "bad_campaign_id"
    assert paths.campaign_db_path("a" * 36).parent == engine_data_dir


# --------------------------------------------------------------------------- #
# Concurrency: one writer per campaign
# --------------------------------------------------------------------------- #


def test_lock_serializes_turns_on_one_campaign(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    # A file-backed app DB so each thread can hold its own session (Postgres-like),
    # unlike the module's shared StaticPool connection.
    app_engine = create_engine(
        f"sqlite:///{tmp_path / 'app.db'}",
        connect_args={"check_same_thread": False, "timeout": 10},
    )
    Base.metadata.create_all(bind=app_engine)
    factory = sessionmaker(bind=app_engine, autoflush=False, autocommit=False)

    setup = factory()
    try:
        user = _user(setup, "bridge-lock@example.com")
        campaign = bridge.create_campaign(setup, user, "Locked")
        user_id, campaign_id = user.id, campaign.id
    finally:
        setup.close()

    # Lock granularity: one lock object per campaign, not one global lock.
    assert bridge._campaign_lock(campaign_id) is bridge._campaign_lock(campaign_id)
    assert bridge._campaign_lock(campaign_id) is not bridge._campaign_lock(str(uuid4()))

    active = {"now": 0, "peak": 0}
    guard = threading.Lock()
    original_act = engine_play.PlaySession.act

    def slow_act(self: Any, text: str):  # take_turn holds the campaign lock here
        with guard:
            active["now"] += 1
            active["peak"] = max(active["peak"], active["now"])
        try:
            time.sleep(0.05)
            return original_act(self, text)
        finally:
            with guard:
                active["now"] -= 1

    monkeypatch.setattr(engine_play.PlaySession, "act", slow_act)

    results: list[dict] = []
    failures: list[BaseException] = []

    def run(text: str) -> None:
        session = factory()
        try:
            results.append(bridge.take_turn(session, user_id, campaign_id, text))
        except BaseException as exc:  # noqa: BLE001 - asserted below
            failures.append(exc)
        finally:
            session.close()

    threads = [
        threading.Thread(target=run, args=(f"I look at the well, part {index}",))
        for index in range(2)
    ]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join(timeout=30)

    assert failures == []
    assert sorted(payload["turn"] for payload in results) == [1, 2]
    assert all(payload["narration"].strip() for payload in results)
    assert active["peak"] == 1  # two turns on one campaign never overlapped


def test_a_deleted_campaign_file_reseeds_on_the_next_turn(db: Session, engine_data_dir: Path):
    user = _user(db, "bridge-heal@example.com")
    campaign = bridge.create_campaign(db, user, "Heal")
    db_file = paths.campaign_db_path(campaign.id)
    db_file.unlink()

    payload = bridge.take_turn(db, user.id, campaign.id, "I look around the yard")
    assert payload["narration"].strip()
    assert db_file.is_file()  # reseeded rather than failing the turn
