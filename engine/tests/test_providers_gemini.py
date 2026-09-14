"""Gemini generateContent adapter: wire mapping both directions + error policy."""
from __future__ import annotations

import pytest
from test_providers_server import FakeProviderServer, gemini_payload

from engine.models import ChatMessage, ChatRequest, ProviderConfig, ToolCall
from engine.providers.base import ProviderError
from engine.providers.gemini import GeminiAdapter
from engine.providers.jsonproto import delta_tool_schema

API_KEY = "gm-test-key"


def make_adapter(
    server: FakeProviderServer,
    *,
    model: str = "gemini-test",
    max_retries: int = 2,
) -> GeminiAdapter:
    cfg = ProviderConfig(
        name="gemini",
        model=model,
        base_url=f"{server.url}",
        api_mode="gemini",
        timeout_s=5.0,
        max_retries=max_retries,
    )
    return GeminiAdapter(cfg, api_key=API_KEY, backoff_base_s=0.0)


def simple_request(**overrides) -> ChatRequest:
    values = {
        "model": "gemini-test",
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
            gemini_payload(
                texts=["Steel rings.", "Someone laughs."],
                function_calls=[("propose_state_deltas", {"deltas": [{"kind": "hp"}]})],
            ),
        )
        response = make_adapter(server).complete(simple_request())
    assert response.text == "Steel rings.\nSomeone laughs."
    assert response.tool_calls == [
        ToolCall(id="", name="propose_state_deltas", arguments={"deltas": [{"kind": "hp"}]})
    ]
    assert response.usage == {"prompt_tokens": 17, "completion_tokens": 9, "total_tokens": 26}
    assert response.provider == "gemini"
    assert response.model == "gemini-test"


def test_request_is_gemini_shaped() -> None:
    with FakeProviderServer() as server:
        server.enqueue(200, gemini_payload(texts=["ok"]))
        make_adapter(server).complete(
            simple_request(
                messages=[
                    ChatMessage(role="system", content="You are the narrator."),
                    ChatMessage(role="user", content="I draw my sword."),
                    ChatMessage(
                        role="assistant",
                        content="You move first.",
                        tool_calls=[
                            ToolCall(id="fc_1", name="propose_state_deltas", arguments={"deltas": []})
                        ],
                    ),
                    ChatMessage(role="tool", content='{"accepted": 0}', tool_call_id="fc_1"),
                ],
                tools=delta_tool_schema(),
            )
        )
        captured = server.last_request()
    assert captured.path == "/v1beta/models/gemini-test:generateContent"
    assert "key=" not in captured.path
    assert captured.headers["x-goog-api-key"] == API_KEY
    body = captured.body
    assert body["systemInstruction"] == {"parts": [{"text": "You are the narrator."}]}
    assert body["generationConfig"] == {"maxOutputTokens": 512, "temperature": 0.5}
    contents = body["contents"]
    assert contents[0] == {"role": "user", "parts": [{"text": "I draw my sword."}]}
    assert contents[1] == {
        "role": "model",
        "parts": [
            {"text": "You move first."},
            {"functionCall": {"name": "propose_state_deltas", "args": {"deltas": []}}},
        ],
    }
    assert contents[2] == {
        "role": "user",
        "parts": [
            {
                "functionResponse": {
                    "name": "propose_state_deltas",
                    "response": {"content": '{"accepted": 0}'},
                }
            }
        ],
    }
    assert body["tools"] == [
        {
            "functionDeclarations": [
                {
                    "name": "propose_state_deltas",
                    "description": delta_tool_schema()[0]["function"]["description"],
                    "parameters": delta_tool_schema()[0]["function"]["parameters"],
                }
            ]
        }
    ]


def test_tool_result_falls_back_to_the_call_id_when_unknown() -> None:
    with FakeProviderServer() as server:
        server.enqueue(200, gemini_payload(texts=["ok"]))
        make_adapter(server).complete(
            simple_request(
                messages=[
                    ChatMessage(role="user", content="go"),
                    ChatMessage(role="tool", content="result", tool_call_id="fc_unknown"),
                ]
            )
        )
        contents = server.last_request().body["contents"]
    # consecutive user-role contents merge, so the response part rides along
    assert [part for part in contents[-1]["parts"] if "functionResponse" in part] == [
        {"functionResponse": {"name": "fc_unknown", "response": {"content": "result"}}}
    ]


def test_model_prefix_is_stripped_and_generation_config_defaults() -> None:
    with FakeProviderServer() as server:
        server.enqueue(200, gemini_payload(texts=["ok"]))
        make_adapter(server, model="models/gemini-flash").complete(
            simple_request(model="", max_tokens=0, messages=[ChatMessage(role="user", content="hi")])
        )
        captured = server.last_request()
    assert captured.path == "/v1beta/models/gemini-flash:generateContent"
    assert captured.body["generationConfig"]["maxOutputTokens"] == 800
    assert "systemInstruction" not in captured.body
    assert "tools" not in captured.body


def test_429_is_retried_then_succeeds() -> None:
    with FakeProviderServer() as server:
        server.enqueue(429, {"error": {"code": 429, "message": "quota"}})
        server.enqueue(200, gemini_payload(texts=["retried"]))
        response = make_adapter(server).complete(simple_request())
        assert len(server.requests) == 2
    assert response.text == "retried"


def test_client_error_is_not_retried_and_never_leaks_the_key() -> None:
    with FakeProviderServer() as server:
        server.enqueue(403, {"error": {"code": 403, "message": f"API key not valid: {API_KEY}"}})
        with pytest.raises(ProviderError) as info:
            make_adapter(server).complete(simple_request())
        assert len(server.requests) == 1
    assert info.value.retryable is False
    assert API_KEY not in str(info.value)
    assert "***" in str(info.value)


def test_blocked_prompt_raises_non_retryable() -> None:
    with FakeProviderServer() as server:
        server.enqueue(200, {"promptFeedback": {"blockReason": "SAFETY"}})
        with pytest.raises(ProviderError) as info:
            make_adapter(server).complete(simple_request())
        assert len(server.requests) == 1
    assert info.value.retryable is False
    assert "SAFETY" in str(info.value)


def test_no_candidates_is_malformed() -> None:
    with FakeProviderServer() as server:
        server.enqueue(200, {"candidates": []})
        with pytest.raises(ProviderError) as info:
            make_adapter(server).complete(simple_request())
        assert len(server.requests) == 1
    assert info.value.retryable is False


def test_error_object_in_200_body_is_normalized() -> None:
    with FakeProviderServer() as server:
        server.enqueue(200, {"error": {"code": 400, "message": "bad request"}})
        with pytest.raises(ProviderError) as info:
            make_adapter(server).complete(simple_request())
    assert "bad request" in str(info.value)
    assert info.value.retryable is False
