"""Saves API: list / create / fetch with ownership scoping (404 on foreign)."""
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
    """Scoped get_db override: set for our tests, restore after.

    Other suites (e.g. test_auth_scoping) install their own global
    override at import time; a module-level override here would clobber
    theirs when the full suite runs, so we never touch it at import.
    """
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


def test_saves_roundtrip_list_create_fetch(client: TestClient):
    _register(client, "saver1@example.com")
    token = _login(client, "saver1@example.com")
    db = TestingSession()
    try:
        user = db.query(User).filter(User.email == "saver1@example.com").first()
        uid = user.id
    finally:
        db.close()
    cid = _mk_campaign(uid)

    r = client.get(f"/campaigns/{cid}/saves", headers=_auth(token))
    assert r.status_code == 200, r.text
    assert r.json() == []

    r = client.post(
        f"/campaigns/{cid}/saves",
        json={"label": "before the storm"},
        headers=_auth(token),
    )
    assert r.status_code == 201, r.text
    created = r.json()
    assert created["label"] == "before the storm"
    assert created["slot"] == "manual"
    assert created["checkpoint"] == "manual"
    save_id = created["id"]

    r = client.get(f"/campaigns/{cid}/saves", headers=_auth(token))
    assert r.status_code == 200, r.text
    items = r.json()
    assert len(items) == 1
    item = items[0]
    for key in ("id", "slot", "label", "checkpoint", "created_at"):
        assert key in item, key
    assert item["id"] == save_id

    r = client.get(f"/saves/{save_id}", headers=_auth(token))
    assert r.status_code == 200, r.text
    fetched = r.json()
    assert fetched["id"] == save_id
    assert fetched["campaign_id"] == cid
    assert fetched["slot"] == "manual"
    assert fetched["label"] == "before the storm"
    assert fetched["checkpoint"] == "manual"
    assert isinstance(fetched["snapshot"], dict)
    assert fetched["snapshot"]["campaign_id"] == cid
    assert "created_at" in fetched


def test_saves_create_defaults(client: TestClient):
    _register(client, "saver2@example.com")
    token = _login(client, "saver2@example.com")
    db = TestingSession()
    try:
        uid = db.query(User).filter(User.email == "saver2@example.com").first().id
    finally:
        db.close()
    cid = _mk_campaign(uid, name="Defaults Saga")

    r = client.post(f"/campaigns/{cid}/saves", json={}, headers=_auth(token))
    assert r.status_code == 201, r.text
    body = r.json()
    assert body["slot"] == "manual"
    assert body["checkpoint"] == "manual"


def test_saves_cross_account_is_404(client: TestClient):
    a = _register(client, "owner-a@example.com")
    _register(client, "owner-b@example.com")
    ta = _login(client, "owner-a@example.com")
    tb = _login(client, "owner-b@example.com")
    cid = _mk_campaign(a["user"]["id"], name="Private Saga")

    r = client.post(
        f"/campaigns/{cid}/saves", json={"label": "mine"}, headers=_auth(ta)
    )
    assert r.status_code == 201, r.text
    save_id = r.json()["id"]

    # Foreign account: 404 everywhere (must not leak existence).
    r = client.get(f"/campaigns/{cid}/saves", headers=_auth(tb))
    assert r.status_code == 404, r.text
    r = client.post(
        f"/campaigns/{cid}/saves", json={"label": "theft"}, headers=_auth(tb)
    )
    assert r.status_code == 404, r.text
    r = client.get(f"/saves/{save_id}", headers=_auth(tb))
    assert r.status_code == 404, r.text

    # Owner still sees everything.
    r = client.get(f"/saves/{save_id}", headers=_auth(ta))
    assert r.status_code == 200, r.text


def test_saves_require_auth_and_404_missing(client: TestClient):
    _register(client, "saver3@example.com")
    token = _login(client, "saver3@example.com")

    r = client.get("/campaigns/does-not-exist/saves")
    assert r.status_code in (401, 403)
    r = client.get("/saves/does-not-exist", headers=_auth(token))
    assert r.status_code == 404, r.text
