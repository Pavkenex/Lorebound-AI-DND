"""FastAPI entrypoint. Routers are registered append-only by workstream owners."""
from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

app = FastAPI(title="Lorebound", version="0.1.0")

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


# Workstream routers register below (one line each, append-only).
# from app.modules.auth.router import router as auth_router
# app.include_router(auth_router)
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
try:  # Stream A: account auth (register/login/me).
    from app.modules.auth.router import router as auth_router

    app.include_router(auth_router)
except Exception:  # pragma: no cover - auth stream not landed yet
    pass
try:  # Stream G: read-only content screens (fixture/slice/Ravenford)
    from app.content.router import router as content_router

    if content_router is not None:
        app.include_router(content_router)
except Exception:  # content stream not landed yet; core app still boots
    pass
try:  # Saves API: campaign save slots (list/create/fetch).
    from app.modules.campaign.saves_api import router as saves_router

    app.include_router(saves_router)
except Exception:  # pragma: no cover - saves stream not landed yet
    pass
try:  # Live play: campaign lifecycle (create/list) + engine-backed play endpoints.
    from app.modules.campaign.api import router as campaigns_router

    app.include_router(campaigns_router)
except Exception:  # pragma: no cover - live stream not landed yet
    pass
try:  # Live play: POST /act (engine-backed actions).
    from app.modules.play.router import router as play_router

    app.include_router(play_router)
except Exception:  # pragma: no cover - live stream not landed yet
    pass
