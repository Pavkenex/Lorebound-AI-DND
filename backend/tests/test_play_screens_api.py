"""Live screens: character / skills / journal / map / inventory / companions."""
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


def _act(client: TestClient, h: dict, text: str, seed: int = 18) -> dict:
    r = client.post("/act", headers=h, json={"text": text, "seed_roll": seed})
    assert r.status_code == 200, r.text
    return r.json()


SCREENS = ("/character", "/skills", "/journal", "/map", "/inventory", "/companions")


def test_screens_require_auth(client: TestClient):
    for path in SCREENS:
        assert client.get(path).status_code == 401, path


def test_character_screen_is_live(client: TestClient):
    h, _cid = _setup(client, "char@example.com")
    c = client.get("/character", headers=h).json()
    assert c["name"] == "Kaelis Thorn"
    assert c["epithet"] == "the Lantern-Bearer"
    assert set(c["attributes"]) == {"Might", "Finesse", "Wits", "Resolve", "Presence"}
    assert c["hp"] == {"cur": 32, "max": 32}
    assert c["achievements"] == []


def test_skills_screen_earns_mastery_from_play(client: TestClient):
    h, _cid = _setup(client, "skills@example.com")
    fresh = client.get("/skills", headers=h).json()
    names = [s["name"] for s in fresh["list"]]
    assert names == ["Persuasion", "Intimidation", "Investigation", "Stealth", "Swordsmanship"]
    assert all(s["xp"] == 0 and s["tier"] == "Untrained" for s in fresh["list"])

    _act(client, h, "I ask Marla about the travelers")
    _act(client, h, "I examine the guest ledger")  # Investigation success
    got = client.get("/skills", headers=h).json()
    inv = next(s for s in got["list"] if s["name"] == "Investigation")
    assert inv["xp"] == 20
    assert inv["recent"] and "Investigation" in inv["recent"][0]


def test_journal_reveals_with_progress(client: TestClient):
    h, _cid = _setup(client, "journal@example.com")
    j0 = client.get("/journal", headers=h).json()
    assert j0["nodes"] == []
    assert "driverless wagon" in j0["detail"]

    _act(client, h, "I ask Marla about the travelers")
    j1 = client.get("/journal", headers=h).json()
    assert [n["id"] for n in j1["nodes"]] == ["missing-caravan"]
    assert "thread starts here" in j1["detail"]

    _act(client, h, "I examine the guest ledger")
    j2 = client.get("/journal", headers=h).json()
    ids = {n["id"] for n in j2["nodes"]}
    assert {"missing-caravan", "silver-powder", "merchant-guild"} <= ids
    assert any(e == ["silver-powder", "merchant-guild"] for e in j2["edges"])


def test_map_knowledge_grows_with_travel(client: TestClient):
    h, _cid = _setup(client, "map@example.com")
    m0 = client.get("/map", headers=h).json()
    names = [p["name"] for p in m0["places"]]
    assert any("Lantern Inn" in n for n in names)
    assert not any("Northern Road" in n for n in names)
    assert m0["incidents"] and "Active" in m0["incidents"][0]

    _act(client, h, "I step out into the rain and take the northern road")
    m1 = client.get("/map", headers=h).json()
    names = [p["name"] for p in m1["places"]]
    assert any("Northern Road" in n for n in names)
    assert m1["roads"] and m1["rumours"]


def test_inventory_shows_purse_and_keepsakes(client: TestClient):
    h, _cid = _setup(client, "inv@example.com")
    items = client.get("/inventory", headers=h).json()
    coin = next(i for i in items if i["kind"] == "Coin")
    assert coin["name"] == "8 guilders"
    assert any(i["name"] == "Lantern (unlit)" for i in items)

    _act(client, h, "I steal from the strongbox")  # +14 silver
    items = client.get("/inventory", headers=h).json()
    assert any(i["name"] == "22 guilders" for i in items)


def test_companions_honestly_empty(client: TestClient):
    h, _cid = _setup(client, "comp@example.com")
    assert client.get("/companions", headers=h).json() == []


def test_screens_follow_play_state_across_travel(client: TestClient):
    """Screens reflect the live position: character/npc consistency isn't fixture-locked."""
    h, _cid = _setup(client, "traveler@example.com")
    _act(client, h, "I walk to the market")
    m = client.get("/map", headers=h).json()
    assert any("Market" in p["name"] for p in m["places"])
    s = client.get("/state", headers=h).json()
    assert "Market" in s["location"]
