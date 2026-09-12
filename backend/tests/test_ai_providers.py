"""Tests for OpenAICompatibleProvider + get_provider factory (stdlib only)."""
from __future__ import annotations

import json
import threading
from http.server import BaseHTTPRequestHandler, HTTPServer

import pytest

from app.modules.ai.providers import (
    OpenAICompatibleProvider,
    ProviderError,
    StubProvider,
    get_provider,
)


class _State:
    def __init__(self, responses):
        self.responses = list(responses)
        self.requests: list[dict] = []
        self.count = 0


def _make_server(state: _State) -> HTTPServer:
    captured = state

    class Handler(BaseHTTPRequestHandler):
        def do_POST(self):
            assert self.path == "/chat/completions", self.path
            length = int(self.headers.get("Content-Length", 0))
            raw = self.rfile.read(length) if length else b"{}"
            captured.requests.append({
                "body": json.loads(raw.decode("utf-8")),
                "authorization": self.headers.get("Authorization"),
                "content_type": self.headers.get("Content-Type"),
            })
            idx = min(captured.count, len(captured.responses) - 1)
            status, payload = captured.responses[idx]
            captured.count += 1
            data = json.dumps(payload).encode("utf-8")
            self.send_response(status)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(data)))
            self.end_headers()
            self.wfile.write(data)

        def log_message(self, format, *args):  # noqa: A002
            pass

    server = HTTPServer(("127.0.0.1", 0), Handler)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    return server


def _ok_payload(text="Hello, traveler.", pt=7, ct=5):
    return {
        "choices": [{"message": {"role": "assistant", "content": text}}],
        "usage": {"prompt_tokens": pt, "completion_tokens": ct},
    }


def test_happy_path_request_shape_and_tokens():
    state = _State([(200, _ok_payload())])
    server = _make_server(state)
    try:
        base = f"http://127.0.0.1:{server.server_port}"
        prov = OpenAICompatibleProvider(
            base_url=base, model="test-model", api_key="sekret", timeout_s=5)
        result = prov.generate("Tell me a tale.", role="narrator", max_tokens=123)
    finally:
        server.shutdown()
    assert result.text == "Hello, traveler."
    assert result.model == "test-model"
    assert result.prompt_tokens == 7
    assert result.completion_tokens == 5
    assert len(state.requests) == 1
    req = state.requests[0]
    assert req["authorization"] == "Bearer sekret"
    assert req["content_type"] == "application/json"
    body = req["body"]
    assert body["model"] == "test-model"
    assert body["max_tokens"] == 123
    assert body["temperature"] == 0.7
    assert isinstance(body["messages"], list) and len(body["messages"]) == 2
    assert body["messages"][0]["role"] == "system"
    assert body["messages"][1] == {"role": "user", "content": "Tell me a tale."}


def test_no_auth_header_without_key():
    state = _State([(200, _ok_payload())])
    server = _make_server(state)
    try:
        base = f"http://127.0.0.1:{server.server_port}"
        prov = OpenAICompatibleProvider(base_url=base, model="m", timeout_s=5)
        prov.generate("hi")
    finally:
        server.shutdown()
    assert state.requests[0]["authorization"] is None


def test_retry_then_success_on_500():
    state = _State([(500, {"error": "boom"}), (200, _ok_payload("recovered"))])
    server = _make_server(state)
    try:
        base = f"http://127.0.0.1:{server.server_port}"
        prov = OpenAICompatibleProvider(base_url=base, model="m", timeout_s=5)
        result = prov.generate("hi")
    finally:
        server.shutdown()
    assert result.text == "recovered"
    assert state.count == 2


def test_persistent_failure_raises_provider_error():
    state = _State([(500, {"error": "boom"}), (500, {"error": "boom"})])
    server = _make_server(state)
    try:
        base = f"http://127.0.0.1:{server.server_port}"
        prov = OpenAICompatibleProvider(base_url=base, model="m", timeout_s=5)
        with pytest.raises(ProviderError):
            prov.generate("hi")
    finally:
        server.shutdown()
    assert state.count == 2


def test_factory_stub_default_unset(monkeypatch):
    for var in ("AI_PROVIDER", "OPENAI_COMPAT_BASE_URL", "OPENAI_COMPAT_MODEL",
                "OPENAI_COMPAT_API_KEY", "OPENAI_COMPAT_TIMEOUT_S"):
        monkeypatch.delenv(var, raising=False)
    assert isinstance(get_provider(), StubProvider)


def test_factory_stub_explicit(monkeypatch):
    monkeypatch.setenv("AI_PROVIDER", "stub")
    assert isinstance(get_provider(), StubProvider)


def test_factory_openai_compatible(monkeypatch):
    monkeypatch.setenv("AI_PROVIDER", "openai-compatible")
    monkeypatch.setenv("OPENAI_COMPAT_BASE_URL", "https://api.example.com/v1")
    monkeypatch.setenv("OPENAI_COMPAT_MODEL", "my-model")
    monkeypatch.setenv("OPENAI_COMPAT_API_KEY", "k")
    monkeypatch.setenv("OPENAI_COMPAT_TIMEOUT_S", "12")
    prov = get_provider()
    assert isinstance(prov, OpenAICompatibleProvider)
    assert prov.base_url == "https://api.example.com/v1"
    assert prov.model_name == "my-model"
    assert prov.api_key == "k"
    assert prov.timeout_s == 12


def test_factory_openai_compatible_defaults(monkeypatch):
    monkeypatch.setenv("AI_PROVIDER", "openai-compatible")
    monkeypatch.setenv("OPENAI_COMPAT_BASE_URL", "https://api.example.com/v1/")
    monkeypatch.setenv("OPENAI_COMPAT_MODEL", "m")
    monkeypatch.delenv("OPENAI_COMPAT_API_KEY", raising=False)
    monkeypatch.delenv("OPENAI_COMPAT_TIMEOUT_S", raising=False)
    prov = get_provider()
    assert isinstance(prov, OpenAICompatibleProvider)
    assert prov.base_url == "https://api.example.com/v1"
    assert prov.api_key == ""
    assert prov.timeout_s == 30


def test_factory_missing_base_url(monkeypatch):
    monkeypatch.setenv("AI_PROVIDER", "openai-compatible")
    monkeypatch.delenv("OPENAI_COMPAT_BASE_URL", raising=False)
    monkeypatch.setenv("OPENAI_COMPAT_MODEL", "m")
    with pytest.raises(Exception):
        get_provider()


def test_factory_missing_model(monkeypatch):
    monkeypatch.setenv("AI_PROVIDER", "openai-compatible")
    monkeypatch.setenv("OPENAI_COMPAT_BASE_URL", "https://api.example.com/v1")
    monkeypatch.delenv("OPENAI_COMPAT_MODEL", raising=False)
    with pytest.raises(Exception):
        get_provider()


def test_factory_unknown_provider(monkeypatch):
    monkeypatch.setenv("AI_PROVIDER", "bogus-xyz")
    with pytest.raises(ValueError):
        get_provider()
