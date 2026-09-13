"""DELETE /saves/{save_id}: owner-scoped removal, 404 on missing/foreign.

Deleting a save only removes that snapshot from the shelf — the live campaign
state and all other saves stay untouched.
"""
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


def _mk_campaign(owner_id: str, name: str = "Saga") -> str:
    db = TestingSession()
    try:
        c = cm.Campaign(owner_user_id=owner_id, name=name, seed_key="custom")
        db.add(c)
        db.commit()
        db.refresh(c)
        return c.id
    finally:
        db.close()


def _auth(token: str) -> dict:
    return {"Authorization": f"Bearer {token}"}


def test_delete_removes_only_that_save(client: TestClient):
    _register(client, "deleter1@example.com")
    token = _login(client, "deleter1@example.com")
    db = TestingSession()
    try:
        uid = db.query(User).filter(User.email == "deleter1@example.com").first().id
    finally:
        db.close()
    cid = _mk_campaign(uid)

    keep = client.post(
        f"/campaigns/{cid}/saves", json={"label": "keep me"}, headers=_auth(token)
    ).json()
    drop = client.post(
        f"/campaigns/{cid}/saves", json={"label": "delete me"}, headers=_auth(token)
    ).json()

    r = client.delete(f"/saves/{drop['id']}", headers=_auth(token))
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["deleted"] is True
    assert body["save_id"] == drop["id"]
    assert body["campaign_id"] == cid

    shelf = client.get(f"/campaigns/{cid}/saves", headers=_auth(token)).json()
    assert [s["id"] for s in shelf] == [keep["id"]]

    # Gone for real: fetching or re-deleting it now 404s.
    assert client.get(f"/saves/{drop['id']}", headers=_auth(token)).status_code == 404
    assert client.delete(f"/saves/{drop['id']}", headers=_auth(token)).status_code == 404


def test_delete_missing_and_foreign_are_404(client: TestClient):
    a = _register(client, "deleter-owner@example.com")
    _register(client, "deleter-thief@example.com")
    ta = _login(client, "deleter-owner@example.com")
    tb = _login(client, "deleter-thief@example.com")
    cid = _mk_campaign(a["user"]["id"], name="Private Shelf")

    save = client.post(
        f"/campaigns/{cid}/saves", json={"label": "mine"}, headers=_auth(ta)
    ).json()

    # Foreign account: 404 — and the save still exists for its owner.
    r = client.delete(f"/saves/{save['id']}", headers=_auth(tb))
    assert r.status_code == 404, r.text
    assert client.get(f"/saves/{save['id']}", headers=_auth(ta)).status_code == 200

    # Unknown save: 404 for everyone.
    r = client.delete("/saves/does-not-exist", headers=_auth(ta))
    assert r.status_code == 404, r.text


def test_delete_requires_auth(client: TestClient):
    _register(client, "deleter3@example.com")
    token = _login(client, "deleter3@example.com")
    db = TestingSession()
    try:
        uid = db.query(User).filter(User.email == "deleter3@example.com").first().id
    finally:
        db.close()
    cid = _mk_campaign(uid)
    save = client.post(
        f"/campaigns/{cid}/saves", json={"label": "guarded"}, headers=_auth(token)
    ).json()

    r = client.delete(f"/saves/{save['id']}")
    assert r.status_code in (401, 403)
