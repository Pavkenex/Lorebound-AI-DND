"""AI provider settings API: GET/PUT /ai/settings + POST /ai/settings/test.

Bring-your-own OpenAI-compatible endpoint, per user. The key is write-only:
it can be set or cleared, never read back. ``/ai/settings/test`` lets the
client verify a configuration (draft or saved) before relying on it.

The panel never lies about play (P12): ``connected``/``active_*`` in the GET
doc are the SAME resolution ``/act`` uses (``settings_store.resolve``), so a
saved-but-incomplete endpoint or a retired ``"stub"`` row reads as not
connected instead of claiming a provider that will not narrate.
"""
from __future__ import annotations

import time
from typing import Any

from fastapi import APIRouter, Depends, HTTPException, status
from pydantic import BaseModel, Field
from sqlalchemy.orm import Session

from app.core.database import get_db
from app.modules.ai import settings_store as store
from app.modules.ai.providers import OpenAICompatibleProvider, ProviderError
from app.modules.auth.deps import require_user
from app.modules.auth.models import User

router = APIRouter(prefix="/ai", tags=["ai"])

TEST_PROMPT = "Connection test. Reply with the single word: pong."


class SettingsIn(BaseModel):
    """PUT body. ``api_key=None`` keeps the stored key; ``clear_key`` drops it.

    ``provider`` is validated against the offered set in the handler (not a
    Literal) so a retired value — ``"stub"`` — answers the 400 contract with a
    readable message instead of a pydantic 422.
    """

    provider: str = Field(max_length=32)
    base_url: str = Field(default="", max_length=500)
    model: str = Field(default="", max_length=200)
    api_key: str | None = Field(default=None, max_length=500)
    clear_key: bool = False
    timeout_s: int = Field(default=store.DEFAULT_TIMEOUT_S, ge=1, le=120)


class TestIn(BaseModel):
    """Optional overrides so a draft can be tested before saving."""

    base_url: str | None = Field(default=None, max_length=500)
    model: str | None = Field(default=None, max_length=200)
    api_key: str | None = Field(default=None, max_length=500)
    timeout_s: int | None = Field(default=None, ge=1, le=120)


def _require_http_url(raw: str) -> str:
    url = store.normalize_base_url(raw)
    if not url.startswith(("http://", "https://")):
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="base_url must start with http:// or https://",
        )
    return url


@router.get("/settings")
def get_settings(user: User = Depends(require_user), db: Session = Depends(get_db)) -> dict[str, Any]:
    return store.settings_doc(db, user.id)


@router.put("/settings")
def put_settings(
    body: SettingsIn,
    user: User = Depends(require_user),
    db: Session = Depends(get_db),
) -> dict[str, Any]:
    mode = (body.provider or "").strip().lower()
    if mode not in store.PROVIDERS:
        # P12 (owner ruling extended to the story game): the built-in
        # storyteller is not a play mode — saving it is not offered and not
        # accepted. Legacy rows carrying it read as NOT CONNECTED.
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=(
                f"provider {mode!r} is not available; "
                f"supported: {', '.join(store.PROVIDERS)}"
            ),
        )
    if mode == "openai-compatible":
        base_url = _require_http_url(body.base_url)
        if not body.model.strip():
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="model is required for an OpenAI-compatible provider",
            )
    else:
        base_url = store.normalize_base_url(body.base_url)
    store.save_settings(
        db,
        user.id,
        provider=mode,
        base_url=base_url,
        model=body.model,
        api_key=body.api_key,
        clear_key=body.clear_key,
        timeout_s=body.timeout_s,
    )
    return store.settings_doc(db, user.id)


@router.post("/settings/test")
def test_settings(
    body: TestIn,
    user: User = Depends(require_user),
    db: Session = Depends(get_db),
) -> dict[str, Any]:
    """One bounded chat-completions call against the effective configuration.

    Body fields override saved settings, so a draft can be verified before
    it is saved. Always 200: the outcome rides in ``ok``/``error``.
    """
    row = store.load_row(db, user.id)
    saved_url = row.base_url if row is not None else ""
    saved_model = row.model if row is not None else ""
    saved_key = row.api_key if row is not None else ""
    saved_timeout = row.timeout_s if row is not None else store.DEFAULT_TIMEOUT_S

    base_url = store.normalize_base_url(body.base_url if body.base_url is not None else saved_url)
    model = (body.model if body.model is not None else saved_model).strip()
    timeout_s = int(body.timeout_s if body.timeout_s is not None else saved_timeout)
    # A blank override means "no key on this draft"; None keeps the saved key.
    api_key = saved_key if body.api_key is None else body.api_key

    if not base_url or not model:
        return {
            "ok": False,
            "error": "Set a base URL and a model first — nothing to test yet.",
        }

    provider = OpenAICompatibleProvider(
        base_url=_require_http_url(base_url), model=model, api_key=api_key, timeout_s=timeout_s,
        session_id=user.id,
    )
    started = time.perf_counter()
    try:
        # Reasoning-style models spend output budget on hidden reasoning first;
        # leave enough room that a short "pong" still comes back as text.
        result = provider.generate(TEST_PROMPT, role="narrator", max_tokens=256)
    except ProviderError as exc:
        return {"ok": False, "error": str(exc)[:300], "latency_ms": int((time.perf_counter() - started) * 1000)}
    latency_ms = int((time.perf_counter() - started) * 1000)
    return {
        "ok": True,
        "model": result.model,
        "latency_ms": latency_ms,
        "reply": (result.text or "").strip()[:120],
    }
