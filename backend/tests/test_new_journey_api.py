"""New Journey (campaign restart): checkpoint the old run, reset state + clock.

Covers the menu's "New Journey" semantics: an in-progress run is snapshotted
onto the save shelf ("Before the new road") before the live state resets to
the opening scene; an untouched campaign just resets; ownership stays scoped.
"""
from __future__ import annotations

import json

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from app.core.database import Base, get_db
from app.main import app
from app.modules.campaign import models as cm
from app.modules.campaign import npc as _npc  # noqa: F401
from app.modules.campaign import story as _story  # noqa: F401
from app.modules.campaign import world as _world  # noqa: F401
from app.modules.character import models as _char  # noqa: F401
from app.modules.inventory import models as _inv  # noqa: F401
from app.modules.play.models import PlayActionRow, PlayStateRow

engine = create_engine(
    "sqlite://", connect_args={"check_same_thread": False}, poolclass=StaticPool
)
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
    """Scoped get_db override: set for our tests, restore after (see test_saves_api)."""
    previous = app.dependency_overrides.get(get_db)
    app.dependency_overrides[get_db] = _override
    try:
        yield TestClient(app, raise_server_exceptions=False)
    finally:
        if previous is None:
            app.dependency_overrides.pop(get_db, None)
        else:
            app.dependency_overrides[get_db] = previous


def _register(client: TestClient, email: str, password: str = "password123") -> dict:
    r = client.post(
        "/auth/register",
        json={"email": email, "password": password, "display_name": email.split("@")[0]},
    )
    assert r.status_code == 201, r.text
    return r.json()


def _login(client: TestClient, email: str, password: str = "password123") -> str:
    r = client.post("/auth/login", data={"username": email, "password": password})
    assert r.status_code == 200, r.text
    return r.json()["access_token"]


def _auth(token: str) -> dict:
    return {"Authorization": f"Bearer {token}"}


def _create_campaign(client: TestClient, token: str, name: str = "New Journey Saga") -> str:
    r = client.post("/campaigns", json={"name": name}, headers=_auth(token))
    assert r.status_code == 201, r.text
    return r.json()["id"]


def test_restart_checkpoints_and_resets(client: TestClient):
    _register(client, "restart1@example.com")
    token = _login(client, "restart1@example.com")
    cid = _create_campaign(client, token)

    # Give the run visible progress: actions taken, a marker in the feed row,
    # the clock moved from its default, and a pending idempotency key.
    db = TestingSession()
    try:
        row = db.get(PlayStateRow, cid)
        assert row is not None, "POST /campaigns must seed the live play state"
        state = json.loads(row.state_json)
        state["actions_taken"] = 7
        feed = state.setdefault("feed", [])
        feed.insert(0, {"kind": "system", "text": "t_restart_marker"})
        row.state_json = json.dumps(state)
        clock = db.query(cm.GameTime).filter(cm.GameTime.campaign_id == cid).first()
        clock.day, clock.hour, clock.minute = 4, 19, 30
        db.add(PlayActionRow(campaign_id=cid, idempotency_key="old-key", response_json="{}"))
        db.commit()
    finally:
        db.close()

    r = client.post(f"/campaigns/{cid}/restart", headers=_auth(token))
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["restarted"] is True
    assert body["had_progress"] is True
    assert body["checkpoint_save_id"], "an in-progress run must be snapshotted"

    # The old run sits on the shelf as an autosave named for the moment.
    r = client.get(f"/campaigns/{cid}/saves", headers=_auth(token))
    shelf = r.json()
    assert len(shelf) == 1
    assert shelf[0]["id"] == body["checkpoint_save_id"]
    assert shelf[0]["slot"] == "autosave"
    assert shelf[0]["label"] == "Before the new road"
    assert shelf[0]["checkpoint"] == "new_journey"

    # Live state, clock, idempotency rows: all back to the opening.
    db = TestingSession()
    try:
        fresh = json.loads(db.get(PlayStateRow, cid).state_json)
        assert fresh["actions_taken"] == 0
        assert "t_restart_marker" not in json.dumps(fresh)
        clock = db.query(cm.GameTime).filter(cm.GameTime.campaign_id == cid).first()
        assert (clock.day, clock.hour, clock.minute) == (1, 8, 0)
        assert db.query(PlayActionRow).filter(PlayActionRow.campaign_id == cid).count() == 0
    finally:
        db.close()


def test_restart_fresh_campaign_writes_no_snapshot(client: TestClient):
    _register(client, "restart2@example.com")
    token = _login(client, "restart2@example.com")
    cid = _create_campaign(client, token)

    r = client.post(f"/campaigns/{cid}/restart", headers=_auth(token))
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["restarted"] is True
    assert body["had_progress"] is False
    assert body["checkpoint_save_id"] is None

    shelf = client.get(f"/campaigns/{cid}/saves", headers=_auth(token)).json()
    assert shelf == []


def test_restart_reactivates_a_completed_campaign(client: TestClient):
    _register(client, "restart3@example.com")
    token = _login(client, "restart3@example.com")
    cid = _create_campaign(client, token)

    db = TestingSession()
    try:
        camp = db.get(cm.Campaign, cid)
        camp.status = "completed"
        db.commit()
    finally:
        db.close()

    r = client.post(f"/campaigns/{cid}/restart", headers=_auth(token))
    assert r.status_code == 200, r.text

    rows = client.get("/campaigns", headers=_auth(token)).json()
    mine = [row for row in rows if row["id"] == cid]
    assert mine and mine[0]["status"] == "active"


def test_restart_is_owner_scoped(client: TestClient):
    a = _register(client, "restart-owner@example.com")
    _register(client, "restart-thief@example.com")
    ta = _login(client, "restart-owner@example.com")
    tb = _login(client, "restart-thief@example.com")
    cid = _create_campaign(client, ta, name="Owner Saga")
    assert a["user"]["id"]

    # Foreign account: 404 (must not leak existence).
    r = client.post(f"/campaigns/{cid}/restart", headers=_auth(tb))
    assert r.status_code == 404, r.text

    # Unknown campaign: 404 for everyone.
    r = client.post("/campaigns/does-not-exist/restart", headers=_auth(ta))
    assert r.status_code == 404, r.text

    # Missing auth: rejected outright.
    r = client.post(f"/campaigns/{cid}/restart")
    assert r.status_code in (401, 403)
