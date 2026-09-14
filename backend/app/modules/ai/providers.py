"""AI provider abstraction: Provider protocol + deterministic StubProvider.

The pipeline only talks to the ``Provider`` protocol, so roles can start as
separate prompts on one model and be split across models later without
changing callers (t_9f1f24aa).

The story game is not playable without a connected model (owner ruling
2026-09-14, extended by P12): :class:`StubProvider` is INTERNAL TEST PLUMBING
— it is injected by tests and by engine/dev tooling, and no player surface may
resolve to it. ``app.modules.ai.settings_store.resolve`` is the one place that
decides "is a model connected?"; callers that get ``None`` refuse the turn
instead of narrating a placeholder.
"""
from __future__ import annotations

import hashlib
import json
import os
import urllib.error
import urllib.request
from dataclasses import dataclass, field
from typing import Any, Protocol
from urllib.parse import urlsplit


class ProviderError(RuntimeError):
    """Raised when an external AI provider call fails after retry."""


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


OPENCODE_SESSION_HEADER = "x-opencode-session"
# OpenCode's docs ask clients to identify themselves by name, not as a generic
# SDK/HTTP-library user agent (https://opencode.ai/docs/go/).
_OPENCODE_USER_AGENT = "lorebound-ai/0.1"


def _is_opencode_base(base_url: str) -> bool:
    """True when *base_url* is hosted on opencode.ai (their Zen/Go relay)."""
    host = (urlsplit(base_url).hostname or "").lower()
    return host == "opencode.ai" or host.endswith(".opencode.ai")


def _opencode_session_value(base_url: str, model: str, session_id: str = "") -> str:
    """Opaque, stable ``x-opencode-session`` value (routing + prompt-cache affinity).

    opencode.ai rejects requests without the header (``400 MissingSessionID``)
    and pins a conversation to one upstream backend by it, so the same inputs
    must always derive the same value.
    """
    basis = f"{base_url.rstrip('/')}|{model}|{session_id}"
    return "lorebound-" + hashlib.sha256(basis.encode("utf-8")).hexdigest()[:24]


class OpenAICompatibleProvider:
    """HTTP provider for any OpenAI-compatible chat completions endpoint.

    Stdlib ``urllib`` only — no new dependencies. POSTs to
    ``<base_url>/chat/completions`` with system + user messages.
    Retries once on timeout/HTTP error, then raises :class:`ProviderError`.

    Endpoints hosted on opencode.ai additionally send the relay's required
    ``x-opencode-session`` header, stable per base URL + model + session id.
    """

    model_name: str

    def __init__(self, base_url: str, model: str, api_key: str = "",
                 timeout_s: float = 30, session_id: str = "") -> None:
        self.base_url = base_url.rstrip("/")
        self.model_name = model
        self.api_key = api_key or ""
        self.timeout_s = timeout_s
        self.session_id = session_id or ""
        self._opencode_session = (
            _opencode_session_value(self.base_url, self.model_name, self.session_id)
            if _is_opencode_base(self.base_url) else ""
        )

    def generate(self, prompt: str, *, role: str = "narrator",
                 max_tokens: int = 600, **kwargs: Any) -> ProviderResult:
        payload = {
            "model": self.model_name,
            "messages": [
                {"role": "system", "content": f"You are the {role}."},
                {"role": "user", "content": prompt},
            ],
            "max_tokens": max_tokens,
            "temperature": 0.7,
        }
        body = json.dumps(payload).encode("utf-8")
        url = f"{self.base_url}/chat/completions"
        last_exc: Exception | None = None
        for _ in range(2):
            try:
                req = urllib.request.Request(
                    url,
                    data=body,
                    headers=self._headers(),
                    method="POST",
                )
                with urllib.request.urlopen(req, timeout=self.timeout_s) as resp:
                    data = json.loads(resp.read().decode("utf-8"))
                return self._to_result(data, role)
            except (TimeoutError, urllib.error.HTTPError, urllib.error.URLError) as exc:
                last_exc = exc
        raise ProviderError(f"OpenAI-compatible request failed: {last_exc}") from last_exc

    def _headers(self) -> dict[str, str]:
        headers = {"Content-Type": "application/json"}
        if self.api_key:
            headers["Authorization"] = f"Bearer {self.api_key}"
        if self._opencode_session:
            headers[OPENCODE_SESSION_HEADER] = self._opencode_session
            headers["User-Agent"] = _OPENCODE_USER_AGENT
        return headers

    def _to_result(self, data: dict[str, Any], role: str) -> ProviderResult:
        try:
            choices = data.get("choices", [])
            text = choices[0]["message"]["content"] if choices else ""
            usage = data.get("usage", {}) or {}
            prompt_tokens = int(usage.get("prompt_tokens", 0))
            completion_tokens = int(usage.get("completion_tokens", 0))
        except (KeyError, IndexError, TypeError, ValueError) as exc:
            raise ProviderError(f"Malformed provider response: {exc}") from exc
        return ProviderResult(
            text=text or "",
            model=self.model_name,
            prompt_tokens=prompt_tokens,
            completion_tokens=completion_tokens,
            metadata={"role": role},
        )


def get_provider() -> Provider:
    """Select a provider from the environment.

    - ``AI_PROVIDER`` unset/empty/``stub`` -> :class:`StubProvider`
      (test/dev plumbing — a player surface must never reach this branch;
      ``settings_store.env_connection()`` treats it as NOT CONNECTED)
    - ``AI_PROVIDER=openai-compatible`` -> :class:`OpenAICompatibleProvider`
      using ``OPENAI_COMPAT_BASE_URL`` (required),
      ``OPENAI_COMPAT_MODEL`` (required),
      ``OPENAI_COMPAT_API_KEY`` (optional),
      ``OPENAI_COMPAT_TIMEOUT_S`` (optional, default 30).
    - anything else raises :class:`ValueError`.
    """
    name = (os.environ.get("AI_PROVIDER") or "").strip().lower()
    if not name or name == "stub":
        return StubProvider()
    if name == "openai-compatible":
        base_url = (os.environ.get("OPENAI_COMPAT_BASE_URL") or "").strip()
        model = (os.environ.get("OPENAI_COMPAT_MODEL") or "").strip()
        if not base_url:
            raise ProviderError("OPENAI_COMPAT_BASE_URL is required")
        if not model:
            raise ProviderError("OPENAI_COMPAT_MODEL is required")
        api_key = os.environ.get("OPENAI_COMPAT_API_KEY", "") or ""
        timeout_raw = (os.environ.get("OPENAI_COMPAT_TIMEOUT_S") or "").strip()
        timeout_s: float = 30
        if timeout_raw:
            try:
                timeout_s = float(timeout_raw)
            except ValueError as exc:
                raise ProviderError(
                    f"Invalid OPENAI_COMPAT_TIMEOUT_S: {timeout_raw!r}") from exc
        return OpenAICompatibleProvider(
            base_url=base_url, model=model, api_key=api_key, timeout_s=timeout_s)
    raise ValueError(f"Unknown AI_PROVIDER: {name!r}")


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
