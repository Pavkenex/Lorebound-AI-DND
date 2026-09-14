"""FastAPI entrypoint. Routers are registered append-only by workstream owners."""
from __future__ import annotations

import importlib
from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from app.core.schema import ensure_schema


@asynccontextmanager
async def lifespan(_app: FastAPI):
    # A fresh SQLite file or compose Postgres has no tables yet; make the game
    # playable out of the box (create_all is idempotent; Alembic owns evolution).
    ensure_schema()
    yield


app = FastAPI(title="Lorebound", version="0.1.0", lifespan=lifespan)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


@app.get("/health")
def health() -> dict:
    return {"status": "ok", "service": "lorebound-backend"}


def include_optional(module: str) -> None:
    """Register a workstream router if the stream has landed.

    A stream that has not landed (or fails to import) must never block app
    boot; the missing surfaces simply 404 until the stream ships. The blind
    except is the boundary by design.
    """
    try:
        mod = importlib.import_module(module)
        router = getattr(mod, "router", None)
    except Exception:  # noqa: BLE001 - optional stream boundary, by design
        return
    if router is not None:
        app.include_router(router)


# Core streams: always present.
from app.modules.progression.router import router as progression_router

app.include_router(progression_router)
from app.modules.exploration.router import router as exploration_router

app.include_router(exploration_router)
from app.modules.economy.router import router as economy_router

app.include_router(economy_router)
from app.modules.history.router import router as history_router

app.include_router(history_router)
from app.modules.actions.router import router as actions_router

app.include_router(actions_router)
from app.modules.combat.router import router as combat_router

app.include_router(combat_router)

# Optional streams, one line each (append-only).
include_optional("app.modules.auth.router")  # Stream A: register / login / me
include_optional("app.modules.ai.router")  # bring-your-own AI provider settings
include_optional("app.content.router")  # Stream G: read-only content screens
include_optional("app.modules.campaign.saves_api")  # save slots (list/create/fetch)
include_optional("app.modules.campaign.api")  # campaign lifecycle (create/list)
include_optional("app.modules.play.router")  # live play: /act, /state, screens
include_optional("app.modules.engine.router")  # phase 2: /engine pilot (flag-gated)
