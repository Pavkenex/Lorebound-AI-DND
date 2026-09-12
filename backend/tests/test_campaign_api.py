"""Campaign lifecycle API: create/list owner-scoped + play-state seeding."""
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
from app.modules.play.models import PlayStateRow

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


def _auth(client: TestClient, email: str) -> dict:
    r = client.post(
        "/auth/register",
        json={"email": email, "password": "password123", "display_name": email.split("@")[0]},
    )
    assert r.status_code == 201, r.text
    return {"Authorization": f"Bearer {r.json()['token']['access_token']}"}


def test_create_campaign_requires_auth(client: TestClient):
    assert client.post("/campaigns").status_code == 401
    assert client.get("/campaigns").status_code == 401


def test_create_list_and_seeded_play_state(client: TestClient):
    h = _auth(client, "owner@example.com")
    r = client.post("/campaigns", headers=h, json={"name": "A Road from Ravenford"})
    assert r.status_code == 201, r.text
    body = r.json()
    assert body["name"] == "A Road from Ravenford"
    assert body["seed_key"] == "hollow_crown"
    assert body["status"] == "active"

    # Play state is seeded at creation: opening pose at the Lantern Inn.
    db = TestingSession()
    try:
        row = db.get(PlayStateRow, body["id"])
        assert row is not None
        assert '"lantern-inn"' in row.state_json
        assert '"Kaelis Thorn"' in row.state_json
    finally:
        db.close()

    r = client.get("/campaigns", headers=h)
    assert r.status_code == 200
    rows = r.json()
    assert len(rows) == 1 and rows[0]["id"] == body["id"]


def test_default_name_and_unknown_seed(client: TestClient):
    h = _auth(client, "seedling@example.com")
    r = client.post("/campaigns", headers=h, json={})
    assert r.status_code == 201
    assert r.json()["name"] == "The Hollow Crown"

    bad = client.post("/campaigns", headers=h, json={"seed_key": "nope"})
    assert bad.status_code == 400


def test_campaigns_are_owner_scoped(client: TestClient):
    ha = _auth(client, "alpha@example.com")
    hb = _auth(client, "beta@example.com")
    made = client.post("/campaigns", headers=ha, json={"name": "Alpha's tale"}).json()
    rows_b = client.get("/campaigns", headers=hb).json()
    assert rows_b == []
    rows_a = client.get("/campaigns", headers=ha).json()
    assert [r["id"] for r in rows_a] == [made["id"]]
