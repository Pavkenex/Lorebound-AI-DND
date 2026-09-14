"""OpenAI-compatible ``chat/completions`` adapter (spec §7).

Covers OpenAI, gateways (OpenRouter, Azure-style proxies) and local servers
(llama.cpp, LM Studio, vLLM, Ollama's OpenAI endpoint): anything speaking
``POST {base_url}/chat/completions``. Point ``cfg.base_url`` at the server
(e.g. ``http://127.0.0.1:11434/v1``) and set ``cfg.api_mode="openai"``;
``cfg.extra["extra_body"]``/``cfg.extra["headers"]`` carry provider-specific
knobs, ``cfg.extra["max_tokens_field"]`` the token-field name for endpoints
that renamed it.

Wire -> normalized: ``choices[0].message.content`` -> ``ChatResponse.text``;
``tool_calls`` -> ``ToolCall`` list with tolerantly decoded JSON arguments
(fences/preamble tolerated, ``{}`` when unusable); ``usage`` normalized to
prompt/completion/total tokens.
"""
from __future__ import annotations

import json
from typing import Any

from ..models import ChatMessage, ChatRequest, ChatResponse, ToolCall
from .base import ProviderError
from .jsonproto import parse_tolerant
from .transport import AdapterBase, as_int, extract_error_message, redact

DEFAULT_BASE_URL = "https://api.openai.com/v1"
CHAT_COMPLETIONS_PATH = "/chat/completions"
DEFAULT_MAX_TOKENS = 800


def parse_arguments(raw: Any) -> dict:
    """Tool-call arguments as a dict: dict | JSON string | fenced JSON | ``{}``."""
    if isinstance(raw, dict):
        return raw
    if isinstance(raw, str) and raw.strip():
        try:
            value = json.loads(raw)
        except ValueError:
            value = None
        if isinstance(value, dict):
            return value
        parsed = parse_tolerant(raw)
        if isinstance(parsed, dict):
            return parsed
    return {}


def to_wire_messages(messages: list[ChatMessage]) -> list[dict]:
    """Normalized messages -> OpenAI wire messages (tool_calls round-trip ready)."""
    wire: list[dict] = []
    for message in messages:
        role = message.role or "user"
        if role == "assistant" and message.tool_calls:
            wire.append(
                {
                    "role": "assistant",
                    "content": message.content or None,
                    "tool_calls": [
                        {
                            "id": call.id or f"call_{index}",
                            "type": "function",
                            "function": {
                                "name": call.name,
                                "arguments": json.dumps(call.arguments or {}),
                            },
                        }
                        for index, call in enumerate(message.tool_calls)
                    ],
                }
            )
        elif role == "tool":
            wire.append(
                {"role": "tool", "tool_call_id": message.tool_call_id, "content": message.content}
            )
        else:
            wire.append({"role": role, "content": message.content})
    return wire


def normalize_usage(usage: Any) -> dict:
    if not isinstance(usage, dict):
        return {}
    prompt = as_int(usage.get("prompt_tokens"))
    completion = as_int(usage.get("completion_tokens"))
    return {
        "prompt_tokens": prompt,
        "completion_tokens": completion,
        "total_tokens": as_int(usage.get("total_tokens")) or prompt + completion,
    }


def from_wire_response(
    data: dict,
    *,
    provider: str,
    fallback_model: str,
    secrets: tuple[str, ...] = (),
) -> ChatResponse:
    """OpenAI wire body -> normalized ``ChatResponse``."""
    if isinstance(data.get("error"), (dict, str)):
        detail = extract_error_message(json.dumps(data))
        raise ProviderError(redact(f"provider error body: {detail}", secrets), retryable=False)
    choices = data.get("choices")
    if not isinstance(choices, list) or not choices or not isinstance(choices[0], dict):
        raise ProviderError("malformed provider response: no choices", retryable=False)
    message = choices[0].get("message")
    message = message if isinstance(message, dict) else {}
    content = message.get("content")
    tool_calls = [
        ToolCall(
            id=str(call.get("id") or ""),
            name=str((call.get("function") or {}).get("name") or ""),
            arguments=parse_arguments((call.get("function") or {}).get("arguments")),
        )
        for call in message.get("tool_calls") or []
        if isinstance(call, dict)
    ]
    return ChatResponse(
        text=content if isinstance(content, str) else "",
        tool_calls=tool_calls,
        usage=normalize_usage(data.get("usage")),
        provider=provider,
        model=str(data.get("model") or fallback_model),
        raw=data,
    )


class OpenAICompatAdapter(AdapterBase):
    """``POST {base_url}/chat/completions`` — the de-facto BYOK wire format."""

    default_base_url = DEFAULT_BASE_URL

    def auth_headers(self) -> dict:
        return {"authorization": f"Bearer {self.api_key}"} if self.api_key else {}

    def complete(self, request: ChatRequest) -> ChatResponse:
        self.require_non_streaming(request)
        max_tokens_key = str(self.cfg.extra.get("max_tokens_field") or "max_tokens")
        max_tokens = as_int(request.max_tokens) or DEFAULT_MAX_TOKENS
        payload: dict[str, Any] = {
            "model": self.model_for(request),
            "messages": to_wire_messages(list(request.messages)),
            max_tokens_key: max_tokens,
            "temperature": request.temperature,
            "stream": False,
        }
        if request.tools:
            payload["tools"] = [dict(tool) for tool in request.tools]
            payload["tool_choice"] = self.cfg.extra.get("tool_choice") or "auto"
        payload.update(self.extra_body())
        data = self.post(self.base_url + CHAT_COMPLETIONS_PATH, payload)
        return from_wire_response(
            data,
            provider=self.name,
            fallback_model=str(payload["model"]),
            secrets=self.secrets(),
        )
