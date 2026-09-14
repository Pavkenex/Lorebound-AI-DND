"""Transport-level behavior: retry policy, backoff, error normalization, redaction.

Exercise ``engine.providers.transport`` directly (the adapters' shared HTTP
layer) against the fake localhost server plus injected openers for the failure
modes a server cannot produce deterministically (timeouts).
"""
from __future__ import annotations

import urllib.error

import pytest
from test_providers_server import FakeProviderServer

from engine.providers.base import ProviderError
from engine.providers.transport import extract_error_message, post_json, redact

SECRET = "sk-secret-transport-test"


class _RaisingOpener:
    """urlopen stand-in raising a fixed exception; records timeouts seen."""

    def __init__(self, exc: BaseException) -> None:
        self.exc = exc
        self.calls = 0
        self.timeouts: list[float | None] = []

    def __call__(self, request, timeout=None):
        self.calls += 1
        self.timeouts.append(timeout)
        raise self.exc


def test_post_json_returns_decoded_object() -> None:
    with FakeProviderServer() as server:
        server.enqueue(200, {"ok": True, "nested": {"n": 1}})
        assert post_json(server.url + "/x", {"a": 1}) == {"ok": True, "nested": {"n": 1}}
        assert server.last_request().body == {"a": 1}


def test_retryable_status_retries_with_exponential_backoff() -> None:
    sleeps: list[float] = []
    with FakeProviderServer() as server:
        server.enqueue(503, {"error": {"message": "busy"}})
        server.enqueue(503, {"error": {"message": "busy"}})
        server.enqueue(200, {"ok": True})
        result = post_json(
            server.url + "/x",
            {},
            max_retries=2,
            backoff_base_s=0.5,
            sleeper=sleeps.append,
        )
        assert result == {"ok": True}
        assert len(server.requests) == 3
        assert sleeps == [0.5, 1.0]


def test_retry_exhausted_raises_retryable_error_with_status() -> None:
    with FakeProviderServer() as server:
        for _ in range(3):
            server.enqueue(503, {"error": {"message": "upstream down"}})
        with pytest.raises(ProviderError) as info:
            post_json(server.url + "/x", {}, max_retries=2, backoff_base_s=0.0)
        assert len(server.requests) == 3
    assert info.value.retryable is True
    assert info.value.status == 503
    assert "upstream down" in str(info.value)


def test_rate_limit_is_retryable() -> None:
    with FakeProviderServer() as server:
        server.enqueue(429, {"error": {"message": "rate limited"}})
        server.enqueue(200, {"ok": True})
        assert post_json(server.url + "/x", {}, max_retries=1, backoff_base_s=0.0) == {"ok": True}
        assert len(server.requests) == 2


@pytest.mark.parametrize("status", [400, 401, 403, 404, 422])
def test_other_4xx_is_not_retried(status: int) -> None:
    with FakeProviderServer() as server:
        server.enqueue(status, {"error": {"message": "nope"}})
        with pytest.raises(ProviderError) as info:
            post_json(server.url + "/x", {}, max_retries=3, backoff_base_s=0.0)
        assert len(server.requests) == 1
    assert info.value.retryable is False
    assert info.value.status == status
    assert "nope" in str(info.value)


def test_timeout_is_retryable_and_uses_configured_timeout() -> None:
    opener = _RaisingOpener(urllib.error.URLError(TimeoutError("timed out")))
    with pytest.raises(ProviderError) as info:
        post_json("http://127.0.0.1:1/x", {}, max_retries=2, timeout_s=7.5, opener=opener)
    assert opener.calls == 3
    assert opener.timeouts == [7.5, 7.5, 7.5]
    assert info.value.retryable is True
    assert "timed out" in str(info.value)


def test_connection_error_is_retryable() -> None:
    opener = _RaisingOpener(ConnectionResetError("connection reset by peer"))
    with pytest.raises(ProviderError) as info:
        post_json("http://127.0.0.1:1/x", {}, max_retries=1, backoff_base_s=0.0, opener=opener)
    assert opener.calls == 2
    assert info.value.retryable is True


def test_non_json_success_body_is_not_retryable() -> None:
    with FakeProviderServer() as server:
        server.enqueue(200, "<html>gateway</html>")
        with pytest.raises(ProviderError) as info:
            post_json(server.url + "/x", {}, max_retries=2, backoff_base_s=0.0)
        assert len(server.requests) == 1
    assert info.value.retryable is False
    assert "non-JSON" in str(info.value)


def test_max_retries_zero_means_single_attempt() -> None:
    with FakeProviderServer() as server:
        server.enqueue(503, {"error": {"message": "busy"}})
        with pytest.raises(ProviderError):
            post_json(server.url + "/x", {}, max_retries=0)
        assert len(server.requests) == 1


def test_negative_max_retries_is_clamped() -> None:
    with FakeProviderServer() as server:
        server.enqueue(503, {"error": {"message": "busy"}})
        with pytest.raises(ProviderError):
            post_json(server.url + "/x", {}, max_retries=-5)
        assert len(server.requests) == 1


def test_secrets_never_appear_in_error_messages() -> None:
    with FakeProviderServer() as server:
        server.enqueue(401, {"error": {"message": f"invalid api key {SECRET} (hint: {SECRET})"}})
        with pytest.raises(ProviderError) as info:
            post_json(server.url + "/x", {}, secrets=(SECRET,), max_retries=0)
    message = str(info.value)
    assert SECRET not in message
    assert message.count("***") == 2
    assert "invalid api key" in message


def test_error_message_shapes() -> None:
    assert extract_error_message('{"error": {"message": "boom", "type": "x"}}') == "boom"
    assert extract_error_message('{"error": "plain"}') == "plain"
    assert extract_error_message('{"message": "top-level"}') == "top-level"
    assert extract_error_message("<html>502</html>") == "<html>502</html>"
    assert extract_error_message("") == ""
    assert len(extract_error_message("x" * 5000)) == 300


def test_redact_replaces_every_occurrence() -> None:
    assert redact("a sk-1 b sk-1", ["sk-1"]) == "a *** b ***"
    assert redact("nothing to hide", []) == "nothing to hide"
    assert redact("empty ignored", [""]) == "empty ignored"
