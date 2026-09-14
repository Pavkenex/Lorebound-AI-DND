"""Google Gemini ``generateContent`` adapter (spec §7).

Wire differences handled here: ``contents``/``parts`` (roles ``user``/``model``)
with a separate ``systemInstruction``; tool calls are ``functionCall`` parts and
tool results ``functionResponse`` parts (which need the *function name*, mapped
back from the ids of earlier ``tool_calls``); generation limits live under
``generationConfig``.

Auth uses the ``x-goog-api-key`` header — never a query parameter, so the key
cannot leak through URLs in exception text or request logs.
"""
from __future__ import annotations

import json
from typing import Any

from ..models import ChatMessage, ChatRequest, ChatResponse, ToolCall
from .base import ProviderError
from .openai_compat import parse_arguments
from .transport import AdapterBase, as_int, extract_error_message, redact

DEFAULT_BASE_URL = "https://generativelanguage.googleapis.com"
GENERATE_PATH = "/v1beta/models/{model}:generateContent"
DEFAULT_MAX_TOKENS = 800
DEFAULT_TOOL_RESULT_NAME = "tool_result"


def to_wire_contents(messages: list[ChatMessage]) -> tuple[list[dict], dict | None]:
    """Normalized messages -> ``(contents, systemInstruction)``.

    Tool results are matched to function names through the ids of previous
    assistant ``tool_calls``; consecutive same-role contents are merged.
    """
    names = {
        call.id: call.name
        for message in messages
        for call in (message.tool_calls or [])
        if call.id
    }
    system_parts: list[str] = []
    contents: list[dict] = []

    def append(role: str, parts: list[dict]) -> None:
        parts = [part for part in parts if part]
        if not parts:
            return
        if contents and contents[-1]["role"] == role:
            contents[-1]["parts"].extend(parts)
        else:
            contents.append({"role": role, "parts": list(parts)})

    for message in messages:
        role = message.role or "user"
        if role == "system":
            if message.content:
                system_parts.append(message.content)
        elif role == "assistant":
            parts: list[dict] = []
            if message.content:
                parts.append({"text": message.content})
            for call in message.tool_calls or []:
                parts.append({"functionCall": {"name": call.name, "args": dict(call.arguments or {})}})
            append("model", parts)
        elif role == "tool":
            name = names.get(message.tool_call_id) or message.tool_call_id or DEFAULT_TOOL_RESULT_NAME
            append(
                "user",
                [
                    {
                        "functionResponse": {
                            "name": name,
                            "response": {"content": message.content},
                        }
                    }
                ],
            )
        elif message.content:
            append("user", [{"text": message.content}])

    system = {"parts": [{"text": part} for part in system_parts]} if system_parts else None
    return contents, system


def to_wire_tools(tools: list | None) -> list[dict]:
    """Normalized (OpenAI-style) tool schemas -> Gemini functionDeclarations."""
    declarations: list[dict] = []
    for tool in tools or []:
        if not isinstance(tool, dict):
            continue
        function = tool.get("function")
        source = function if isinstance(function, dict) else tool
        parameters = source.get("parameters") or source.get("input_schema")
        declarations.append(
            {
                "name": str(source.get("name") or ""),
                "description": str(source.get("description") or ""),
                "parameters": dict(parameters) if isinstance(parameters, dict) else {
                    "type": "object",
                    "properties": {},
                },
            }
        )
    return [{"functionDeclarations": declarations}] if declarations else []


def normalize_usage(usage: Any) -> dict:
    if not isinstance(usage, dict):
        return {}
    prompt = as_int(usage.get("promptTokenCount"))
    completion = as_int(usage.get("candidatesTokenCount"))
    return {
        "prompt_tokens": prompt,
        "completion_tokens": completion,
        "total_tokens": as_int(usage.get("totalTokenCount")) or prompt + completion,
    }


def from_wire_response(
    data: dict,
    *,
    provider: str,
    fallback_model: str,
    secrets: tuple[str, ...] = (),
) -> ChatResponse:
    """Gemini wire body -> normalized ``ChatResponse``."""
    if isinstance(data.get("error"), (dict, str)):
        detail = extract_error_message(json.dumps(data))
        raise ProviderError(redact(f"provider error body: {detail}", secrets), retryable=False)
    candidates = data.get("candidates")
    if not isinstance(candidates, list) or not candidates or not isinstance(candidates[0], dict):
        feedback = data.get("promptFeedback")
        reason = feedback.get("blockReason") if isinstance(feedback, dict) else None
        if reason:
            raise ProviderError(
                redact(f"provider blocked the request: {reason}", secrets), retryable=False
            )
        raise ProviderError("malformed provider response: no candidates", retryable=False)
    content = candidates[0].get("content")
    parts = content.get("parts") if isinstance(content, dict) else None
    texts: list[str] = []
    tool_calls: list[ToolCall] = []
    for part in parts if isinstance(parts, list) else []:
        if not isinstance(part, dict):
            continue
        if isinstance(part.get("text"), str):
            texts.append(part["text"])
        elif isinstance(part.get("functionCall"), dict):
            call = part["functionCall"]
            tool_calls.append(
                ToolCall(
                    id=str(call.get("id") or ""),
                    name=str(call.get("name") or ""),
                    arguments=parse_arguments(call.get("args")),
                )
            )
    return ChatResponse(
        text="\n".join(texts),
        tool_calls=tool_calls,
        usage=normalize_usage(data.get("usageMetadata")),
        provider=provider,
        model=str(data.get("modelVersion") or fallback_model),
        raw=data,
    )


class GeminiAdapter(AdapterBase):
    """``POST {base_url}/v1beta/models/{model}:generateContent``."""

    default_base_url = DEFAULT_BASE_URL

    def auth_headers(self) -> dict:
        return {"x-goog-api-key": self.api_key} if self.api_key else {}

    def complete(self, request: ChatRequest) -> ChatResponse:
        self.require_non_streaming(request)
        model = self.model_for(request)
        if model.startswith("models/"):
            model = model[len("models/") :]
        contents, system = to_wire_contents(list(request.messages))
        payload: dict[str, Any] = {"contents": contents}
        if system:
            payload["systemInstruction"] = system
        config: dict[str, Any] = {
            "maxOutputTokens": as_int(request.max_tokens) or DEFAULT_MAX_TOKENS,
        }
        if request.temperature is not None:
            config["temperature"] = request.temperature
        payload["generationConfig"] = config
        tools = to_wire_tools(request.tools)
        if tools:
            payload["tools"] = tools
        payload.update(self.extra_body())
        data = self.post(self.base_url + GENERATE_PATH.format(model=model), payload)
        return from_wire_response(
            data,
            provider=self.name,
            fallback_model=model,
            secrets=self.secrets(),
        )
