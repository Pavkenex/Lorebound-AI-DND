"""AI metering headers on /act: calls, cost (stub-honest), cache on replay."""
from __future__ import annotations

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from app.core.database import Base, get_db
from app.main import app
from app.modules.auth.models import User  # noqa: F401  (register metadata)
from app.modules.campaign import models as cm  # noqa: F401
from app.modules.campaign import npc as _npc  # noqa: F401
from app.modules.campaign import story as _story  # noqa: F401
from app.modules.campaign import world as _world  # noqa: F401
from app.modules.character import models as _char  # noqa: F401
from app.modules.inventory import models as _inv  # noqa: F401
from app.modules.play import models as pm  # noqa: F401

pytestmark = pytest.mark.usefixtures("stub_play_provider")

engine = create_engine("sqlite://", connect_args={"check_same_thread": False}, poolclass=StaticPool)
TestingSession = sessionmaker(bind=engine, autoflush=False, autocommit=False)
Base.metadata.create_all(bind=engine)


def _override():
    db = TestingSession()
    try:
        yield db
    finally:
        db.close()


@pytest.fixture()
def client():
    previous = app.dependency_overrides.get(get_db)
    app.dependency_overrides[get_db] = _override
    try:
        yield TestClient(app, raise_server_exceptions=False)
    finally:
        if previous is None:
            app.dependency_overrides.pop(get_db, None)
        else:
            app.dependency_overrides[get_db] = previous


def _setup(client: TestClient, email: str) -> dict:
    r = client.post(
        "/auth/register",
        json={"email": email, "password": "password123", "display_name": email.split("@")[0]},
    )
    headers = {"Authorization": f"Bearer {r.json()['token']['access_token']}"}
    client.post("/campaigns", headers=headers, json={})
    # The chronicle opens on the prologue's road (§intro); these tests play
    # inside the tavern, so the player walks in first.
    w = client.post(
        "/act", headers=headers, json={"text": "I head down to the inn and step inside"}
    )
    assert w.status_code == 200, w.text
    return headers


def test_every_beat_counts_a_narration_call(client: TestClient):
    """P14: an authored beat no longer narrates itself — it calls the model."""
    h = _setup(client, "meter1@example.com")
    r = client.post("/act", headers=h, json={"text": "I ask Marla about the travelers"})
    assert r.status_code == 200
    assert r.headers["x-ai-calls"] == "1"  # the beat's prose is the model's now
    assert r.headers["x-ai-cost-usd"] == "0.000000"  # stub is free
    assert r.headers["x-cache"] == "miss"


def test_free_text_counts_a_narration_call(client: TestClient):
    h = _setup(client, "meter2@example.com")
    r = client.post("/act", headers=h, json={"text": "I hum a marching tune"})
    assert int(r.headers["x-ai-calls"]) >= 1  # pipeline narrated via stub
    assert r.headers["x-ai-cost-usd"] == "0.000000"


def test_replay_is_cache_hit_and_free(client: TestClient):
    h = _setup(client, "meter3@example.com")
    body = {"text": "I steal from the strongbox", "seed_roll": 15}
    first = client.post("/act", headers={**h, "Idempotency-Key": "meter-key"}, json=body)
    again = client.post("/act", headers={**h, "Idempotency-Key": "meter-key"}, json=body)
    assert first.headers["x-cache"] == "miss"
    assert again.headers["x-cache"] == "hit"
    assert again.headers["x-ai-calls"] == "0"
    assert again.headers["x-ai-cost-usd"] == "0.000000"


def test_cost_env_rates_move_the_needle(client: TestClient, monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setenv("AI_COST_PROMPT_PER_1K", "0.01")
    monkeypatch.setenv("AI_COST_COMPLETION_PER_1K", "0.02")
    h = _setup(client, "meter4@example.com")
    r = client.post("/act", headers=h, json={"text": "I hum a marching tune"})
    assert float(r.headers["x-ai-cost-usd"]) > 0
