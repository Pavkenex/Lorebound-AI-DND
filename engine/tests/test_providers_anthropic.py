"""Anthropic Messages adapter: wire mapping both directions + error policy."""
from __future__ import annotations

import pytest
from test_providers_server import FakeProviderServer, anthropic_payload

from engine.models import ChatMessage, ChatRequest, ProviderConfig, ToolCall
from engine.providers.anthropic import AnthropicAdapter
from engine.providers.base import ProviderError
from engine.providers.jsonproto import delta_tool_schema

API_KEY = "sk-ant-test-key"


def make_adapter(server: FakeProviderServer, *, max_retries: int = 2) -> AnthropicAdapter:
    cfg = ProviderConfig(
        name="anthropic",
        model="claude-test",
        base_url=f"{server.url}",
        api_mode="anthropic",
        timeout_s=5.0,
        max_retries=max_retries,
    )
    return AnthropicAdapter(cfg, api_key=API_KEY, backoff_base_s=0.0)


def simple_request(**overrides) -> ChatRequest:
    values = {
        "model": "claude-test",
        "messages": [ChatMessage(role="user", content="I draw my sword.")],
        "max_tokens": 512,
        "temperature": 0.5,
    }
    values.update(overrides)
    return ChatRequest(**values)


def test_complete_normalizes_response() -> None:
    with FakeProviderServer() as server:
        server.enqueue(
            200,
            anthropic_payload(
                texts=["Steel rings.", "Someone laughs."],
                tool_uses=[("toolu_1", "propose_state_deltas", {"deltas": [{"kind": "hp"}]})],
            ),
        )
        response = make_adapter(server).complete(simple_request())
    assert response.text == "Steel rings.\nSomeone laughs."
    assert response.tool_calls == [
        ToolCall(id="toolu_1", name="propose_state_deltas", arguments={"deltas": [{"kind": "hp"}]})
    ]
    assert response.usage == {"prompt_tokens": 13, "completion_tokens": 7, "total_tokens": 20}
    assert response.provider == "anthropic"
    assert response.model == "claude-test"


def test_request_is_anthropic_shaped() -> None:
    with FakeProviderServer() as server:
        server.enqueue(200, anthropic_payload(texts=["ok"]))
        make_adapter(server).complete(
            simple_request(
                messages=[
                    ChatMessage(role="system", content="You are the narrator."),
                    ChatMessage(role="system", content="Never break the pins."),
                    ChatMessage(role="user", content="I draw my sword."),
                    ChatMessage(
                        role="assistant",
                        content="You move first.",
                        tool_calls=[
                            ToolCall(
                                id="toolu_1",
                                name="propose_state_deltas",
                                arguments={"deltas": []},
                            )
                        ],
                    ),
                    ChatMessage(role="tool", content='{"accepted": 0}', tool_call_id="toolu_1"),
                ],
                tools=delta_tool_schema(),
            )
        )
        captured = server.last_request()
    assert captured.path == "/v1/messages"
    assert captured.headers["x-api-key"] == API_KEY
    assert captured.headers["anthropic-version"] == "2023-06-01"
    body = captured.body
    assert body["model"] == "claude-test"
    assert body["system"] == "You are the narrator.\n\nNever break the pins."
    assert body["max_tokens"] == 512
    assert body["temperature"] == 0.5
    assert "messages" in body and all(m["role"] != "system" for m in body["messages"])
    assert body["messages"][0] == {
        "role": "user",
        "content": [{"type": "text", "text": "I draw my sword."}],
    }
    assistant = body["messages"][1]
    assert assistant["role"] == "assistant"
    assert assistant["content"][0] == {"type": "text", "text": "You move first."}
    assert assistant["content"][1] == {
        "type": "tool_use",
        "id": "toolu_1",
        "name": "propose_state_deltas",
        "input": {"deltas": []},
    }
    assert body["messages"][2] == {
        "role": "user",
        "content": [
            {"type": "tool_result", "tool_use_id": "toolu_1", "content": '{"accepted": 0}'}
        ],
    }
    tool = body["tools"][0]
    assert tool["name"] == "propose_state_deltas"
    assert tool["input_schema"]["type"] == "object"
    assert "function" not in tool
    assert "tool_choice" not in body


def test_consecutive_same_role_messages_are_merged() -> None:
    with FakeProviderServer() as server:
        server.enqueue(200, anthropic_payload(texts=["ok"]))
        make_adapter(server).complete(
            simple_request(
                messages=[
                    ChatMessage(role="user", content="first"),
                    ChatMessage(role="user", content="second"),
                    ChatMessage(role="assistant", content="reply"),
                    ChatMessage(role="user", content="third"),
                ]
            )
        )
        body = server.last_request().body
    assert body["messages"] == [
        {
            "role": "user",
            "content": [{"type": "text", "text": "first"}, {"type": "text", "text": "second"}],
        },
        {"role": "assistant", "content": [{"type": "text", "text": "reply"}]},
        {"role": "user", "content": [{"type": "text", "text": "third"}]},
    ]


def test_parallel_tool_results_share_one_user_message() -> None:
    with FakeProviderServer() as server:
        server.enqueue(200, anthropic_payload(texts=["ok"]))
        make_adapter(server).complete(
            simple_request(
                messages=[
                    ChatMessage(role="user", content="go"),
                    ChatMessage(
                        role="assistant",
                        content="",
                        tool_calls=[
                            ToolCall(id="toolu_1", name="a", arguments={}),
                            ToolCall(id="toolu_2", name="b", arguments={}),
                        ],
                    ),
                    ChatMessage(role="tool", content="ra", tool_call_id="toolu_1"),
                    ChatMessage(role="tool", content="rb", tool_call_id="toolu_2"),
                ]
            )
        )
        body = server.last_request().body
    assert [len(m["content"]) for m in body["messages"]] == [1, 2, 2]
    results = body["messages"][2]["content"]
    assert [block["tool_use_id"] for block in results] == ["toolu_1", "toolu_2"]
    assert all(block["type"] == "tool_result" for block in results)


def test_max_tokens_is_always_sent() -> None:
    with FakeProviderServer() as server:
        server.enqueue(200, anthropic_payload(texts=["ok"]))
        make_adapter(server).complete(simple_request(max_tokens=0))
        assert server.last_request().body["max_tokens"] == 1024


def test_system_and_tools_are_omitted_when_absent() -> None:
    with FakeProviderServer() as server:
        server.enqueue(200, anthropic_payload(texts=["ok"]))
        make_adapter(server).complete(simple_request())
        body = server.last_request().body
    assert "system" not in body
    assert "tools" not in body
    assert "authorization" not in server.last_request().headers


def test_429_is_retried_then_succeeds() -> None:
    with FakeProviderServer() as server:
        server.enqueue(429, {"error": {"type": "rate_limit_error", "message": "slow down"}})
        server.enqueue(200, anthropic_payload(texts=["retried"]))
        response = make_adapter(server).complete(simple_request())
        assert len(server.requests) == 2
    assert response.text == "retried"


def test_error_object_in_200_body_is_normalized() -> None:
    with FakeProviderServer() as server:
        server.enqueue(200, {"error": {"type": "overloaded_error", "message": "busy"}})
        with pytest.raises(ProviderError) as info:
            make_adapter(server).complete(simple_request())
        assert len(server.requests) == 1
    assert info.value.retryable is False
    assert "busy" in str(info.value)


def test_missing_content_blocks_is_a_malformed_response() -> None:
    with FakeProviderServer() as server:
        server.enqueue(200, {"id": "msg_test", "role": "assistant"})
        with pytest.raises(ProviderError) as info:
            make_adapter(server).complete(simple_request())
    assert info.value.retryable is False
