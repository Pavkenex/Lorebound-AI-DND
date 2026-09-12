"""GET /state: live game-state payload for the adventure screen."""
from __future__ import annotations

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from app.core.database import Base, get_db
from app.main import app
from app.modules.auth.models import User
from app.modules.campaign import models as cm
from app.modules.campaign import npc as _npc  # noqa: F401
from app.modules.campaign import story as _story  # noqa: F401
from app.modules.campaign import world as _world  # noqa: F401
from app.modules.character import models as _char  # noqa: F401
from app.modules.inventory import models as _inv  # noqa: F401
from app.modules.play import models as pm  # noqa: F401

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


def _setup(client: TestClient, email: str):
    r = client.post(
        "/auth/register",
        json={"email": email, "password": "password123", "display_name": email.split("@")[0]},
    )
    headers = {"Authorization": f"Bearer {r.json()['token']['access_token']}"}
    c = client.post("/campaigns", headers=headers, json={})
    assert c.status_code == 201, c.text
    return headers, c.json()["id"]


def test_state_requires_auth(client: TestClient):
    assert client.get("/state").status_code == 401


def test_fresh_campaign_state_shape(client: TestClient):
    h, _cid = _setup(client, "shape@example.com")
    r = client.get("/state", headers=h)
    assert r.status_code == 200, r.text
    s = r.json()
    assert s["location"] == "The Lantern Inn, Ravenford"
    assert s["time"] == "Day 1 · 19:00 · rain"
    assert [n["name"] for n in s["npcs"]] == ["Marla Voss", "Borin"]
    assert s["leads"] == []
    assert "hearth" in s["interactables"]
    assert s["party"][0]["name"] == "Kaelis"
    assert s["party"][0]["hp"] == "32/32"
    assert len(s["feed"]) == 2
    assert s["feed"][0]["kind"] == "narration"
    assert s["character"]["name"] == "Kaelis Thorn"
    assert s["character"]["hp"] == {"cur": 32, "max": 32}
    assert s["completed"] is False


def test_state_reflects_beats(client: TestClient):
    h, _cid = _setup(client, "beats@example.com")
    client.post("/act", headers=h, json={"text": "I ask Marla about the travelers"})
    s = client.get("/state", headers=h).json()
    assert s["leads"] == ["Missing Travelers"]

    client.post("/act", headers=h, json={"text": "I steal the strongbox", "seed_roll": 15})
    s = client.get("/state", headers=h).json()
    assert s["character"]["hp"]["cur"] == 32  # theft left no wound
    s_after_leave = client.post(
        "/act", headers=h, json={"text": "I step out and take the northern road"}
    )
    assert s_after_leave.status_code == 200
    s = client.get("/state", headers=h).json()
    assert s["location"] == "The Northern Road"
    assert s["npcs"] == []
    assert "wagon ruts" in s["interactables"]
    assert s["time"] == "Day 1 · 19:30 · rain"  # talk 5 + steal 15 + leave 10 = 30m
    assert s["feed"][-1]["kind"] in ("narration", "system")


def test_state_works_for_campaign_without_play_row(client: TestClient):
    """Campaigns that predate the live layer render a seeded (unpersisted) view."""
    h, _cid = _setup(client, "legacy@example.com")
    s = client.get("/state", headers=h).json()
    assert s["location"] == "The Lantern Inn, Ravenford"
    # And a forked campaign row created directly in the DB:
    db = TestingSession()
    try:
        user = db.query(User).filter(User.email == "legacy@example.com").first()
        c = cm.Campaign(owner_user_id=user.id, name="Old saga", seed_key="custom")
        db.add(c)
        db.commit()
        db.refresh(c)
        cid2 = c.id
    finally:
        db.close()
    s2 = client.get(f"/state?campaign_id={cid2}", headers=h)
    assert s2.status_code == 200, s2.text
    assert s2.json()["location"] == "The Lantern Inn, Ravenford"
