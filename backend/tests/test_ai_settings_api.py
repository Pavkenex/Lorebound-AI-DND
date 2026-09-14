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
from app.modules.ai.providers import OpenAICompatibleProvider
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


def test_unconfigured_doc_reports_not_connected(client):
    """No row, no env: the doc says NOT CONNECTED — there is no stub to claim (P12)."""
    headers = _register(client)
    r = client.get("/ai/settings", headers=headers)
    assert r.status_code == 200, r.text
    doc = r.json()
    assert doc["provider"] is None
    assert doc["configured"] is False
    assert doc["has_key"] is False
    assert doc["connected"] is False
    assert doc["active_provider"] == ""
    assert doc["active_source"] == "default"
    assert doc["active_reason"] == "unset"
    assert doc["env_provider"] == ""
    assert doc["providers"] == ["openai-compatible"]


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


def _assert_doc_matches_runtime(db, user) -> None:
    """The panel's claim IS the play path's provider — both directions (P12)."""
    conn = store.resolve(db, user.id)
    runtime = store.resolve_provider(db, user.id)
    doc = store.settings_doc(db, user.id)
    assert conn.connected == (runtime is not None)
    assert (conn.provider is None) == (runtime is None)
    assert doc["connected"] is conn.connected
    assert doc["active_provider"] == conn.name
    assert doc["active_source"] == conn.source
    assert doc["active_reason"] == conn.reason
    if conn.connected:
        assert doc["active_provider"] != ""
        assert doc["active_model"] == conn.model
    else:
        assert doc["active_provider"] == ""
        assert "stub" not in doc["active_provider"]


def test_resolution_is_one_truth_across_surfaces(client, fake_openai, monkeypatch):
    db = TestingSession()
    try:
        user = User(
            email=f"direct{next(_EMAIL_SEQ)}@example.com", password_hash="x", display_name="P2"
        )
        db.add(user)
        db.commit()

        # 1. Nothing anywhere -> not connected, and the panel says so.
        assert store.resolve_provider(db, user.id) is None
        assert store.settings_doc(db, user.id)["active_reason"] == "unset"
        _assert_doc_matches_runtime(db, user)

        # 2. Env openai-compatible -> connected from the environment.
        monkeypatch.setenv("AI_PROVIDER", "openai-compatible")
        monkeypatch.setenv("OPENAI_COMPAT_BASE_URL", "https://env.example/v1")
        monkeypatch.setenv("OPENAI_COMPAT_MODEL", "env-model")
        prov = store.resolve_provider(db, user.id)
        assert isinstance(prov, OpenAICompatibleProvider)
        assert prov.model_name == "env-model"
        doc = store.settings_doc(db, user.id)
        assert doc["connected"] is True and doc["active_source"] == "env"
        assert doc["active_model"] == "env-model"
        _assert_doc_matches_runtime(db, user)

        # 3. A broken env config is NOT silently swapped for the stub: it reads
        #    as not connected, with the reason named.
        monkeypatch.delenv("OPENAI_COMPAT_MODEL")
        assert store.resolve_provider(db, user.id) is None
        doc = store.settings_doc(db, user.id)
        assert doc["connected"] is False and doc["active_reason"] == "misconfigured"
        _assert_doc_matches_runtime(db, user)
        monkeypatch.setenv("OPENAI_COMPAT_MODEL", "env-model")

        # 4. A saved row with a complete endpoint beats the env, and the panel
        #    names the saved endpoint — not the environment's.
        store.save_settings(
            db, user.id,
            provider="openai-compatible", base_url=fake_openai.base_url, model="saved",
            api_key="k",
        )
        prov = store.resolve_provider(db, user.id)
        assert isinstance(prov, OpenAICompatibleProvider)
        assert prov.model_name == "saved"
        assert prov.base_url == fake_openai.base_url
        doc = store.settings_doc(db, user.id)
        assert doc["active_source"] == "settings" and doc["active_model"] == "saved"
        _assert_doc_matches_runtime(db, user)

        # 5. A saved-but-incomplete endpoint reads as NOT CONNECTED. The old
        #    code narrated with the stub here while the panel still claimed
        #    the saved endpoint — exactly the disagreement P12 removes.
        store.save_settings(
            db, user.id, provider="openai-compatible", base_url=fake_openai.base_url, model=""
        )
        assert store.resolve_provider(db, user.id) is None
        assert store.settings_doc(db, user.id)["active_reason"] == "incomplete"
        _assert_doc_matches_runtime(db, user)

        # 6. A legacy "stub" row (the retired default) reads as not connected
        #    even with a configured environment: it is the player's saved
        #    choice, and that choice is now empty-handed, never the stub.
        store.save_settings(db, user.id, provider="stub")
        assert store.resolve_provider(db, user.id) is None
        doc = store.settings_doc(db, user.id)
        assert doc["provider"] == "stub" and doc["connected"] is False
        assert doc["active_reason"] == "stub"
        _assert_doc_matches_runtime(db, user)
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
    # The panel's claim and the play path agree the moment it is saved (P12).
    doc = saved.json()
    assert doc["connected"] is True
    assert doc["active_provider"] == "openai-compatible"
    assert doc["active_source"] == "settings"
    assert doc["active_model"] == "story-model"

    # The chronicle opens on the prologue's road (§intro); walk in so the
    # tuned provider narrates an in-tavern act.
    walk = client.post(
        "/act", headers=headers, json={"text": "I head down to the inn and step inside"}
    )
    assert walk.status_code == 200, walk.text

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


def test_put_rejects_the_builtin_storyteller(client):
    """The built-in storyteller is not a play mode: saving it is refused (P12)."""
    headers = _register(client)
    r = client.put(
        "/ai/settings", headers=headers, json={"provider": "stub", "model": "whatever"}
    )
    assert r.status_code == 400, r.text
    assert "openai-compatible" in str(r.json()["detail"])
    # Nothing was saved — the failed PUT is not half-applied.
    doc = client.get("/ai/settings", headers=headers).json()
    assert doc["configured"] is False and doc["connected"] is False
    assert doc["providers"] == ["openai-compatible"]


def test_act_refuses_without_a_connected_model(client):
    """Unset AI -> the turn is refused, not narrated (P12; P11's contract)."""
    headers = _register(client)
    assert client.post("/campaigns", headers=headers, json={}).status_code == 201

    before = client.get("/state", headers=headers).json()

    actions = (
        "I head down to the inn and step inside",  # authored beat (no model)
        "I hum a quiet tune to the rafters",  # model-dependent beat
    )
    for i, text in enumerate(actions):
        r = client.post(
            "/act",
            headers={**headers, "Idempotency-Key": f"unconnected-{i}"},
            json={"text": text},
        )
        assert r.status_code == 400, r.text
        assert r.json() == {"detail": "connect_your_ai"}
        assert "holds its breath" not in r.text  # no stub prose
        assert "quill falters" not in r.text  # no cover prose

    after = client.get("/state", headers=headers).json()
    assert after == before, "a refused turn must persist nothing"


def test_act_with_legacy_stub_row_refuses(client):
    """A legacy "stub" row is a saved choice for nobody: it is not connected."""
    email = f"legacy{next(_EMAIL_SEQ)}@example.com"
    headers = _register(client, email=email)
    assert client.post("/campaigns", headers=headers, json={}).status_code == 201

    db = TestingSession()
    try:
        user = db.query(User).filter(User.email == email).one()
        store.save_settings(db, user.id, provider="stub")  # retired value, direct write
    finally:
        db.close()

    doc = client.get("/ai/settings", headers=headers).json()
    assert doc["provider"] == "stub"  # the panel shows what is stored...
    assert doc["connected"] is False  # ...and that it will not narrate
    assert doc["active_provider"] == ""
    assert doc["active_reason"] == "stub"

    r = client.post("/act", headers=headers, json={"text": "I hum a quiet tune to the rafters"})
    assert r.status_code == 400, r.text
    assert r.json() == {"detail": "connect_your_ai"}


def test_model_failure_is_machine_readable_and_retry_safe(client, fake_openai):
    """A failing model call answers with a code, not with fiction (P12)."""
    email = f"fail{next(_EMAIL_SEQ)}@example.com"
    headers = _register(client, email=email)
    assert client.post("/campaigns", headers=headers, json={}).status_code == 201

    saved = client.put(
        "/ai/settings",
        headers=headers,
        json={
            "provider": "openai-compatible",
            "base_url": fake_openai.base_url,
            "model": "story-model",
            "api_key": "sk-live-SECRETKEY12345",
        },
    )
    assert saved.status_code == 200 and saved.json()["connected"] is True

    fake_openai.reply = json.dumps({"narration": "The hearth answers with a slow crackle."})
    walk = client.post(
        "/act",
        headers={**headers, "Idempotency-Key": "mf-1"},
        json={"text": "I head down to the inn and step inside"},
    )
    assert walk.status_code == 200, walk.text

    before = client.get("/state", headers=headers).json()
    fake_openai.status = 500

    r = client.post(
        "/act",
        headers={**headers, "Idempotency-Key": "mf-2"},
        json={"text": "I hum a quiet tune to the rafters"},
    )
    assert r.status_code == 502, r.text
    detail = r.json()["detail"]
    assert detail["code"] == "provider_failed"
    assert detail["retryable"] is True
    assert detail["message"]
    assert "SECRETKEY" not in r.text  # the key never rides a failure detail
    assert "quill falters" not in r.text  # and no in-world cover prose
    assert "holds its breath" not in r.text
    assert client.get("/state", headers=headers).json() == before  # nothing applied

    # Retry-safe: the failure consumed nothing, so the same action with the
    # same idempotency key lands cleanly once the model is back.
    fake_openai.status = 200
    fake_openai.reply = json.dumps({"narration": "The rafters keep their secrets now."})
    retry = client.post(
        "/act",
        headers={**headers, "Idempotency-Key": "mf-2"},
        json={"text": "I hum a quiet tune to the rafters"},
    )
    assert retry.status_code == 200, retry.text
    assert retry.json()["narration"] == "The rafters keep their secrets now."


def test_provider_error_scrub_strips_key_shaped_material():
    """The player-visible failure detail is key-free and bounded."""
    from app.modules.play.engine import _scrub_provider_error

    text = _scrub_provider_error("HTTP 401: Bearer sk-live-abcdef123456 rejected")
    assert "sk-live-abcdef123456" not in text
    assert "Bearer" not in text
    assert "[redacted]" in text
    assert _scrub_provider_error("") == "the model call failed"
    assert len(_scrub_provider_error("x" * 500)) == 200


def test_actions_submit_refuses_without_an_env_provider(client, fake_openai, monkeypatch):
    """The env-only pipeline surface never narrates with the built-in stub (P12)."""
    r = client.post("/actions/submit", json={"text": "look around"})
    assert r.status_code == 400, r.text
    assert r.json() == {"detail": "connect_your_ai"}

    monkeypatch.setenv("AI_PROVIDER", "openai-compatible")
    monkeypatch.setenv("OPENAI_COMPAT_BASE_URL", fake_openai.base_url)
    monkeypatch.setenv("OPENAI_COMPAT_MODEL", "env-model")
    fake_openai.reply = json.dumps({"narration": "The dust settles."})
    ok = client.post("/actions/submit", json={"text": "look around"})
    assert ok.status_code == 200, ok.text
    assert fake_openai.requests, "the env provider was never called"
