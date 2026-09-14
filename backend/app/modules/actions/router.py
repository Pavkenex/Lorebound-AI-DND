"""Actions router (Stream B)."""
from __future__ import annotations

from fastapi import APIRouter, HTTPException, status

from app.modules.actions.pipeline import ActionInput, Pipeline
from app.modules.actions.suggest import SceneContext
from app.modules.ai.settings_store import env_connection

router = APIRouter(prefix="/actions", tags=["actions"])


@router.post("/submit")
def submit_action(action: ActionInput) -> dict:
    """Run one pipeline turn on the environment's provider.

    This surface has no account, so the environment configuration is the only
    connection it can use — and ``/actions`` never narrates with the built-in
    stub (P12): an unset/``stub``/misconfigured environment answers the same
    ``400 {"detail": "connect_your_ai"}`` every other player path does.
    """
    conn = env_connection()
    if not conn.connected:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST, detail="connect_your_ai"
        )
    result = Pipeline(provider=conn.provider).orchestrate(action)
    return result.model_dump()


@router.post("/suggest")
def suggest(scene: SceneContext) -> dict:
    from app.modules.actions.suggest import generate_suggestions
    buttons = generate_suggestions(scene)
    return {"suggestions": [b.model_dump() for b in buttons], "free_text_enabled": True}
