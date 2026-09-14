"""Provider adapters (spec §7) — BYOK, multi-vendor, degrade gracefully.

Files:
- ``base.py``    — NarratorAdapter protocol (the one internal call signature)
- ``registry.py``— build_adapter() + capability probing with cache
- ``openai_compat.py`` / ``anthropic.py`` / ``gemini.py`` — wire adapters
- ``jsonproto.py`` — the degraded JSON-in-text protocol + the canonical
  Pass B tool-schema/codec used by BOTH native and degraded paths

Rules: stdlib-only (urllib.request); no secrets in logs; keys are runtime
inputs; retries/rate-limit handling normalized; tests use a fake HTTP server.
"""

from .base import NarratorAdapter
from .registry import build_adapter, probe_capabilities

__all__ = ["NarratorAdapter", "build_adapter", "probe_capabilities"]
