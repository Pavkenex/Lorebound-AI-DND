"""Adapter construction + capability detection (spec §7).

- ``build_adapter(cfg, api_key=...)`` returns the right wire adapter for
  ``cfg.api_mode``. The key is a runtime input — never persisted.
- ``probe_capabilities(...)`` runs a ONCE-per key/model canary call (a tiny
  tool-call round trip) and caches the result in the ``provider_caps`` table
  when a store is given. When native tool calls are unreliable/unsupported,
  adapters report ``native_tools=False`` and the pipeline degrades to the
  JSON-in-text protocol (``jsonproto``).
"""
from __future__ import annotations

from typing import Any, Optional

from ..models import ProviderCaps, ProviderConfig
from .base import NarratorAdapter


def build_adapter(cfg: ProviderConfig, *, api_key: str | None = None) -> NarratorAdapter:
    raise NotImplementedError("R4 card implements build_adapter")


def probe_capabilities(adapter: NarratorAdapter, cfg: ProviderConfig, *,
                       store: Any = None, force: bool = False) -> ProviderCaps:
    """Canary-probe and (optionally) cache in ``provider_caps``. Never raises —
    on probe failure returns conservative defaults (native_tools=False)."""
    raise NotImplementedError("R4 card implements probe_capabilities")


def cache_key(cfg: ProviderConfig) -> str:
    """Stable cache key: provider + model + base_url (NO key material)."""
    return f"{cfg.name}|{cfg.model}|{cfg.base_url}|{cfg.api_mode}"


_ = Optional  # noqa: B018  (placeholder for the implementing card)
