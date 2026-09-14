"""Registry: adapter construction, canary capability probe, provider_caps cache."""
from __future__ import annotations

import json
import urllib.error

import pytest
from test_providers_server import FakeProviderServer, openai_payload

from engine.models import ProviderCaps, ProviderConfig
from engine.providers.anthropic import AnthropicAdapter
from engine.providers.gemini import GeminiAdapter
from engine.providers.openai_compat import OpenAICompatAdapter
from engine.providers.registry import (
    CANARY_PROMPT,
    CANARY_TOOL_NAME,
    build_adapter,
    cache_key,
    probe_capabilities,
)
from engine.store import Store

API_KEY = "sk-registry-secret"


def make_cfg(server: FakeProviderServer | None = None, **overrides) -> ProviderConfig:
    values = {
        "name": "local",
        "model": "local-model",
        "base_url": f"{server.url}/v1" if server else "http://127.0.0.1:1/v1",
        "api_mode": "openai",
        "timeout_s": 5.0,
        "max_retries": 0,
    }
    values.update(overrides)
    return ProviderConfig(**values)


def make_adapter(server: FakeProviderServer, **overrides):
    cfg = make_cfg(server, **overrides)
    adapter = build_adapter(cfg, api_key=API_KEY)
    adapter.backoff_base_s = 0.0
    return adapter


def canary_ok() -> dict:
    return openai_payload(tool_calls=[("call_canary", CANARY_TOOL_NAME, {"ok": True})])


# --------------------------------------------------------------------------- #
# build_adapter
# --------------------------------------------------------------------------- #

@pytest.mark.parametrize(
    ("mode", "expected"),
    [
        ("openai", OpenAICompatAdapter),
        ("openai_compat", OpenAICompatAdapter),
        ("openai-compatible", OpenAICompatAdapter),
        ("local", OpenAICompatAdapter),
        ("anthropic", AnthropicAdapter),
        ("claude", AnthropicAdapter),
        ("gemini", GeminiAdapter),
        ("google", GeminiAdapter),
        ("  GEMINI  ", GeminiAdapter),
    ],
)
def test_build_adapter_by_api_mode(mode: str, expected: type) -> None:
    cfg = make_cfg(api_mode=mode)
    adapter = build_adapter(cfg, api_key=API_KEY)
    assert isinstance(adapter, expected)
    assert adapter.name == "local"
    assert adapter.api_key == API_KEY
    assert adapter.capabilities() == ProviderCaps(native_tools=True)


def test_build_adapter_rejects_unknown_mode() -> None:
    with pytest.raises(ValueError, match="unknown api_mode"):
        build_adapter(make_cfg(api_mode="mystery-wire"), api_key=API_KEY)


def test_build_adapter_without_key_is_allowed_for_local_servers() -> None:
    adapter = build_adapter(make_cfg(api_mode="openai"), api_key=None)
    assert adapter.api_key is None
    assert adapter.secrets() == ()


# --------------------------------------------------------------------------- #
# probe_capabilities
# --------------------------------------------------------------------------- #

def test_probe_reports_native_tools_on_a_canary_round_trip() -> None:
    with FakeProviderServer() as server:
        server.enqueue(200, canary_ok())
        cfg = make_cfg(server)
        adapter = make_adapter(server)
        caps = probe_capabilities(adapter, cfg)
        captured = server.last_request()
    assert caps.native_tools is True
    assert caps.probed_at > 0
    assert adapter.capabilities() is caps
    body = captured.body
    assert body["model"] == "local-model"
    assert body["tools"][0]["function"]["name"] == CANARY_TOOL_NAME
    assert CANARY_PROMPT in body["messages"][0]["content"]
    assert body["max_tokens"] == 64
    assert body["temperature"] == 0.0


def test_probe_reports_degraded_when_no_tool_call_comes_back() -> None:
    with FakeProviderServer() as server:
        server.enqueue(200, openai_payload(text="I would rather chat."))
        cfg = make_cfg(server)
        caps = probe_capabilities(make_adapter(server), cfg)
        assert len(server.requests) == 1
    assert caps.native_tools is False
    assert caps.probed_at > 0


def test_probe_never_raises_on_transport_failure() -> None:
    def failing_opener(request, timeout=None):
        raise urllib.error.URLError(TimeoutError("timed out"))

    cfg = make_cfg()
    adapter = build_adapter(cfg, api_key=API_KEY)
    adapter.opener = failing_opener
    caps = probe_capabilities(adapter, cfg)
    assert caps.native_tools is False
    assert caps.probed_at > 0


def test_probe_never_raises_on_broken_store() -> None:
    class BrokenStore:
        def find_one(self, *args, **kwargs):
            raise RuntimeError("db gone")

        def upsert(self, *args, **kwargs):
            raise RuntimeError("db gone")

    with FakeProviderServer() as server:
        server.enqueue(200, canary_ok())
        cfg = make_cfg(server)
        caps = probe_capabilities(make_adapter(server), cfg, store=BrokenStore())
    assert caps.native_tools is True


def test_declared_caps_from_config_are_merged() -> None:
    with FakeProviderServer() as server:
        server.enqueue(200, openai_payload(text="no tools here"))
        cfg = make_cfg(
            server,
            context_window=128000,
            extra={"caps": {"streaming": True, "json_mode": True, "context_window": 64000}},
        )
        caps = probe_capabilities(make_adapter(server), cfg)
    assert caps.native_tools is False  # the canary alone decides this slot
    assert caps.streaming is True
    assert caps.json_mode is True
    assert caps.context_window == 64000  # explicit declaration wins


def test_context_window_declared_without_extra_caps() -> None:
    with FakeProviderServer() as server:
        server.enqueue(200, canary_ok())
        caps = probe_capabilities(make_adapter(server), make_cfg(server, context_window=32000))
    assert caps.context_window == 32000


def test_cfg_extra_caps_cannot_override_probe_slots() -> None:
    with FakeProviderServer() as server:
        server.enqueue(200, openai_payload(text="no tools"))
        cfg = make_cfg(
            server,
            extra={"caps": {"native_tools": True, "probed_at": 1234567890}},
        )
        caps = probe_capabilities(make_adapter(server), cfg)
    assert caps.native_tools is False
    assert caps.probed_at != 1234567890


# --------------------------------------------------------------------------- #
# provider_caps cache
# --------------------------------------------------------------------------- #

def test_probe_caches_and_reuses_the_verdict() -> None:
    with FakeProviderServer() as server:
        server.enqueue(200, canary_ok())
        cfg = make_cfg(server)
        store = Store(":memory:")
        try:
            first = probe_capabilities(make_adapter(server), cfg, store=store)
            assert len(server.requests) == 1
            row = store.find_one("provider_caps", {"provider_key": cache_key(cfg)})
            assert row is not None
            assert json.loads(row["caps"])["native_tools"] is True
            assert row["probed_at"] == first.probed_at

            # a fresh adapter + empty response queue: only a cache hit can succeed
            cached = probe_capabilities(make_adapter(server), cfg, store=store)
            assert len(server.requests) == 1
        finally:
            store.close()
    assert cached == first
    assert cached is not first


def test_degraded_verdict_is_cached_too() -> None:
    with FakeProviderServer() as server:
        server.enqueue(200, openai_payload(text="chat only, no tools"))
        cfg = make_cfg(server)
        store = Store(":memory:")
        try:
            caps = probe_capabilities(make_adapter(server), cfg, store=store)
            row = store.find_one("provider_caps", {"provider_key": cache_key(cfg)})
        finally:
            store.close()
    assert caps.native_tools is False
    assert row is not None
    assert json.loads(row["caps"])["native_tools"] is False


def test_force_reprobes_and_refreshes_the_cache() -> None:
    with FakeProviderServer() as server:
        server.enqueue(200, canary_ok())
        server.enqueue(200, openai_payload(text="no tools now"))
        cfg = make_cfg(server)
        store = Store(":memory:")
        try:
            first = probe_capabilities(make_adapter(server), cfg, store=store)
            second = probe_capabilities(make_adapter(server), cfg, store=store, force=True)
            row = store.find_one("provider_caps", {"provider_key": cache_key(cfg)})
        finally:
            store.close()
        assert len(server.requests) == 2
    assert row is not None
    assert first.native_tools is True
    assert second.native_tools is False
    assert json.loads(row["caps"])["native_tools"] is False


def test_failed_probe_is_not_cached() -> None:
    def failing_opener(request, timeout=None):
        raise urllib.error.URLError(ConnectionResetError("reset"))

    cfg = make_cfg()
    store = Store(":memory:")
    try:
        adapter = build_adapter(cfg, api_key=API_KEY)
        adapter.opener = failing_opener
        caps = probe_capabilities(adapter, cfg, store=store)
        assert caps.native_tools is False
        assert store.find_one("provider_caps", {"provider_key": cache_key(cfg)}) is None
    finally:
        store.close()


def test_corrupt_cache_row_triggers_a_fresh_probe() -> None:
    with FakeProviderServer() as server:
        server.enqueue(200, canary_ok())
        cfg = make_cfg(server)
        store = Store(":memory:")
        try:
            store.upsert(
                "provider_caps",
                {"provider_key": cache_key(cfg), "caps": "not json", "probed_at": 1},
                conflict="provider_key",
            )
            caps = probe_capabilities(make_adapter(server), cfg, store=store)
            row = store.find_one("provider_caps", {"provider_key": cache_key(cfg)})
        finally:
            store.close()
        assert len(server.requests) == 1
    assert row is not None
    assert caps.native_tools is True
    assert json.loads(row["caps"])["native_tools"] is True


def test_cache_key_and_cached_row_never_contain_key_material() -> None:
    with FakeProviderServer() as server:
        server.enqueue(200, canary_ok())
        cfg = make_cfg(server)
        store = Store(":memory:")
        try:
            probe_capabilities(make_adapter(server), cfg, store=store)
            rows = store.find("provider_caps")
        finally:
            store.close()
    assert API_KEY not in cache_key(cfg)
    assert cache_key(cfg) == f"local|local-model|{cfg.base_url}|openai"
    dumped = json.dumps(rows)
    assert API_KEY not in dumped
    assert "sk-" not in dumped
