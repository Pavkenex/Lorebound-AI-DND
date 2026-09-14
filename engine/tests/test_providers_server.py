"""Fake provider HTTP server + canned vendor payloads for the adapter tests.

A stdlib ``http.server`` bound to an ephemeral localhost port. It serves canned
vendor-shaped payloads and records every request, so the adapter tests can
assert BOTH directions: what the adapter SENT (path, headers, JSON body) and
what it did with the response. No real network is ever touched.
"""
from __future__ import annotations

import json
import threading
import time
from collections import deque
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from typing import Any


class CapturedRequest:
    """One request the fake server received (headers lower-cased)."""

    def __init__(self, method: str, path: str, headers: dict[str, str], raw: bytes, body: Any) -> None:
        self.method = method
        self.path = path
        self.headers = headers
        self.raw = raw
        self.body = body

    def __repr__(self) -> str:  # pragma: no cover - debugging aid
        return f"CapturedRequest({self.method} {self.path})"


class _Handler(BaseHTTPRequestHandler):
    def do_POST(self) -> None:  # noqa: N802 - BaseHTTPRequestHandler API
        length = int(self.headers.get("content-length") or 0)
        raw = self.rfile.read(length) if length else b""
        try:
            body = json.loads(raw.decode("utf-8")) if raw else None
        except ValueError:
            body = None
        server: FakeProviderServer = self.server  # type: ignore[assignment]
        with server.lock:
            server.requests.append(
                CapturedRequest(
                    self.command,
                    self.path,
                    {key.lower(): value for key, value in self.headers.items()},
                    raw,
                    body,
                )
            )
            try:
                status, payload, delay = server.responses.popleft()
            except IndexError:
                status, payload, delay = 500, {"error": {"message": "no canned response"}}, 0.0
        if delay:
            time.sleep(delay)
        data = payload.encode("utf-8") if isinstance(payload, str) else json.dumps(payload).encode("utf-8")
        self.send_response(status)
        self.send_header("content-type", "application/json")
        self.send_header("content-length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)

    def log_message(self, format: str, *args: Any) -> None:  # noqa: A002 - base API
        return  # silence stderr access logs


class FakeProviderServer(ThreadingHTTPServer):
    """Canned-response HTTP server on 127.0.0.1:<ephemeral>.

    ``enqueue(status, payload)`` queues one response; ``payload`` may be a dict
    (JSON-encoded) or a raw string (sent as-is, for broken-body tests). Runs are
    deterministic: responses are consumed in FIFO order, and an empty queue
    answers 500 so a test can never hang waiting.
    """

    daemon_threads = True
    allow_reuse_address = True

    def __init__(self, host: str = "127.0.0.1", port: int = 0) -> None:
        super().__init__((host, port), _Handler)
        self.requests: list[CapturedRequest] = []
        self.responses: deque[tuple[int, Any, float]] = deque()
        self.lock = threading.Lock()
        self.thread: threading.Thread | None = None

    # -- lifecycle --------------------------------------------------------- #
    def start(self) -> FakeProviderServer:
        self.thread = threading.Thread(target=self.serve_forever, kwargs={"poll_interval": 0.01})
        self.thread.daemon = True
        self.thread.start()
        return self

    def stop(self) -> None:
        self.shutdown()
        self.server_close()
        if self.thread is not None:
            self.thread.join(timeout=5)
            self.thread = None

    def __enter__(self) -> FakeProviderServer:
        return self.start()

    def __exit__(self, *exc_info: Any) -> None:
        self.stop()

    # -- test helpers ------------------------------------------------------ #
    @property
    def url(self) -> str:
        host, port = self.server_address[0], self.server_address[1]
        return f"http://{host}:{port}"

    def enqueue(self, status: int, payload: Any, *, delay: float = 0.0) -> None:
        with self.lock:
            self.responses.append((status, payload, delay))

    def reset(self) -> None:
        with self.lock:
            self.requests.clear()
            self.responses.clear()

    def last_request(self) -> CapturedRequest:
        assert self.requests, "fake server received no requests"
        return self.requests[-1]


# --------------------------------------------------------------------------- #
# Canned vendor payloads
# --------------------------------------------------------------------------- #

def openai_payload(
    *,
    text: str = "",
    tool_calls: list[tuple[str, str, Any]] | None = None,
    usage: dict | None = None,
    model: str = "gpt-test",
    finish_reason: str = "stop",
) -> dict:
    """OpenAI chat.completion body. ``tool_calls`` items are (id, name, arguments)."""
    message: dict = {"role": "assistant", "content": text or None}
    if tool_calls:
        message["tool_calls"] = [
            {
                "id": call_id,
                "type": "function",
                "function": {
                    "name": name,
                    "arguments": arguments if isinstance(arguments, str) else json.dumps(arguments),
                },
            }
            for call_id, name, arguments in tool_calls
        ]
    return {
        "id": "chatcmpl-test",
        "object": "chat.completion",
        "model": model,
        "choices": [{"index": 0, "message": message, "finish_reason": finish_reason}],
        "usage": usage if usage is not None else {
            "prompt_tokens": 11,
            "completion_tokens": 5,
            "total_tokens": 16,
        },
    }


def anthropic_payload(
    *,
    texts: list[str] | None = None,
    tool_uses: list[tuple[str, str, Any]] | None = None,
    usage: dict | None = None,
    model: str = "claude-test",
) -> dict:
    """Anthropic messages body. ``tool_uses`` items are (id, name, input)."""
    content: list[dict] = [{"type": "text", "text": text} for text in texts or []]
    content += [
        {"type": "tool_use", "id": call_id, "name": name, "input": payload}
        for call_id, name, payload in tool_uses or []
    ]
    return {
        "id": "msg_test",
        "type": "message",
        "role": "assistant",
        "model": model,
        "content": content,
        "stop_reason": "end_turn",
        "usage": usage if usage is not None else {"input_tokens": 13, "output_tokens": 7},
    }


def gemini_payload(
    *,
    texts: list[str] | None = None,
    function_calls: list[tuple[str, Any]] | None = None,
    usage: dict | None = None,
    model: str = "gemini-test",
) -> dict:
    """Gemini generateContent body. ``function_calls`` items are (name, args)."""
    parts: list[dict] = [{"text": text} for text in texts or []]
    parts += [{"functionCall": {"name": name, "args": args}} for name, args in function_calls or []]
    return {
        "candidates": [
            {"content": {"role": "model", "parts": parts}, "finishReason": "STOP"}
        ],
        "usageMetadata": usage if usage is not None else {
            "promptTokenCount": 17,
            "candidatesTokenCount": 9,
            "totalTokenCount": 26,
        },
        "modelVersion": model,
    }


# --------------------------------------------------------------------------- #
# Self-tests: the fake must be trustworthy before other tests rely on it
# --------------------------------------------------------------------------- #

def test_server_serves_canned_payload_and_captures_requests() -> None:
    import urllib.request

    with FakeProviderServer() as server:
        server.enqueue(200, {"hello": "world"})
        request = urllib.request.Request(
            server.url + "/v1/thing",
            data=json.dumps({"sent": True}).encode(),
            headers={"content-type": "application/json", "x-test": "1"},
            method="POST",
        )
        with urllib.request.urlopen(request, timeout=5) as response:
            assert json.loads(response.read()) == {"hello": "world"}
        captured = server.last_request()
        assert (captured.method, captured.path) == ("POST", "/v1/thing")
        assert captured.body == {"sent": True}
        assert captured.headers["x-test"] == "1"


def test_server_empty_queue_answers_500() -> None:
    import urllib.error
    import urllib.request

    with FakeProviderServer() as server:
        request = urllib.request.Request(server.url + "/x", data=b"{}", method="POST")
        try:
            urllib.request.urlopen(request, timeout=5)
        except urllib.error.HTTPError as exc:
            assert exc.code == 500
        else:  # pragma: no cover - defensive
            raise AssertionError("expected HTTP 500 for an empty response queue")


def test_server_serves_raw_string_body() -> None:
    import urllib.request

    with FakeProviderServer() as server:
        server.enqueue(200, "not json at all")
        request = urllib.request.Request(server.url + "/x", data=b"{}", method="POST")
        with urllib.request.urlopen(request, timeout=5) as response:
            assert response.read().decode() == "not json at all"
