"""AI provider abstraction: Provider protocol + deterministic StubProvider.

The pipeline only talks to the ``Provider`` protocol, so roles can start as
separate prompts on one model and be split across models later without
changing callers (t_9f1f24aa).
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Protocol


@dataclass
class ProviderResult:
    text: str
    model: str = "stub"
    prompt_tokens: int = 0
    completion_tokens: int = 0
    metadata: dict[str, Any] = field(default_factory=dict)


class Provider(Protocol):
    """Anything that can turn a prompt into text."""

    model_name: str

    def generate(self, prompt: str, *, role: str = "narrator",
                 max_tokens: int = 600, **kwargs: Any) -> ProviderResult:
        ...


class StubProvider:
    """Deterministic provider for tests/dev: no network, no cost, no RNG.

    Returns canned, role-appropriate text that echoes key mechanics markers
    (``[OUTCOME:...]``) so validator/authority paths can be exercised.
    """

    model_name = "stub-deterministic"

    def __init__(self, canned: dict[str, str] | None = None) -> None:
        self.canned = canned or {}
        self.calls: list[dict[str, Any]] = []

    def generate(self, prompt: str, *, role: str = "narrator",
                 max_tokens: int = 600, **kwargs: Any) -> ProviderResult:
        self.calls.append({"role": role, "prompt_chars": len(prompt), "max_tokens": max_tokens})
        if role in self.canned:
            text = self.canned[role]
        else:
            text = _default_canned(role, prompt)
        return ProviderResult(text=text, model=self.model_name,
                              prompt_tokens=len(prompt) // 4,
                              completion_tokens=len(text) // 4,
                              metadata={"role": role, "stub": True})


def _default_canned(role: str, prompt: str) -> str:
    first_line = next((ln.strip() for ln in prompt.splitlines() if ln.strip()), "the scene")
    if role == "interpreter":
        return '{"kind": "other", "summary": "Act", "checks": [], "risk": "low"}'
    if role == "actor":
        return f"In character, reacting to {first_line[:80]}."
    if role == "director":
        return "No intervention: let the scene play out."
    if role == "summarizer":
        return f"Summary: events concerning {first_line[:80]}."
    if role == "generator":
        return "Generated content seed: a weathered notice board with three postings."
    # narrator default: well-formed prose, no factual claims, no rewards.
    return (
        "The room holds its breath for a moment. Whatever was attempted plays out "
        "as the dice decree — nothing more appears than was already here, and no one "
        "present changes their nature on your word alone. The moment passes, and the "
        "choice of what to do next is yours."
    )
