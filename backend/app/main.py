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
