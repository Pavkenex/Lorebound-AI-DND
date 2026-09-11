"""Actions router (Stream B)."""
from __future__ import annotations

from fastapi import APIRouter

from app.modules.actions.pipeline import ActionInput, Pipeline
from app.modules.actions.suggest import SceneContext

router = APIRouter(prefix="/actions", tags=["actions"])
_pipeline = Pipeline()


@router.post("/submit")
def submit_action(action: ActionInput) -> dict:
    result = _pipeline.orchestrate(action)
    return result.model_dump()


@router.post("/suggest")
def suggest(scene: SceneContext) -> dict:
    from app.modules.actions.suggest import generate_suggestions
    buttons = generate_suggestions(scene)
    return {"suggestions": [b.model_dump() for b in buttons], "free_text_enabled": True}
