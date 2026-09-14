"""Provider adapters (spec §7) — BYOK, multi-vendor, degrade gracefully.

Files:
- ``base.py``    — NarratorAdapter protocol (the one internal call signature)
- ``transport.py`` — HTTP policy: urllib, timeouts, retries, error
  normalization, secret redaction
- ``registry.py``— build_adapter() + capability probing with cache
- ``openai_compat.py`` / ``anthropic.py`` / ``gemini.py`` — wire adapters
- ``jsonproto.py`` — the degraded JSON-in-text protocol + the canonical
  Pass B tool-schema/codec used by BOTH native and degraded paths

Rules: stdlib-only (urllib.request); no secrets in logs; keys are runtime
inputs; retries/rate-limit handling normalized; tests use a fake HTTP server.
"""

from .base import NarratorAdapter, ProviderError
from .jsonproto import (
    PROPOSE_DELTAS_TOOL_NAME,
    build_json_protocol_prompt,
    delta_tool_schema,
    parse_tolerant,
    proposals_from_payload,
    proposals_from_tool_calls,
    regenerate_note,
    should_regenerate,
)
from .registry import build_adapter, probe_capabilities

__all__ = [
    "PROPOSE_DELTAS_TOOL_NAME",
    "NarratorAdapter",
    "ProviderError",
    "build_adapter",
    "build_json_protocol_prompt",
    "delta_tool_schema",
    "parse_tolerant",
    "probe_capabilities",
    "proposals_from_payload",
    "proposals_from_tool_calls",
    "regenerate_note",
    "should_regenerate",
]
