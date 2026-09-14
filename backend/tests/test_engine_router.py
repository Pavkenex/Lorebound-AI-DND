"""Engine pilot HTTP surface (phase 2 P3): flag gate, auth/scoping, runtime key.

Offline: the real app with the engine router registered, an in-memory SQLite for
the app rows, a throwaway ``ENGINE_DATA_DIR`` per test, and scripted transports
for the live paths (docs/INTEGRATION_PLAN.md §2, §4, §5).
"""
from __future__ import annotations

import io
import json
import logging
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any, ClassVar, Self
from urllib import error as urllib_error
from urllib import request as urllib_request
from uuid import uuid4

import pytest
from engine.models import ChatResponse, ProviderCaps, ProviderConfig, ToolCall
from engine.providers.registry import build_adapter, probe_capabilities
from engine.store import Store
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from app.core.config import settings
from app.core.database import Base, get_db
from app.main import app
from app.modules.auth.models import User  # noqa: F401  (register metadata)
from app.modules.campaign import models as cm  # noqa: F401
from app.modules.campaign import npc as _npc  # noqa: F401
from app.modules.campaign import story as _story  # noqa: F401
from app.modules.campaign import world as _world  # noqa: F401
from app.modules.character import models as _char  # noqa: F401
from app.modules.engine import bridge, paths
from app.modules.engine import router as engine_router
from app.modules.inventory import models as _inv  # noqa: F401
from app.modules.play import models as pm  # noqa: F401

test_engine = create_engine(
    "sqlite://", connect_args={"check_same_thread": False}, poolclass=StaticPool
)
TestingSession = sessionmaker(bind=test_engine, autoflush=False, autocommit=False)
Base.metadata.create_all(bind=test_engine)

#: Distinctive runtime key: any leak is greppable (ASCII — it travels as a header).
RUNTIME_KEY = "sk-p3-4f7a1c2e-b0bb1e-secret-live-key"

#: A well-formed campaign id that belongs to nobody (shape-valid, absent).
GHOST_ID = "00000000-0000-4000-8000-000000000000"


def _override():
    db = TestingSession()
    try:
        yield db
    finally:
        db.close()


@pytest.fixture()
def client():
    previous = app.dependency_overrides.get(get_db)
    app.dependency_overrides[get_db] = _override
    try:
        yield TestClient(app, raise_server_exceptions=False)
    finally:
        if previous is None:
            app.dependency_overrides.pop(get_db, None)
        else:
            app.dependency_overrides[get_db] = previous


@pytest.fixture(autouse=True)
def engine_data_dir(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    """Every test runs against a throwaway ENGINE_DATA_DIR (read at call time)."""
    target = tmp_path / "engine_data"
    monkeypatch.setattr(settings, "ENGINE_DATA_DIR", str(target))
    return target


@pytest.fixture(autouse=True)
def engine_mode_on(monkeypatch: pytest.MonkeyPatch) -> None:
    """The pilot is enabled by default here; deploys default it OFF."""
    monkeypatch.setattr(settings, "ENGINE_MODE", True)


# --------------------------------------------------------------------------- #
# Helpers
# --------------------------------------------------------------------------- #

#: Every endpoint of plan §5, for the flag-off / auth sweeps.
ENDPOINTS: tuple[tuple[str, str, dict | None], ...] = (
    ("GET", "/engine/campaigns", None),
    ("POST", "/engine/campaigns", {"name": "Pilot"}),
    ("POST", f"/engine/campaigns/{GHOST_ID}/turns", {"text": "look around"}),
    ("GET", f"/engine/campaigns/{GHOST_ID}/state", None),
    ("GET", "/engine/connection", None),
    ("PUT", "/engine/connection", {"provider": "stub"}),
    ("POST", "/engine/connection/check", None),
)


def _sweep_email(prefix: str, method: str, url: str) -> str:
    slug = url.strip("/").replace("/", "-")
    return f"{prefix}-{method.lower()}-{slug}@example.com"


def _register(client: TestClient, email: str) -> dict:
    r = client.post(
        "/auth/register",
        json={"email": email, "password": "password123", "display_name": email.split("@")[0]},
    )
    assert r.status_code == 201, r.text
    return {"Authorization": f"Bearer {r.json()['token']['access_token']}"}


def _create(client: TestClient, headers: dict, name: str = "Pilot") -> dict:
    r = client.post("/engine/campaigns", headers=headers, json={"name": name})
    assert r.status_code == 201, r.text
    return r.json()


def _connect_live(
    client: TestClient,
    headers: dict,
    *,
    provider: str = "openai",
    model: str = "gpt-4o-mini",
    base_url: str = "https://api.example.test/v1",
    timeout_s: int | None = None,
) -> dict:
    body: dict = {"provider": provider, "model": model, "base_url": base_url}
    if timeout_s is not None:
        body["timeout_s"] = timeout_s
    r = client.put("/engine/connection", headers=headers, json=body)
    assert r.status_code == 200, r.text
    return r.json()


def _db_text() -> str:
    """Every value in every app-DB table, flattened (the Postgres stand-in)."""
    db = TestingSession()
    try:
        chunks: list[str] = []
        for table in Base.metadata.sorted_tables:
            for row in db.execute(table.select()).fetchall():
                chunks.extend(str(value) for value in row)
        return "\n".join(chunks)
    finally:
        db.close()


def _data_dir_text(data_dir: Path) -> str:
    """Every byte under ENGINE_DATA_DIR (db + -wal/-shm), best-effort decoded."""
    if not data_dir.exists():
        return ""
    return "\n".join(
        path.read_bytes().decode("utf-8", "ignore")
        for path in sorted(data_dir.rglob("*"))
        if path.is_file()
    )


# --------------------------------------------------------------------------- #
# The route table + the flag gate (plan §5)
# --------------------------------------------------------------------------- #


def test_the_registered_surface_matches_plan_section_5():
    spec = app.openapi()
    engine_paths = {
        path: sorted(method.upper() for method in methods)
        for path, methods in spec["paths"].items()
        if path.startswith("/engine")
    }
    assert engine_paths == {
        "/engine/campaigns": ["GET", "POST"],
        "/engine/campaigns/{campaign_id}/state": ["GET"],
        "/engine/campaigns/{campaign_id}/turns": ["POST"],
        "/engine/connection": ["GET", "PUT"],
        "/engine/connection/check": ["POST"],
    }


@pytest.mark.parametrize(("method", "url", "body"), ENDPOINTS)
def test_flag_off_hides_every_path(client: TestClient, monkeypatch: pytest.MonkeyPatch,
                                   method: str, url: str, body: dict | None):
    monkeypatch.setattr(settings, "ENGINE_MODE", False)

    # Anonymous and authenticated alike: the surface simply does not exist.
    anon = client.request(method, url, json=body)
    assert anon.status_code == 404
    assert anon.json() == {"detail": "Not Found"}

    headers = _register(client, _sweep_email("flagoff", method, url))
    authed = client.request(method, url, headers=headers, json=body)
    assert authed.status_code == 404
    assert authed.json() == {"detail": "Not Found"}


def test_flag_off_404s_before_body_validation(client: TestClient, monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setattr(settings, "ENGINE_MODE", False)
    # An invalid body would be a 422 with the flag on; the guard runs first.
    r = client.post("/engine/campaigns", json={"name": "x" * 500})
    assert r.status_code == 404


@pytest.mark.parametrize(("method", "url", "body"), ENDPOINTS)
def test_every_path_requires_auth_when_the_flag_is_on(client: TestClient, method: str, url: str,
                                                      body: dict | None):
    r = client.request(method, url, json=body)
    assert r.status_code == 401


# --------------------------------------------------------------------------- #
# Stub flow: create -> turn -> state -> list
# --------------------------------------------------------------------------- #


def test_stub_flow_create_turn_state_list(client: TestClient):
    headers = _register(client, "router-flow@example.com")
    created = _create(client, headers, "The chronicle")
    assert set(created) == {"id", "name", "world", "last_turn_at"}
    assert (created["name"], created["world"], created["last_turn_at"]) == (
        "The chronicle", "demo", None,
    )
    campaign_id = created["id"]

    r = client.post(
        f"/engine/campaigns/{campaign_id}/turns",
        headers=headers,
        json={"text": "I take in the yard and the smell of salt on the wind"},
    )
    assert r.status_code == 200, r.text
    turn = r.json()
    assert turn["turn"] == 1
    assert turn["narration"].strip()
    assert all(set(entry) == {"npc_id", "name", "text"} for entry in turn["dialogue"])
    assert turn["capability"] == {
        "provider": "stub", "model": "stub", "mode": "stub",
        "native_tools": False, "degraded": False,
    }
    assert isinstance(turn["suggestions"], list)
    assert turn["state"]["turn"] == 1

    state = client.get(f"/engine/campaigns/{campaign_id}/state", headers=headers)
    assert state.status_code == 200
    assert state.json()["turn"] == 1
    assert state.json()["location"]["id"] == "yard"

    listing = client.get("/engine/campaigns", headers=headers)
    assert listing.status_code == 200
    rows = listing.json()
    assert [row["id"] for row in rows] == [campaign_id]
    assert set(rows[0]) == {"id", "name", "world", "last_turn_at"}
    assert rows[0]["last_turn_at"] is not None  # the turn stamped the side-row


def test_list_is_mine_only_played_first_and_engine_only(client: TestClient):
    a = _register(client, "router-list-a@example.com")
    b = _register(client, "router-list-b@example.com")

    first = _create(client, a, "First")
    second = _create(client, a, "Second")
    b_campaign = _create(client, b, "B only")
    # A legacy campaign is a campaigns row too — the pilot list must ignore it.
    legacy = client.post("/campaigns", headers=a, json={})
    assert legacy.status_code == 201, legacy.text

    r = client.post(
        f"/engine/campaigns/{first['id']}/turns",
        headers=a,
        json={"text": "I look around the yard"},
    )
    assert r.status_code == 200, r.text

    rows = client.get("/engine/campaigns", headers=a).json()
    assert [row["id"] for row in rows] == [first["id"], second["id"]]
    assert legacy.json()["id"] not in {row["id"] for row in rows}

    rows_b = client.get("/engine/campaigns", headers=b).json()
    assert [row["id"] for row in rows_b] == [b_campaign["id"]]

    # Cross-account: B can neither read nor play A's campaign (404, never 403).
    assert client.get(f"/engine/campaigns/{first['id']}/state", headers=b).status_code == 404
    assert (
        client.post(
            f"/engine/campaigns/{first['id']}/turns", headers=b, json={"text": "hello"}
        ).status_code
        == 404
    )


def test_unknown_or_legacy_campaigns_are_404(client: TestClient):
    headers = _register(client, "router-unknown@example.com")
    assert client.get(f"/engine/campaigns/{GHOST_ID}/state", headers=headers).status_code == 404
    assert (
        client.post(
            f"/engine/campaigns/{GHOST_ID}/turns", headers=headers, json={"text": "hello"}
        ).status_code
        == 404
    )
    assert client.get(f"/engine/campaigns/{uuid4()}/state", headers=headers).status_code == 404

    # A legacy campaign (owned, but no engine side-row) is not an engine one.
    legacy_id = client.post("/campaigns", headers=headers, json={}).json()["id"]
    assert (
        client.post(
            f"/engine/campaigns/{legacy_id}/turns", headers=headers, json={"text": "hello"}
        ).status_code
        == 404
    )
    assert client.get(f"/engine/campaigns/{legacy_id}/state", headers=headers).status_code == 404


def test_body_limits_are_enforced(client: TestClient):
    headers = _register(client, "router-limits@example.com")
    campaign_id = _create(client, headers)["id"]

    assert client.post("/engine/campaigns", headers=headers, json={"name": "n" * 201}).status_code == 422
    assert client.post("/engine/campaigns", headers=headers, json={"name": "n" * 200}).status_code == 201

    turns_url = f"/engine/campaigns/{campaign_id}/turns"
    assert client.post(turns_url, headers=headers, json={}).status_code == 422
    assert client.post(turns_url, headers=headers, json={"text": ""}).status_code == 422
    assert client.post(turns_url, headers=headers, json={"text": "t" * 2001}).status_code == 422


# --------------------------------------------------------------------------- #
# Runtime key flow (plan §4)
# --------------------------------------------------------------------------- #


class _ScriptedAdapter:
    """Fake live adapter (the P2 bridge-test shape): canary, then the delta envelope."""

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


def test_live_turn_without_a_key_asks_to_connect(client: TestClient):
    headers = _register(client, "router-nokey@example.com")
    campaign_id = _create(client, headers)["id"]
    _connect_live(client, headers, timeout_s=10)

    r = client.post(
        f"/engine/campaigns/{campaign_id}/turns", headers=headers, json={"text": "I look around"}
    )
    assert r.status_code == 400
    assert r.json() == {"detail": "connect_your_ai"}

    r = client.post(
        f"/engine/campaigns/{campaign_id}/turns",
        headers={**headers, "X-Provider-Key": "   "},
        json={"text": "I look around"},
    )
    assert r.status_code == 400
    assert r.json() == {"detail": "connect_your_ai"}

    store = Store(paths.campaign_db_path(campaign_id))
    try:
        assert store.count("turn_log") == 0  # nothing ran, nothing spent
    finally:
        store.close()


def test_turn_passes_the_runtime_key_through_and_persists_nowhere(
    client: TestClient, engine_data_dir: Path, monkeypatch: pytest.MonkeyPatch
):
    headers = _register(client, "router-key@example.com")
    campaign_id = _create(client, headers)["id"]
    _connect_live(client, headers)

    seen: dict[str, Any] = {}

    def fake_build_adapter(cfg: Any, *, api_key: str | None = None) -> _ScriptedAdapter:
        seen["cfg"] = cfg
        seen["key"] = api_key
        return _ScriptedAdapter(cfg, api_key)

    monkeypatch.setattr(bridge, "build_adapter", fake_build_adapter)

    r = client.post(
        f"/engine/campaigns/{campaign_id}/turns",
        headers={**headers, "X-Provider-Key": RUNTIME_KEY},
        json={"text": "I greet the woman by the gate"},
    )
    assert r.status_code == 200, r.text

    # The header became the bridge's runtime argument, with the stored prefs.
    assert seen["key"] == RUNTIME_KEY
    assert (seen["cfg"].model, seen["cfg"].base_url, seen["cfg"].api_mode) == (
        "gpt-4o-mini", "https://api.example.test/v1", "openai",
    )

    # The live payload passed through untouched.
    payload = r.json()
    assert payload["narration"].startswith("The yard holds its breath")
    assert payload["dialogue"] == [
        {"npc_id": "npc:1", "name": "Marla Quist", "text": "Mind the ruts."}
    ]
    assert payload["capability"] == {
        "provider": "openai", "model": "gpt-4o-mini", "mode": "live",
        "native_tools": True, "degraded": False,
    }

    # PROOF (§4): the key is in no response body, no app-DB row, no campaign file.
    assert RUNTIME_KEY not in r.text
    assert RUNTIME_KEY not in _db_text()
    assert RUNTIME_KEY not in _data_dir_text(engine_data_dir)


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


def test_provider_failure_is_502_scrubbed_and_never_logged(
    client: TestClient, engine_data_dir: Path, caplog: pytest.LogCaptureFixture
):
    _EchoAuthHandler.received = []
    server = ThreadingHTTPServer(("127.0.0.1", 0), _EchoAuthHandler)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    try:
        headers = _register(client, "router-fail@example.com")
        campaign_id = _create(client, headers)["id"]
        _connect_live(
            client, headers, base_url=f"http://127.0.0.1:{server.server_port}/v1", timeout_s=10
        )

        with caplog.at_level(logging.DEBUG):
            r = client.post(
                f"/engine/campaigns/{campaign_id}/turns",
                headers={**headers, "X-Provider-Key": RUNTIME_KEY},
                json={"text": "I look around the yard"},
            )

        assert r.status_code == 502, r.text
        assert RUNTIME_KEY not in r.text
        assert "***" in r.json()["detail"]  # the engine's redaction marker
        # ...and it really was sent: the hostile provider saw it in auth.
        assert any(RUNTIME_KEY in header for header in _EchoAuthHandler.received)

        # §4: the whole failing path leaves no key in any log record.
        assert RUNTIME_KEY not in caplog.text
        assert RUNTIME_KEY not in _db_text()
        assert RUNTIME_KEY not in _data_dir_text(engine_data_dir)
    finally:
        server.shutdown()
        server.server_close()


# --------------------------------------------------------------------------- #
# Connection prefs (non-secret only — no key field exists, by design)
# --------------------------------------------------------------------------- #


def test_connection_prefs_roundtrip_and_shape(client: TestClient):
    headers = _register(client, "router-prefs@example.com")

    got = client.get("/engine/connection", headers=headers)
    assert got.status_code == 200
    doc = got.json()
    assert set(doc) == {"provider", "base_url", "model", "timeout_s", "updated_at"}
    assert (doc["provider"], doc["base_url"], doc["model"], doc["timeout_s"]) == (
        "stub", "", "", 30,
    )
    assert not any(
        word in field
        for field in doc
        for word in ("key", "token", "secret", "password", "auth", "credential")
    )

    saved = client.put(
        "/engine/connection",
        headers=headers,
        json={
            "provider": "OPENAI",
            "base_url": "https://api.example.test/v1",
            "model": "gpt-4o-mini",
            "timeout_s": 45,
        },
    )
    assert saved.status_code == 200, saved.text
    saved_doc = saved.json()
    assert (saved_doc["provider"], saved_doc["base_url"], saved_doc["model"], saved_doc["timeout_s"]) == (
        "openai", "https://api.example.test/v1", "gpt-4o-mini", 45,
    )
    assert saved_doc["updated_at"]

    # Partial update: omitted fields are kept, not reset.
    partial = client.put("/engine/connection", headers=headers, json={"model": "gpt-4o"})
    assert partial.status_code == 200
    assert (
        partial.json()["provider"], partial.json()["model"], partial.json()["timeout_s"]
    ) == ("openai", "gpt-4o", 45)

    again = client.get("/engine/connection", headers=headers).json()
    assert again["model"] == "gpt-4o"  # persisted, not just echoed

    for bad in ({"provider": "carrier-pigeon"}, {"provider": "anthropic"},
                {"timeout_s": 0}, {"timeout_s": 10_000},
                {"model": "m" * 201}, {"base_url": "u" * 501}):
        r = client.put("/engine/connection", headers=headers, json=bad)
        assert r.status_code == 400, f"{bad}: {r.status_code} {r.text}"

    # A wrong TYPE is the HTTP layer's 422, before the bridge ever sees it.
    assert client.put(
        "/engine/connection", headers=headers, json={"timeout_s": "soon"}
    ).status_code == 422

    after = client.get("/engine/connection", headers=headers).json()
    assert (after["provider"], after["model"], after["timeout_s"]) == ("openai", "gpt-4o", 45)


def test_put_ignores_unknown_fields_gracefully_and_never_echoes_a_key(client: TestClient):
    headers = _register(client, "router-unknown-fields@example.com")

    # No key-shaped field exists. Unknown fields are ignored rather than
    # rejected, because a 422 body echoes the offending input — and a stray
    # api_key must never bounce back in any response (§4).
    r = client.put(
        "/engine/connection",
        headers=headers,
        json={"provider": "stub", "timeout_s": 20, "api_key": RUNTIME_KEY, "has_key": True},
    )
    assert r.status_code == 200, r.text
    assert RUNTIME_KEY not in r.text
    doc = r.json()
    assert set(doc) == {"provider", "base_url", "model", "timeout_s", "updated_at"}
    assert doc["timeout_s"] == 20
    assert RUNTIME_KEY not in _db_text()


# --------------------------------------------------------------------------- #
# Connection check: the capability canary over a fake transport
# --------------------------------------------------------------------------- #


class _FakeResponse:
    def __init__(self, payload: dict) -> None:
        self._raw = json.dumps(payload).encode("utf-8")

    def read(self) -> bytes:
        return self._raw

    def __enter__(self) -> Self:
        return self

    def __exit__(self, *exc: object) -> bool:
        return False


class _FakeTransport:
    """Fake urllib opener: scripted JSON outcomes, records what was sent."""

    def __init__(self, *outcomes: Any) -> None:
        self.outcomes = list(outcomes)
        self.requests: list[Any] = []

    def __call__(self, request: Any, timeout: float | None = None) -> _FakeResponse:
        self.requests.append(request)
        outcome = self.outcomes.pop(0)
        if callable(outcome):
            outcome = outcome(request)  # a scripted factory: raise, or return a body
        if isinstance(outcome, BaseException):
            raise outcome
        return _FakeResponse(outcome)


def _chat_completion(*, tool_calls: list[dict] | None = None, text: str = "") -> dict:
    message: dict = {"role": "assistant", "content": text}
    if tool_calls:
        message["tool_calls"] = tool_calls
    return {"choices": [{"message": message}], "model": "gpt-4o-mini"}


def _canary_tool_call() -> dict:
    return {
        "id": "call_1",
        "type": "function",
        "function": {"name": "report_capability", "arguments": json.dumps({"ok": True})},
    }


def _http_error_401(request: Any) -> urllib_error.HTTPError:
    auth = request.headers.get("Authorization") or ""
    body = json.dumps({"error": {"message": f"invalid api key: {auth}"}}).encode()
    return urllib_error.HTTPError(request.full_url, 401, "Unauthorized", None, io.BytesIO(body))


def test_connection_check_with_a_fake_transport(client: TestClient,
                                                monkeypatch: pytest.MonkeyPatch):
    headers = _register(client, "router-check@example.com")
    _connect_live(client, headers)
    transport = _FakeTransport(_chat_completion(tool_calls=[_canary_tool_call()]))
    monkeypatch.setattr(urllib_request, "urlopen", transport)

    r = client.post(
        "/engine/connection/check", headers={**headers, "X-Provider-Key": RUNTIME_KEY}
    )
    assert r.status_code == 200, r.text
    verdict = r.json()
    assert set(verdict) == {"reachable", "native_tools", "detail"}
    assert verdict["reachable"] is True
    assert verdict["native_tools"] is True
    assert RUNTIME_KEY not in r.text

    # The canary really went out: one POST to the stored base_url carrying the
    # canary tool, with the runtime key in the auth header (and nowhere else).
    assert len(transport.requests) == 1
    sent = transport.requests[0]
    assert sent.full_url == "https://api.example.test/v1/chat/completions"
    assert sent.headers["Authorization"] == f"Bearer {RUNTIME_KEY}"
    payload = json.loads(sent.data.decode("utf-8"))
    assert payload["model"] == "gpt-4o-mini"
    assert [tool["function"]["name"] for tool in payload["tools"]] == ["report_capability"]
    assert payload["tool_choice"] == "auto"


def test_connection_check_reports_degraded_without_native_tools(
    client: TestClient, monkeypatch: pytest.MonkeyPatch
):
    headers = _register(client, "router-check-degraded@example.com")
    _connect_live(client, headers)
    transport = _FakeTransport(_chat_completion(text="I cannot call tools, sorry."))
    monkeypatch.setattr(urllib_request, "urlopen", transport)

    r = client.post(
        "/engine/connection/check", headers={**headers, "X-Provider-Key": RUNTIME_KEY}
    )
    assert r.status_code == 200, r.text
    assert r.json()["reachable"] is True
    assert r.json()["native_tools"] is False
    assert "fallback" in r.json()["detail"]


def test_connection_check_reports_unreachable_scrubbed(client: TestClient,
                                                       monkeypatch: pytest.MonkeyPatch):
    headers = _register(client, "router-check-fail@example.com")
    _connect_live(client, headers)
    monkeypatch.setattr(
        urllib_request, "urlopen", _FakeTransport(_http_error_401)
    )

    r = client.post(
        "/engine/connection/check", headers={**headers, "X-Provider-Key": RUNTIME_KEY}
    )
    assert r.status_code == 200, r.text  # a verdict, never a failed request
    verdict = r.json()
    assert verdict["reachable"] is False
    assert verdict["native_tools"] is False
    assert RUNTIME_KEY not in r.text
    assert "***" in verdict["detail"]


def test_connection_check_survives_unexpected_probe_errors(
    client: TestClient, monkeypatch: pytest.MonkeyPatch
):
    headers = _register(client, "router-check-boom@example.com")
    _connect_live(client, headers)

    def exploding_transport(request: Any, timeout: float | None = None) -> None:
        raise RuntimeError(f"hostile transport echoed {RUNTIME_KEY}")

    monkeypatch.setattr(urllib_request, "urlopen", exploding_transport)
    r = client.post(
        "/engine/connection/check", headers={**headers, "X-Provider-Key": RUNTIME_KEY}
    )
    assert r.status_code == 200, r.text
    assert r.json()["reachable"] is False
    assert RUNTIME_KEY not in r.text


def test_connection_check_stub_needs_no_key(client: TestClient):
    headers = _register(client, "router-check-stub@example.com")
    r = client.post("/engine/connection/check", headers=headers)
    assert r.status_code == 200, r.text
    verdict = r.json()
    assert verdict["reachable"] is True
    assert verdict["native_tools"] is False
    assert "stub" in verdict["detail"]


def test_connection_check_live_without_key_asks_to_connect(client: TestClient):
    headers = _register(client, "router-check-nokey@example.com")
    _connect_live(client, headers)
    r = client.post("/engine/connection/check", headers=headers)
    assert r.status_code == 400
    assert r.json() == {"detail": "connect_your_ai"}


def test_canary_verdict_matches_the_engine_registry_probe(client: TestClient,
                                                          monkeypatch: pytest.MonkeyPatch):
    """The check's native-tools verdict is the registry's own predicate."""
    headers = _register(client, "router-check-parity@example.com")
    _connect_live(client, headers)
    cfg = ProviderConfig(
        name="openai", model="gpt-4o-mini",
        base_url="https://api.example.test/v1", api_mode="openai",
    )

    for body, expected in (
        (_chat_completion(tool_calls=[_canary_tool_call()]), True),
        (_chat_completion(text="no tools here"), False),
    ):
        mine_transport = _FakeTransport(body)
        monkeypatch.setattr(urllib_request, "urlopen", mine_transport)
        mine = client.post(
            "/engine/connection/check", headers={**headers, "X-Provider-Key": RUNTIME_KEY}
        ).json()

        their_transport = _FakeTransport(body)
        monkeypatch.setattr(urllib_request, "urlopen", their_transport)
        theirs = probe_capabilities(
            build_adapter(cfg, api_key=RUNTIME_KEY), cfg, store=None, force=True
        )

        assert mine["native_tools"] is theirs.native_tools is expected


# --------------------------------------------------------------------------- #
# Error map (plan §5)
# --------------------------------------------------------------------------- #


def test_bridge_error_map_covers_every_code():
    cases = {
        "connect_your_ai": (400, "connect_your_ai"),
        "invalid_prefs": (400, None),
        "unsupported_provider": (400, None),
        "unknown_world": (400, None),
        "bad_campaign_id": (400, None),
        "provider_error": (502, None),
        "no_campaign": (404, "campaign not found"),
        "not_an_engine_campaign": (404, "campaign not found"),
    }
    for code, (expected_status, expected_detail) in cases.items():
        mapped = engine_router._http_error(bridge.EngineBridgeError(code, detail=f"why {code}"))
        assert mapped.status_code == expected_status, code
        assert mapped.detail == (expected_detail or f"why {code}"), code

    # An unknown code is still a clean client error, never a 500.
    mapped = engine_router._http_error(bridge.EngineBridgeError("something-new", detail="huh"))
    assert (mapped.status_code, mapped.detail) == (400, "huh")

    # Defense in depth: even a detail that already carries the key is scrubbed.
    leaked = engine_router._http_error(
        bridge.EngineBridgeError("provider_error", detail=f"boom {RUNTIME_KEY}"),
        provider_key=RUNTIME_KEY,
    )
    assert RUNTIME_KEY not in leaked.detail
    assert "***" in leaked.detail
