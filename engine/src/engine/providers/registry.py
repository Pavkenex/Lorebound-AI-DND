"""Adapter construction + capability detection (spec §7).

- ``build_adapter(cfg, api_key=...)`` returns the right wire adapter for
  ``cfg.api_mode``. The key is a runtime input — never persisted.
- ``probe_capabilities(...)`` runs a ONCE-per key/model canary call (a tiny
  tool-call round trip) and caches the result in the ``provider_caps`` table
  when a store is given. When native tool calls are unreliable/unsupported,
  adapters report ``native_tools=False`` and the pipeline degrades to the
  JSON-in-text protocol (``jsonproto``).

Diagnostics-only slots (``streaming``, ``json_mode``, ``context_window``) are
never guessed by the canary: they come from ``cfg.context_window`` and from
``cfg.extra["caps"]`` declarations. Verdicts that came from a real model
response are cached (tools *and* no-tools); probes that failed before any
response are not, so a transient outage can never freeze a wrong verdict.

``probe_capabilities`` never raises: capability detection is advisory, and a
failed probe must cost quality, never the game (spec §7 quality floor).
"""
from __future__ import annotations

import dataclasses
import json
import time
from typing import Any

from ..models import ChatMessage, ChatRequest, ProviderCaps, ProviderConfig
from .anthropic import AnthropicAdapter
from .base import NarratorAdapter
from .gemini import GeminiAdapter
from .openai_compat import OpenAICompatAdapter
from .transport import AdapterBase, as_int

CANARY_TOOL_NAME = "report_capability"
CANARY_PROMPT = (
    "Capability canary: call the report_capability tool exactly once with "
    '{"ok": true}. Do not answer with text.'
)
CANARY_MAX_TOKENS = 64

_API_MODES: dict[str, Any] = {
    "openai": OpenAICompatAdapter,
    "openai_compat": OpenAICompatAdapter,
    "openai-compatible": OpenAICompatAdapter,
    "local": OpenAICompatAdapter,  # local servers speak the OpenAI wire
    "anthropic": AnthropicAdapter,
    "claude": AnthropicAdapter,
    "gemini": GeminiAdapter,
    "google": GeminiAdapter,
}

_NOT_DECLARABLE = frozenset({"native_tools", "probed_at"})
"""Slots only a probe may fill; ``cfg.extra['caps']`` cannot override them."""


def canary_tool_schema() -> list[dict]:
    """The canary tool: trivial to call, impossible to satisfy with prose."""
    return [
        {
            "type": "function",
            "function": {
                "name": CANARY_TOOL_NAME,
                "description": "Capability canary: call this tool exactly once.",
                "parameters": {
                    "type": "object",
                    "properties": {"ok": {"type": "boolean"}},
                    "required": ["ok"],
                },
            },
        }
    ]


def build_adapter(cfg: ProviderConfig, *, api_key: str | None = None) -> AdapterBase:
    """Wire adapter for ``cfg.api_mode`` (``openai`` | ``anthropic`` | ``gemini``).

    The returned adapter satisfies ``NarratorAdapter`` (``complete`` /
    ``capabilities``); being concrete it also carries the probe/transport knobs.
    """
    mode = (cfg.api_mode or "openai").strip().lower()
    adapter_cls = _API_MODES.get(mode)
    if adapter_cls is None:
        raise ValueError(
            f"unknown api_mode {cfg.api_mode!r}; expected one of {sorted(set(_API_MODES))}"
        )
    return adapter_cls(cfg, api_key=api_key)


def declared_caps(cfg: ProviderConfig) -> dict:
    """Non-probed capability slots from config (context window + declarations)."""
    fields = {field.name for field in dataclasses.fields(ProviderCaps)}
    declared: dict = {}
    if cfg.context_window is not None:
        declared["context_window"] = as_int(cfg.context_window)
    extra = cfg.extra.get("caps")
    if isinstance(extra, dict):
        declared.update(
            {
                key: value
                for key, value in extra.items()
                if key in fields and key not in _NOT_DECLARABLE
            }
        )
    return declared


def probe_capabilities(
    adapter: NarratorAdapter,
    cfg: ProviderConfig,
    *,
    store: Any = None,
    force: bool = False,
) -> ProviderCaps:
    """Canary-probe and (optionally) cache in ``provider_caps``. Never raises —
    on probe failure returns conservative defaults (native_tools=False).

    A cached verdict for this provider+model+base_url is reused unless
    ``force=True``. Verdicts that came from a real model response (tools or no
    tools) are cached; probes that failed before any response (timeout, HTTP
    error, sick store) are not, so a transient outage can never freeze a wrong
    verdict. Successful probes are attached to the adapter (if it exposes a
    ``caps`` attribute) so ``adapter.capabilities()`` reflects reality.
    """
    key = cache_key(cfg)
    if not force:
        cached = _read_cache(store, key)
        if cached is not None:
            _attach_caps(adapter, cached)
            return cached
    caps, answered = _run_canary(adapter, cfg)
    _attach_caps(adapter, caps)
    if answered:
        _write_cache(store, key, caps)
    return caps


def cache_key(cfg: ProviderConfig) -> str:
    """Stable cache key: provider + model + base_url (NO key material)."""
    return f"{cfg.name}|{cfg.model}|{cfg.base_url}|{cfg.api_mode}"


def _run_canary(adapter: NarratorAdapter, cfg: ProviderConfig) -> tuple[ProviderCaps, bool]:
    """``(caps, answered)`` — ``answered`` is False when no response came back."""
    declared = declared_caps(cfg)
    try:
        response = adapter.complete(
            ChatRequest(
                model=cfg.model,
                messages=[ChatMessage(role="user", content=CANARY_PROMPT)],
                tools=canary_tool_schema(),
                max_tokens=CANARY_MAX_TOKENS,
                temperature=0.0,
            )
        )
    except Exception:  # advisory probe: any failure means "no native tools"
        return ProviderCaps(native_tools=False, probed_at=_now(), **declared), False
    native = any(call.name == CANARY_TOOL_NAME for call in response.tool_calls or [])
    return ProviderCaps(native_tools=bool(native), probed_at=_now(), **declared), True


def _now() -> int:
    return int(time.time())


def _attach_caps(adapter: NarratorAdapter, caps: ProviderCaps) -> None:
    try:
        adapter.caps = caps  # type: ignore[attr-defined]
    except Exception:  # custom adapters without a caps slot are fine
        return


def _read_cache(store: Any, key: str) -> ProviderCaps | None:
    if store is None:
        return None
    try:
        row = store.find_one("provider_caps", {"provider_key": key})
    except Exception:  # a sick store must not break capability detection
        return None
    if not isinstance(row, dict):
        return None
    raw = row.get("caps")
    try:
        payload = json.loads(raw) if isinstance(raw, str) else dict(raw or {})
    except (TypeError, ValueError):
        return None
    if not isinstance(payload, dict):
        return None
    fields = {field.name for field in dataclasses.fields(ProviderCaps)}
    data = {key: value for key, value in payload.items() if key in fields}
    for flag in ("native_tools", "streaming", "json_mode"):
        if flag in data and not isinstance(data[flag], bool):
            return None  # corrupt/hand-edited row: re-probe instead of trusting it
    for number in ("probed_at", "context_window"):
        if number in data and data[number] is not None and not isinstance(data[number], int):
            return None
    caps = ProviderCaps(**data)
    if not caps.probed_at:
        caps.probed_at = as_int(row.get("probed_at"))
    return caps


def _write_cache(store: Any, key: str, caps: ProviderCaps) -> None:
    if store is None:
        return
    try:
        store.upsert(
            "provider_caps",
            {
                "provider_key": key,
                "caps": json.dumps(dataclasses.asdict(caps)),
                "probed_at": as_int(caps.probed_at),
            },
            conflict="provider_key",
        )
    except Exception:  # cache write failures must not fail a probe
        return
