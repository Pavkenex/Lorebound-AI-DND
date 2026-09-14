"""Anthropic Messages API adapter (spec §7).

Wire differences handled here: ``system`` is a top-level field (never a
message); content is a block list (``text`` / ``tool_use`` / ``tool_result``);
``max_tokens`` is required; tool schemas carry ``input_schema`` instead of the
OpenAI ``function.parameters`` wrapper; roles must alternate, so consecutive
same-role messages are merged (tool results become user-side blocks).

Normalized -> wire: ``ChatMessage.tool_calls`` -> ``tool_use`` blocks,
``role="tool"`` -> ``tool_result`` blocks (following the assistant message).
"""
from __future__ import annotations

import json
from typing import Any

from ..models import ChatMessage, ChatRequest, ChatResponse, ToolCall
from .base import ProviderError
from .openai_compat import parse_arguments
from .transport import AdapterBase, as_int, extract_error_message, redact

DEFAULT_BASE_URL = "https://api.anthropic.com"
MESSAGES_PATH = "/v1/messages"
ANTHROPIC_VERSION = "2023-06-01"
DEFAULT_MAX_TOKENS = 1024


def split_system(messages: list[ChatMessage]) -> tuple[str, list[ChatMessage]]:
    """Pull system messages out of the list (Anthropic wants them separate)."""
    parts = [message.content for message in messages if message.role == "system" and message.content]
    rest = [message for message in messages if message.role != "system"]
    return "\n\n".join(parts), rest


def to_wire_messages(messages: list[ChatMessage]) -> list[dict]:
    """Normalized messages (system stripped) -> Anthropic message list.

    Consecutive same-role messages are merged into one entry because the API
    requires alternating user/assistant turns.
    """
    wire: list[dict] = []

    def append(role: str, blocks: list[dict]) -> None:
        blocks = [block for block in blocks if block]
        if not blocks:
            return
        if wire and wire[-1]["role"] == role:
            wire[-1]["content"].extend(blocks)
        else:
            wire.append({"role": role, "content": list(blocks)})

    for message in messages:
        role = message.role or "user"
        if role == "assistant":
            blocks: list[dict] = []
            if message.content:
                blocks.append({"type": "text", "text": message.content})
            for index, call in enumerate(message.tool_calls or []):
                blocks.append(
                    {
                        "type": "tool_use",
                        "id": call.id or f"toolu_{index}",
                        "name": call.name,
                        "input": dict(call.arguments or {}),
                    }
                )
            append("assistant", blocks)
        elif role == "tool":
            append(
                "user",
                [
                    {
                        "type": "tool_result",
                        "tool_use_id": message.tool_call_id,
                        "content": message.content,
                    }
                ],
            )
        elif message.content:
            append("user", [{"type": "text", "text": message.content}])
    return wire


def to_wire_tools(tools: list | None) -> list[dict]:
    """Normalized (OpenAI-style) tool schemas -> Anthropic tool definitions."""
    wire: list[dict] = []
    for tool in tools or []:
        if not isinstance(tool, dict):
            continue
        function = tool.get("function")
        source = function if isinstance(function, dict) else tool
        schema = source.get("input_schema") or source.get("parameters")
        wire.append(
            {
                "name": str(source.get("name") or ""),
                "description": str(source.get("description") or ""),
                "input_schema": dict(schema) if isinstance(schema, dict) else {
                    "type": "object",
                    "properties": {},
                },
            }
        )
    return wire


def normalize_usage(usage: Any) -> dict:
    if not isinstance(usage, dict):
        return {}
    prompt = as_int(usage.get("input_tokens"))
    completion = as_int(usage.get("output_tokens"))
    return {
        "prompt_tokens": prompt,
        "completion_tokens": completion,
        "total_tokens": prompt + completion,
    }


def from_wire_response(
    data: dict,
    *,
    provider: str,
    fallback_model: str,
    secrets: tuple[str, ...] = (),
) -> ChatResponse:
    """Anthropic wire body -> normalized ``ChatResponse``."""
    if isinstance(data.get("error"), (dict, str)):
        detail = extract_error_message(json.dumps(data))
        raise ProviderError(redact(f"provider error body: {detail}", secrets), retryable=False)
    content = data.get("content")
    if not isinstance(content, list):
        raise ProviderError("malformed provider response: no content blocks", retryable=False)
    texts: list[str] = []
    tool_calls: list[ToolCall] = []
    for block in content:
        if not isinstance(block, dict):
            continue
        if block.get("type") == "text" and isinstance(block.get("text"), str):
            texts.append(block["text"])
        elif block.get("type") == "tool_use":
            tool_calls.append(
                ToolCall(
                    id=str(block.get("id") or ""),
                    name=str(block.get("name") or ""),
                    arguments=parse_arguments(block.get("input")),
                )
            )
    return ChatResponse(
        text="\n".join(texts),
        tool_calls=tool_calls,
        usage=normalize_usage(data.get("usage")),
        provider=provider,
        model=str(data.get("model") or fallback_model),
        raw=data,
    )


class AnthropicAdapter(AdapterBase):
    """``POST {base_url}/v1/messages`` with ``x-api-key`` auth."""

    default_base_url = DEFAULT_BASE_URL

    def auth_headers(self) -> dict:
        headers = {"anthropic-version": ANTHROPIC_VERSION}
        if self.api_key:
            headers["x-api-key"] = self.api_key
        return headers

    def complete(self, request: ChatRequest) -> ChatResponse:
        self.require_non_streaming(request)
        system, messages = split_system(list(request.messages))
        payload: dict[str, Any] = {
            "model": self.model_for(request),
            "messages": to_wire_messages(messages),
            "max_tokens": as_int(request.max_tokens) or DEFAULT_MAX_TOKENS,
        }
        if system:
            payload["system"] = system
        if request.temperature is not None:
            payload["temperature"] = request.temperature
        tools = to_wire_tools(request.tools)
        if tools:
            payload["tools"] = tools
        payload.update(self.extra_body())
        data = self.post(self.base_url + MESSAGES_PATH, payload)
        return from_wire_response(
            data,
            provider=self.name,
            fallback_model=self.model_for(request),
            secrets=self.secrets(),
        )
