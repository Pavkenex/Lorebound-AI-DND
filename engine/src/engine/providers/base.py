"""The one internal provider call signature (spec §7).

Every backend (OpenAI-compatible, Anthropic, Gemini, local servers) is adapted
to this surface; the orchestrator only ever sees ``ChatRequest`` in,
``ChatResponse`` out. Wire differences (tools schemas, system prompts,
streaming frames, error shapes) are the adapter's problem.
"""
from __future__ import annotations

from typing import Protocol

from ..models import ChatRequest, ChatResponse, ProviderCaps


class NarratorAdapter(Protocol):
    name: str

    def capabilities(self) -> ProviderCaps:
        """Declared/probed capabilities (native_tools etc.)."""
        ...

    def complete(self, request: ChatRequest) -> ChatResponse:
        """One round-trip. Raises normalized ProviderError subclasses on failure."""
        ...


class ProviderError(Exception):
    """Normalized provider failure. ``retryable`` guides the retry policy."""

    def __init__(self, message: str, *, retryable: bool = False, status: int | None = None) -> None:
        super().__init__(message)
        self.retryable = retryable
        self.status = status
