"""Save/load round-trips the live play state (snapshot includes play block)."""
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


def _setup(client: TestClient, email: str) -> tuple[dict, str]:
    r = client.post(
        "/auth/register",
        json={"email": email, "password": "password123", "display_name": email.split("@")[0]},
    )
    headers = {"Authorization": f"Bearer {r.json()['token']['access_token']}"}
    c = client.post("/campaigns", headers=headers, json={})
    return headers, c.json()["id"]


def _act(client: TestClient, h: dict, text: str, seed: int = 15) -> dict:
    r = client.post("/act", headers=h, json={"text": text, "seed_roll": seed})
    assert r.status_code == 200, r.text
    return r.json()


def test_save_snapshot_includes_play_state(client: TestClient):
    h, cid = _setup(client, "snap@example.com")
    _act(client, h, "I steal from the strongbox")
    r = client.post(f"/campaigns/{cid}/saves", headers=h, json={"label": "Before the road"})
    assert r.status_code == 201, r.text
    save_id = r.json()["id"]
    got = client.get(f"/saves/{save_id}", headers=h).json()
    play = got["snapshot"]["play"]
    assert play["silver"] == 22
    assert play["marla_memory"] and "stole:storeroom-strongbox" in play["marla_memory"]


def test_load_restores_live_state(client: TestClient):
    h, cid = _setup(client, "roundtrip@example.com")
    _act(client, h, "I ask Marla about the travelers")
    _act(client, h, "I steal from the strongbox")
    save = client.post(f"/campaigns/{cid}/saves", headers=h, json={"label": "Mid-journey"}).json()

    # Mutate after saving: more silver, on the road, lead progressed.
    _act(client, h, "I step out and take the northern road")
    _act(client, h, "I study the wagon ruts")
    st = client.get("/state", headers=h).json()
    assert st["location"] == "The Northern Road"

    r = client.post(f"/saves/{save['id']}/load", headers=h)
    assert r.status_code == 200, r.text
    assert r.json()["loaded"] is True and r.json()["had_play_state"] is True

    st = client.get("/state", headers=h).json()
    assert st["location"] == "The Lantern Inn, Ravenford"  # saved position restored
    assert st["character"]["hp"]["cur"] == 32
    assert st["lead_stage"] == "rumored"  # post-save progress rolled back
    assert st["clues"] == []


def test_load_is_owner_scoped(client: TestClient):
    ha, cid = _setup(client, "owner-a@example.com")
    _act(client, ha, "I steal from the strongbox")
    save = client.post(f"/campaigns/{cid}/saves", headers=ha, json={"label": "Mine"}).json()

    rb = client.post(
        "/auth/register",
        json={"email": "owner-b@example.com", "password": "password123", "display_name": "B"},
    )
    hb = {"Authorization": f"Bearer {rb.json()['token']['access_token']}"}
    assert client.post(f"/saves/{save['id']}/load", headers=hb).status_code == 404
    assert client.post(f"/saves/{save['id']}/load", headers=hb).status_code != 200


def test_autosaves_from_beats_are_listed_and_loadable(client: TestClient):
    h, cid = _setup(client, "autos@example.com")
    _act(client, h, "I steal from the strongbox")  # inventory_change autosave
    rows = client.get(f"/campaigns/{cid}/saves", headers=h).json()
    autos = [r for r in rows if r["slot"] == "autosave"]
    assert autos, rows
    assert any(r["checkpoint"] == "inventory_change" for r in autos)
    r = client.post(f"/saves/{autos[0]['id']}/load", headers=h)
    assert r.status_code == 200 and r.json()["had_play_state"] is True
