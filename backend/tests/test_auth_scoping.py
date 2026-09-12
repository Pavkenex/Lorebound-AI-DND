"""Auth + campaign-ownership scoping (Stream A, t_44d10222).

- register/login/me flow
- bad credentials and bad tokens are rejected
- no endpoint can read another account's campaign (404, not 403)
"""
from __future__ import annotations

from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from app.core.database import Base, get_db
from app.main import app
from app.modules.auth.models import User  # noqa: F401  (register metadata)
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


app.dependency_overrides[get_db] = _override
client = TestClient(app, raise_server_exceptions=False)


def _register(email: str, password: str = "password123") -> dict:
    r = client.post(
        "/auth/register",
        json={"email": email, "password": password, "display_name": email.split("@")[0]},
    )
    assert r.status_code == 201, r.text
    return r.json()


def _login(email: str, password: str = "password123") -> str:
    r = client.post("/auth/login", data={"username": email, "password": password})
    assert r.status_code == 200, r.text
    return r.json()["access_token"]


def _mk_campaign(owner_id: str) -> str:
    db = TestingSession()
    try:
        c = cm.Campaign(owner_user_id=owner_id, name="Test Campaign", seed_key="custom")
        db.add(c)
        db.commit()
        db.refresh(c)
        return c.id
    finally:
        db.close()


def test_register_login_me():
    body = _register("alice@example.com")
    assert body["user"]["email"] == "alice@example.com"
    token = _login("alice@example.com")
    r = client.get("/auth/me", headers={"Authorization": f"Bearer {token}"})
    assert r.status_code == 200
    assert r.json()["email"] == "alice@example.com"


def test_duplicate_register_rejected():
    _register("bob@example.com")
    r = client.post(
        "/auth/register",
        json={"email": "bob@example.com", "password": "password123"},
    )
    assert r.status_code == 400


def test_bad_credentials_rejected():
    _register("carol@example.com")
    r = client.post("/auth/login", data={"username": "carol@example.com", "password": "wrongpass1"})
    assert r.status_code == 401
    r = client.get("/auth/me")
    assert r.status_code in (401, 403)
    r = client.get("/auth/me", headers={"Authorization": "Bearer garbage.token.here"})
    assert r.status_code == 401


def test_cross_account_campaign_is_404():
    a = _register("dave@example.com")
    b = _register("erin@example.com")
    ta = _login("dave@example.com")
    tb = _login("erin@example.com")
    cid = _mk_campaign(a["user"]["id"])
    assert b["user"]["id"] != a["user"]["id"]

    r = client.get(f"/auth/campaigns/{cid}", headers={"Authorization": f"Bearer {ta}"})
    assert r.status_code == 200
    assert r.json()["id"] == cid

    # Another account's campaign: 404 (must not leak existence).
    r = client.get(f"/auth/campaigns/{cid}", headers={"Authorization": f"Bearer {tb}"})
    assert r.status_code == 404
