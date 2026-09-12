"""AI provider settings: CRUD, key secrecy, test endpoint, resolution, /act wiring."""
from __future__ import annotations

import itertools
import json
import threading
from http.server import BaseHTTPRequestHandler, HTTPServer

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from app.core.database import Base, get_db
from app.main import app
from app.modules.ai import settings_store as store
from app.modules.ai.providers import OpenAICompatibleProvider, StubProvider
from app.modules.auth.models import User
from app.modules.campaign import models as _cm  # noqa: F401
from app.modules.campaign import npc as _npc  # noqa: F401
from app.modules.campaign import story as _story  # noqa: F401
from app.modules.campaign import world as _world  # noqa: F401
from app.modules.character import models as _char  # noqa: F401
from app.modules.inventory import models as _inv  # noqa: F401
from app.modules.play import models as _pm  # noqa: F401

engine = create_engine("sqlite://", connect_args={"check_same_thread": False}, poolclass=StaticPool)
TestingSession = sessionmaker(bind=engine, autoflush=False, autocommit=False)
Base.metadata.create_all(bind=engine)

#: The in-memory DB is shared across tests in one process; emails must be unique.
_EMAIL_SEQ = itertools.count(1)


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
def _clean_env(monkeypatch):
    for var in ("AI_PROVIDER", "OPENAI_COMPAT_BASE_URL", "OPENAI_COMPAT_MODEL",
                "OPENAI_COMPAT_API_KEY", "OPENAI_COMPAT_TIMEOUT_S"):
        monkeypatch.delenv(var, raising=False)


class _FakeOpenAI:
    """Local chat-completions stub that records requests."""

    def __init__(self, reply: str = "pong", status: int = 200, body: dict | None = None):
        self.reply = reply
        self.status = status
        self.body = body
        self.requests: list[dict] = []

    def start(self) -> HTTPServer:
        captured = self

        class Handler(BaseHTTPRequestHandler):
            def do_POST(self):
                length = int(self.headers.get("Content-Length", 0))
                raw = self.rfile.read(length) if length else b"{}"
                captured.requests.append({
                    "path": self.path,
                    "authorization": self.headers.get("Authorization"),
                    "body": json.loads(raw.decode("utf-8")),
                })
                payload = captured.body or {
                    "choices": [{"message": {"role": "assistant", "content": captured.reply}}],
                    "usage": {"prompt_tokens": 5, "completion_tokens": 3},
                }
                data = json.dumps(payload).encode("utf-8")
                self.send_response(captured.status)
                self.send_header("Content-Type", "application/json")
                self.send_header("Content-Length", str(len(data)))
                self.end_headers()
                self.wfile.write(data)

            def log_message(self, format, *args):
                pass

        server = HTTPServer(("127.0.0.1", 0), Handler)
        self._server = server
        threading.Thread(target=server.serve_forever, daemon=True).start()
        return server

    @property
    def base_url(self) -> str:
        return f"http://127.0.0.1:{self._server.server_port}"


@pytest.fixture()
def fake_openai():
    fake = _FakeOpenAI()
    fake.start()
    try:
        yield fake
    finally:
        fake._server.shutdown()


def _register(client: TestClient, email: str | None = None) -> dict:
    email = email or f"player{next(_EMAIL_SEQ)}@example.com"
    r = client.post(
        "/auth/register",
        json={"email": email, "password": "password123", "display_name": "P"},
    )
    assert r.status_code == 201, r.text
    return {"Authorization": f"Bearer {r.json()['token']['access_token']}"}


def test_default_doc_is_stub_unconfigured(client):
    headers = _register(client)
    r = client.get("/ai/settings", headers=headers)
    assert r.status_code == 200, r.text
    doc = r.json()
    assert doc["provider"] is None
    assert doc["configured"] is False
    assert doc["has_key"] is False
    assert doc["active_provider"] == "stub"
    assert doc["active_source"] == "default"
    assert doc["env_provider"] == "stub"


def test_save_and_read_back_never_echoes_key(client):
    headers = _register(client)
    r = client.put(
        "/ai/settings",
        headers=headers,
        json={
            "provider": "openai-compatible",
            "base_url": "https://api.example.com/v1/",
            "model": "my-model",
            "api_key": "sekret-key",
            "timeout_s": 12,
        },
    )
    assert r.status_code == 200, r.text
    assert "sekret" not in r.text
    doc = r.json()
    assert doc["provider"] == "openai-compatible"
    assert doc["configured"] is True
    assert doc["base_url"] == "https://api.example.com/v1"  # trailing slash trimmed
    assert doc["model"] == "my-model"
    assert doc["has_key"] is True
    assert doc["timeout_s"] == 12
    assert doc["active_provider"] == "openai-compatible"
    assert doc["active_source"] == "settings"

    again = client.get("/ai/settings", headers=headers).json()
    assert again == doc
    assert "sekret" not in client.get("/ai/settings", headers=headers).text


def test_blank_key_keeps_stored_key(client):
    headers = _register(client)
    base = {
        "provider": "openai-compatible",
        "base_url": "https://api.example.com/v1",
        "model": "m",
    }
    assert client.put("/ai/settings", headers=headers, json={**base, "api_key": "sekret"}).status_code == 200
    r = client.put("/ai/settings", headers=headers, json={**base, "model": "m2"})
    assert r.status_code == 200, r.text
    assert r.json()["has_key"] is True
    assert r.json()["model"] == "m2"

    cleared = client.put(
        "/ai/settings", headers=headers, json={**base, "clear_key": True}
    ).json()
    assert cleared["has_key"] is False


def test_invalid_base_url_and_missing_model_rejected(client):
    headers = _register(client)
    bad = client.put(
        "/ai/settings",
        headers=headers,
        json={"provider": "openai-compatible", "base_url": "ftp://nope", "model": "m"},
    )
    assert bad.status_code == 400
    missing = client.put(
        "/ai/settings",
        headers=headers,
        json={"provider": "openai-compatible", "base_url": "http://ok.example", "model": " "},
    )
    assert missing.status_code == 400


def test_test_endpoint_without_config_reports_ok_false(client):
    headers = _register(client)
    r = client.post("/ai/settings/test", headers=headers, json={})
    assert r.status_code == 200, r.text
    assert r.json()["ok"] is False
    assert "error" in r.json()


def test_test_endpoint_uses_saved_key_and_draft_overrides(client, fake_openai):
    headers = _register(client)
    saved = client.put(
        "/ai/settings",
        headers=headers,
        json={
            "provider": "openai-compatible",
            "base_url": "https://unreachable.invalid/v1",
            "model": "saved-model",
            "api_key": "sekret-key",
        },
    )
    assert saved.status_code == 200

    # Draft override: local server URL + model, saved key must ride along.
    r = client.post(
        "/ai/settings/test",
        headers=headers,
        json={"base_url": fake_openai.base_url, "model": "draft-model"},
    )
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["ok"] is True, body
    assert body["model"] == "draft-model"
    assert body["reply"] == "pong"
    assert "latency_ms" in body

    req = fake_openai.requests[0]
    assert req["path"] == "/chat/completions"
    assert req["authorization"] == "Bearer sekret-key"
    assert req["body"]["model"] == "draft-model"


def test_test_endpoint_reports_provider_error(client):
    fake = _FakeOpenAI(status=500, body={"error": "boom"})
    fake.start()
    try:
        headers = _register(client)
        r = client.post(
            "/ai/settings/test",
            headers=headers,
            json={"base_url": fake.base_url, "model": "m", "api_key": ""},
        )
        assert r.status_code == 200, r.text
        body = r.json()
        assert body["ok"] is False
        assert "error" in body
    finally:
        fake._server.shutdown()


def test_resolution_precedence(client, fake_openai, monkeypatch):
    db = TestingSession()
    try:
        user = User(
            email=f"direct{next(_EMAIL_SEQ)}@example.com", password_hash="x", display_name="P2"
        )
        db.add(user)
        db.commit()

        # No row, no env -> stub.
        assert isinstance(store.resolve_provider(db, user.id), StubProvider)

        # No row, env openai-compatible -> env provider wins.
        monkeypatch.setenv("AI_PROVIDER", "openai-compatible")
        monkeypatch.setenv("OPENAI_COMPAT_BASE_URL", "https://env.example/v1")
        monkeypatch.setenv("OPENAI_COMPAT_MODEL", "env-model")
        prov = store.resolve_provider(db, user.id)
        assert isinstance(prov, OpenAICompatibleProvider)
        assert prov.model_name == "env-model"

        # Saved custom row beats env.
        store.save_settings(
            db, user.id,
            provider="openai-compatible", base_url=fake_openai.base_url, model="saved",
            api_key="k",
        )
        prov = store.resolve_provider(db, user.id)
        assert isinstance(prov, OpenAICompatibleProvider)
        assert prov.model_name == "saved"
        assert prov.base_url == fake_openai.base_url

        # Saved stub row beats env too (explicit choice).
        store.save_settings(db, user.id, provider="stub")
        assert isinstance(store.resolve_provider(db, user.id), StubProvider)
        doc = store.settings_doc(db, user.id)
        assert doc["active_provider"] == "stub"
        assert doc["active_source"] == "settings"
    finally:
        db.close()


def test_act_uses_saved_custom_provider(client, fake_openai):
    headers = _register(client, email="act@example.com")
    c = client.post("/campaigns", headers=headers, json={})
    assert c.status_code == 201, c.text

    saved = client.put(
        "/ai/settings",
        headers=headers,
        json={
            "provider": "openai-compatible",
            "base_url": fake_openai.base_url,
            "model": "story-model",
            "api_key": "act-key",
        },
    )
    assert saved.status_code == 200, saved.text

    # The narrator expects a JSON NarratorOutput; serve one.
    fake_openai.reply = json.dumps(
        {"narration": "The rafters keep their secrets; the dust settles in slow ribbons."}
    )

    r = client.post(
        "/act",
        headers={**headers, "Idempotency-Key": "act-1"},
        json={"text": "I hum a quiet tune to the rafters"},
    )
    assert r.status_code == 200, r.text
    payload = r.json()
    assert payload["narration"] == "The rafters keep their secrets; the dust settles in slow ribbons."

    assert fake_openai.requests, "custom provider was never called"
    req = fake_openai.requests[0]
    assert req["authorization"] == "Bearer act-key"
    assert req["body"]["model"] == "story-model"
