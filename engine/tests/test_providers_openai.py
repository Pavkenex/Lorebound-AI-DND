"""OpenAI-compatible adapter: wire mapping in both directions + error policy.

Uses the stdlib fake server: canned vendor payloads in, captured requests out.
"""
from __future__ import annotations

import json

import pytest
from test_providers_server import FakeProviderServer, openai_payload

from engine.models import ChatMessage, ChatRequest, ProviderConfig, ToolCall
from engine.providers.base import ProviderError
from engine.providers.jsonproto import delta_tool_schema
from engine.providers.openai_compat import OpenAICompatAdapter, parse_arguments

API_KEY = "sk-openai-test-key"


def make_adapter(
    server: FakeProviderServer,
    *,
    extra: dict | None = None,
    max_retries: int = 2,
    model: str = "local-model",
    api_key: str | None = API_KEY,
    name: str = "local",
) -> OpenAICompatAdapter:
    cfg = ProviderConfig(
        name=name,
        model=model,
        base_url=f"{server.url}/v1",
        api_mode="openai",
        timeout_s=5.0,
        max_retries=max_retries,
        extra=extra or {},
    )
    return OpenAICompatAdapter(cfg, api_key=api_key, backoff_base_s=0.0)


def simple_request(**overrides) -> ChatRequest:
    values = {
        "model": "local-model",
        "messages": [ChatMessage(role="user", content="I draw my sword.")],
        "max_tokens": 256,
        "temperature": 0.5,
    }
    values.update(overrides)
    return ChatRequest(**values)


def test_complete_normalizes_response() -> None:
    with FakeProviderServer() as server:
        server.enqueue(
            200,
            openai_payload(
                text="Steel rings in the yard.",
                tool_calls=[
                    ("call_1", "propose_state_deltas", {"deltas": [{"kind": "hp"}]}),
                ],
                usage={"prompt_tokens": 100, "completion_tokens": 20, "total_tokens": 120},
                model="local-model-7b",
            ),
        )
        adapter = make_adapter(server)
        response = adapter.complete(simple_request())
    assert response.text == "Steel rings in the yard."
    assert response.tool_calls == [
        ToolCall(id="call_1", name="propose_state_deltas", arguments={"deltas": [{"kind": "hp"}]})
    ]
    assert response.usage == {"prompt_tokens": 100, "completion_tokens": 20, "total_tokens": 120}
    assert (response.provider, response.model) == ("local", "local-model-7b")
    assert response.raw["id"] == "chatcmpl-test"


def test_request_is_openai_shaped() -> None:
    with FakeProviderServer() as server:
        server.enqueue(200, openai_payload(text="ok"))
        adapter = make_adapter(server)
        adapter.complete(
            simple_request(
                messages=[
                    ChatMessage(role="system", content="You are the narrator."),
                    ChatMessage(role="user", content="I draw my sword."),
                    ChatMessage(
                        role="assistant",
                        content="",
                        tool_calls=[
                            ToolCall(
                                id="call_1",
                                name="propose_state_deltas",
                                arguments={"deltas": []},
                            )
                        ],
                    ),
                    ChatMessage(role="tool", content='{"accepted": 0}', tool_call_id="call_1"),
                ],
                tools=delta_tool_schema(),
            )
        )
        captured = server.last_request()
    assert captured.method == "POST"
    assert captured.path == "/v1/chat/completions"
    assert captured.headers["authorization"] == f"Bearer {API_KEY}"
    assert captured.headers["content-type"].startswith("application/json")
    body = captured.body
    assert body["model"] == "local-model"
    assert body["stream"] is False
    assert body["max_tokens"] == 256
    assert body["temperature"] == 0.5
    assert body["tool_choice"] == "auto"
    assert body["tools"][0]["function"]["name"] == "propose_state_deltas"
    assert body["messages"][0] == {"role": "system", "content": "You are the narrator."}
    assert body["messages"][1] == {"role": "user", "content": "I draw my sword."}
    assistant = body["messages"][2]
    assert assistant["role"] == "assistant"
    assert assistant["content"] is None
    assert assistant["tool_calls"][0]["id"] == "call_1"
    assert assistant["tool_calls"][0]["type"] == "function"
    assert json.loads(assistant["tool_calls"][0]["function"]["arguments"]) == {"deltas": []}
    assert body["messages"][3] == {
        "role": "tool",
        "tool_call_id": "call_1",
        "content": '{"accepted": 0}',
    }


def test_no_tools_and_no_api_key_for_local_servers() -> None:
    with FakeProviderServer() as server:
        server.enqueue(200, openai_payload(text="ok"))
        adapter = make_adapter(server, api_key=None)
        adapter.complete(simple_request())
        captured = server.last_request()
    body = captured.body
    assert "tools" not in body
    assert "tool_choice" not in body
    assert "authorization" not in captured.headers


def test_tool_arguments_are_parsed_tolerantly() -> None:
    with FakeProviderServer() as server:
        server.enqueue(
            200,
            openai_payload(
                tool_calls=[
                    ("call_1", "propose_state_deltas", '```json\n{"deltas": [{"kind": "hp"}]}\n```'),
                    ("call_2", "propose_state_deltas", "certainly! not json"),
                ]
            ),
        )
        response = make_adapter(server).complete(simple_request())
    assert response.tool_calls[0].arguments == {"deltas": [{"kind": "hp"}]}
    assert response.tool_calls[1].arguments == {}


def test_parse_arguments_handles_every_shape() -> None:
    assert parse_arguments({"a": 1}) == {"a": 1}
    assert parse_arguments('{"a": 1}') == {"a": 1}
    assert parse_arguments('Here you go: {"a": 1}') == {"a": 1}
    assert parse_arguments("[1, 2]") == {}
    assert parse_arguments(None) == {}
    assert parse_arguments("") == {}


def test_429_is_retried_then_succeeds() -> None:
    with FakeProviderServer() as server:
        server.enqueue(429, {"error": {"message": "rate limited"}})
        server.enqueue(200, openai_payload(text="second try"))
        response = make_adapter(server).complete(simple_request())
        assert len(server.requests) == 2
    assert response.text == "second try"


def test_retries_exhausted_on_5xx() -> None:
    with FakeProviderServer() as server:
        for _ in range(3):
            server.enqueue(503, {"error": {"message": "upstream busy"}})
        with pytest.raises(ProviderError) as info:
            make_adapter(server, max_retries=2).complete(simple_request())
        assert len(server.requests) == 3
    assert info.value.retryable is True
    assert info.value.status == 503


def test_client_error_is_not_retried_and_never_leaks_the_key() -> None:
    with FakeProviderServer() as server:
        server.enqueue(401, {"error": {"message": f"bad key {API_KEY}"}})
        with pytest.raises(ProviderError) as info:
            make_adapter(server).complete(simple_request())
        assert len(server.requests) == 1
    assert info.value.retryable is False
    assert API_KEY not in str(info.value)
    assert "***" in str(info.value)


def test_streaming_is_rejected() -> None:
    with FakeProviderServer() as server:
        with pytest.raises(ProviderError) as info:
            make_adapter(server).complete(simple_request(stream=True))
        assert server.requests == []
    assert info.value.retryable is False


def test_extra_body_headers_and_token_field_overrides() -> None:
    extra = {
        "extra_body": {"top_p": 0.9},
        "headers": {"x-custom-auth": "proxy"},
        "max_tokens_field": "max_completion_tokens",
        "tool_choice": "required",
    }
    with FakeProviderServer() as server:
        server.enqueue(200, openai_payload(text="ok"))
        make_adapter(server, extra=extra).complete(simple_request(tools=delta_tool_schema()))
        captured = server.last_request()
    assert captured.body["top_p"] == 0.9
    assert "max_tokens" not in captured.body
    assert captured.body["max_completion_tokens"] == 256
    assert captured.body["tool_choice"] == "required"
    assert captured.headers["x-custom-auth"] == "proxy"
    assert captured.headers["authorization"] == f"Bearer {API_KEY}"


def test_error_object_in_200_body_is_normalized() -> None:
    with FakeProviderServer() as server:
        server.enqueue(200, {"error": {"message": "quota exceeded", "type": "quota"}})
        with pytest.raises(ProviderError) as info:
            make_adapter(server).complete(simple_request())
        assert len(server.requests) == 1
    assert info.value.retryable is False
    assert "quota exceeded" in str(info.value)


def test_missing_usage_and_null_content_are_tolerated() -> None:
    with FakeProviderServer() as server:
        server.enqueue(200, {"choices": [{"index": 0, "message": {"role": "assistant"}}]})
        response = make_adapter(server).complete(simple_request())
    assert response.text == ""
    assert response.tool_calls == []
    assert response.usage == {}
    assert response.model == "local-model"
