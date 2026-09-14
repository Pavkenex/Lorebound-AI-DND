"""HTTP plumbing shared by the wire adapters (spec §7).

One module owns the transport policy so the adapters only map wire <-> normalized
shapes:

- request construction via stdlib ``urllib.request`` (no third-party HTTP);
- timeout from ``cfg.timeout_s``;
- retry + exponential backoff for **retryable** failures only, bounded by
  ``cfg.max_retries`` (total attempts = ``max_retries + 1``);
- error normalization to :class:`~engine.providers.base.ProviderError`
  (HTTP 408/425/429 and 5xx retryable; any other 4xx is a caller/config error
  and is NOT retried; timeouts and connection errors are retryable);
- secret hygiene: error text is built from status + (trimmed) response body
  only, and every known secret is redacted before it can reach an exception
  message, a log line, or a traceback.

Retryable failures: HTTP 408/425/429 and 5xx, socket timeouts, connection
errors, and truncated HTTP responses (``http.client.HTTPException``).
"""
from __future__ import annotations

import json
import time
import urllib.error
import urllib.request
from collections.abc import Callable, Mapping, Sequence
from http.client import HTTPException
from typing import Any

from ..models import ChatRequest, ProviderCaps, ProviderConfig
from .base import ProviderError

RETRYABLE_STATUSES: frozenset[int] = frozenset({408, 425, 429})
"""Non-5xx statuses that are worth retrying (rate limit / explicit timeouts)."""

_MAX_ERROR_BODY = 300
"""Error-body characters kept in a normalized message (no full HTML dumps)."""


def redact(text: str, secrets: Sequence[str] = ()) -> str:
    """Replace every occurrence of each secret with ``***``."""
    out = text
    for secret in secrets:
        if secret:
            out = out.replace(secret, "***")
    return out


def as_int(value: Any, default: int = 0) -> int:
    """Lenient int coercion for vendor payloads (``None``/garbage -> default)."""
    try:
        return int(value)
    except (TypeError, ValueError):
        return default


def extract_error_message(body: str, *, limit: int = _MAX_ERROR_BODY) -> str:
    """Best-effort human-readable message out of a provider error body.

    Understands the shapes vendors actually use: ``{"error": {"message": …}}``,
    ``{"error": "…"}``, ``{"message": "…"}``; anything else is trimmed raw text.
    """
    if not body:
        return ""
    text = body.strip()
    try:
        parsed = json.loads(text)
    except ValueError:
        parsed = None
    if isinstance(parsed, dict):
        error = parsed.get("error")
        if isinstance(error, dict) and isinstance(error.get("message"), str):
            text = error["message"]
        elif isinstance(error, str):
            text = error
        elif isinstance(parsed.get("message"), str):
            text = parsed["message"]
    return text[:limit]


def _status_message(status: int, detail: str) -> str:
    return f"provider HTTP {status}: {detail}" if detail else f"provider HTTP {status}"


def _reason(exc: BaseException) -> str:
    if isinstance(exc, urllib.error.URLError) and not isinstance(exc, urllib.error.HTTPError):
        cause = exc.reason
        return cause if isinstance(cause, str) else f"{type(cause).__name__}: {cause}"
    return f"{type(exc).__name__}: {exc}"


def _read_error_body(exc: urllib.error.HTTPError) -> str:
    try:
        return exc.read().decode("utf-8", "replace")
    except (OSError, ValueError, HTTPException):
        return ""


def post_json(
    url: str,
    payload: Mapping[str, Any],
    *,
    headers: Mapping[str, str] | None = None,
    timeout_s: float = 60.0,
    max_retries: int = 0,
    backoff_base_s: float = 0.5,
    opener: Callable[..., Any] | None = None,
    secrets: Sequence[str] = (),
    sleeper: Callable[[float], None] = time.sleep,
) -> dict:
    """POST ``payload`` as JSON and decode the JSON-object response.

    Retries retryable failures up to ``max_retries`` times with exponential
    backoff (``backoff_base_s * 2**(attempt-1)``; ``0`` disables sleeping, which
    tests use to keep retry paths instant). Raises ``ProviderError`` — with
    ``retryable`` set so callers can decide whether to surface or re-attempt.
    """
    send = opener or urllib.request.urlopen
    request_headers = {"content-type": "application/json", **dict(headers or {})}
    body = json.dumps(payload).encode("utf-8")
    attempts = max(1, int(max_retries) + 1)
    attempt = 0
    while True:
        if attempt and backoff_base_s > 0:
            sleeper(float(backoff_base_s) * (2 ** (attempt - 1)))
        try:
            request = urllib.request.Request(url, data=body, headers=request_headers, method="POST")
            with send(request, timeout=timeout_s) as response:
                raw = response.read()
        except urllib.error.HTTPError as exc:  # subclass of URLError — catch first
            status = int(exc.code)
            detail = extract_error_message(_read_error_body(exc))
            failure = ProviderError(
                redact(_status_message(status, detail), secrets),
                retryable=status in RETRYABLE_STATUSES or status >= 500,
                status=status,
            )
        except (urllib.error.URLError, HTTPException, OSError) as exc:
            failure = ProviderError(
                redact(f"provider request failed: {_reason(exc)}", secrets),
                retryable=True,
            )
        else:
            try:
                data = json.loads(raw.decode("utf-8", "replace"))
            except ValueError:
                data = None
            if not isinstance(data, dict):
                raise ProviderError(redact("provider returned a non-JSON response body", secrets))
            return data
        if not failure.retryable or attempt >= attempts - 1:
            raise failure
        attempt += 1


class AdapterBase:
    """Shared adapter state: config, runtime key, declared caps, transport knobs.

    Subclasses implement ``complete()`` and the wire-specific hooks
    (``auth_headers``/``to_wire_*``). ``caps`` starts as the capabilities the
    wire family *declares* and is replaced by :func:`registry.probe_capabilities`
    with the probed verdict once a canary round trip has run.
    """

    default_base_url = ""
    """Used when ``cfg.base_url`` is empty."""

    def __init__(
        self,
        cfg: ProviderConfig,
        *,
        api_key: str | None = None,
        caps: ProviderCaps | None = None,
        opener: Callable[..., Any] | None = None,
        backoff_base_s: float = 0.5,
    ) -> None:
        self.cfg = cfg
        self.api_key = api_key or None
        self.name = cfg.name or "provider"
        self.caps = caps if caps is not None else self.declared_caps()
        self.opener = opener
        self.backoff_base_s = max(0.0, float(backoff_base_s))

    # -- capabilities ------------------------------------------------------ #
    def declared_caps(self) -> ProviderCaps:
        """Capabilities of the wire family before any probe.

        Every wire family here speaks native tool calling; whether the *model*
        behind it does is what the canary probe settles. The probe verdict
        replaces this via ``adapter.caps``.
        """
        return ProviderCaps(native_tools=True)

    def capabilities(self) -> ProviderCaps:
        return self.caps

    # -- wire plumbing ----------------------------------------------------- #
    @property
    def base_url(self) -> str:
        return (self.cfg.base_url or self.default_base_url).rstrip("/")

    def secrets(self) -> tuple[str, ...]:
        """Values that must never appear in error text/logs."""
        return (self.api_key,) if self.api_key else ()

    def model_for(self, request: ChatRequest) -> str:
        return request.model or self.cfg.model

    def extra_body(self) -> dict:
        """Provider-specific payload knobs (``cfg.extra['extra_body']``)."""
        raw = self.cfg.extra.get("extra_body")
        return dict(raw) if isinstance(raw, dict) else {}

    def auth_headers(self) -> dict:
        """Vendor auth headers; ``{}`` when no key was supplied (local servers)."""
        return {}

    def wire_headers(self) -> dict:
        """Auth headers, then ``cfg.extra['headers']`` (user config wins)."""
        headers = {str(k): str(v) for k, v in self.auth_headers().items()}
        raw = self.cfg.extra.get("headers")
        if isinstance(raw, dict):
            headers.update({str(k): str(v) for k, v in raw.items()})
        return headers

    def post(self, url: str, payload: Mapping[str, Any]) -> dict:
        return post_json(
            url,
            payload,
            headers=self.wire_headers(),
            timeout_s=self.cfg.timeout_s,
            max_retries=self.cfg.max_retries,
            backoff_base_s=self.backoff_base_s,
            opener=self.opener,
            secrets=self.secrets(),
        )

    def require_non_streaming(self, request: ChatRequest) -> None:
        if request.stream:
            raise ProviderError("streaming is not implemented by this adapter", retryable=False)
