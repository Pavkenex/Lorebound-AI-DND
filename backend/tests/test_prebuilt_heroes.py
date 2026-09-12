"""Standard prebuilt hero sheets: catalog validity + apply flow over HTTP."""
from __future__ import annotations

import itertools

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from app.core.database import Base, get_db
from app.main import app
from app.modules.auth.models import User  # noqa: F401  (register metadata)
from app.modules.campaign import models as _cm  # noqa: F401
from app.modules.campaign import npc as _npc  # noqa: F401
from app.modules.campaign import story as _story  # noqa: F401
from app.modules.campaign import world as _world  # noqa: F401
from app.modules.character import models as _char  # noqa: F401
from app.modules.character.attributes import ATTRIBUTES
from app.modules.character.creation import BACKGROUNDS, is_complete
from app.modules.character.prebuilt import ATTRIBUTE_POOL, PREBUILTS
from app.modules.inventory import models as _inv  # noqa: F401
from app.modules.play import models as _pm  # noqa: F401
from app.modules.play.creation import SUGGESTED_TRAITS
from app.modules.progression.skills import SKILL_REGISTRY

engine = create_engine("sqlite://", connect_args={"check_same_thread": False}, poolclass=StaticPool)
TestingSession = sessionmaker(bind=engine, autoflush=False, autocommit=False)
Base.metadata.create_all(bind=engine)

_EMAIL_SEQ = itertools.count(1)


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


def _setup(client: TestClient) -> tuple[dict, str]:
    email = f"hero{next(_EMAIL_SEQ)}@example.com"
    r = client.post("/auth/register", json={"email": email, "password": "password123", "display_name": "H"})
    assert r.status_code == 201, r.text
    headers = {"Authorization": f"Bearer {r.json()['token']['access_token']}"}
    c = client.post("/campaigns", headers=headers, json={})
    assert c.status_code == 201, c.text
    return headers, c.json()["id"]


# ---------------------------------------------------------------- catalog --


def test_catalog_is_standard_and_fully_valid():
    assert len(PREBUILTS) >= 6
    ids = [h.id for h in PREBUILTS]
    names = [h.name for h in PREBUILTS]
    assert len(set(ids)) == len(ids)
    assert len(set(names)) == len(names)
    assert {h.klass for h in PREBUILTS} >= {"Knight", "Rogue", "Wizard", "Cleric", "Ranger", "Bard"}
    for hero in PREBUILTS:
        # Builds through the REAL stage validators; raises if anything is off.
        draft = hero.build_creation_state()
        assert is_complete(draft)
        assert set(hero.attributes) == set(ATTRIBUTES)
        assert all(1 <= v <= 20 for v in hero.attributes.values())
        assert sum(hero.attributes.values()) <= ATTRIBUTE_POOL
        assert hero.drives[0] != hero.drives[1]
        assert hero.background in BACKGROUNDS
        assert hero.skills and all(s in SKILL_REGISTRY for s in hero.skills)
        assert hero.traits and all(t in SUGGESTED_TRAITS for t in hero.traits)
        assert hero.blurb and hero.appearance


# ------------------------------------------------------------- HTTP flow --


def test_list_endpoint_requires_auth(client):
    assert client.get("/character/prebuilts").status_code == 401


def test_list_endpoint_returns_cards(client):
    headers, _ = _setup(client)
    r = client.get("/character/prebuilts", headers=headers)
    assert r.status_code == 200, r.text
    heroes = r.json()
    assert len(heroes) == len(PREBUILTS)
    first = heroes[0]
    assert {"id", "name", "class", "blurb", "attributes", "skills", "traits", "background"} <= set(first)


def test_apply_endpoint_sets_the_live_character(client):
    headers, _ = _setup(client)
    r = client.post("/character/prebuilts/knight/apply", headers=headers, json={})
    assert r.status_code == 200, r.text
    doc = r.json()
    assert doc["applied"] is True
    assert doc["character"]["name"] == "Ser Aldric Vane"
    assert doc["character"]["created"] is True

    state = client.get("/state", headers=headers).json()
    assert state["character"]["name"] == "Ser Aldric Vane"

    creation = client.get("/character/creation", headers=headers).json()
    assert creation["applied"] is True


def test_apply_twice_is_refused(client):
    headers, _ = _setup(client)
    assert client.post("/character/prebuilts/rogue/apply", headers=headers, json={}).status_code == 200
    again = client.post("/character/prebuilts/rogue/apply", headers=headers, json={})
    assert again.status_code == 400
    assert "already created" in again.json()["detail"]


def test_apply_unknown_id_is_404(client):
    headers, _ = _setup(client)
    r = client.post("/character/prebuilts/nobody/apply", headers=headers, json={})
    assert r.status_code == 404


def test_every_prebuilt_applies_cleanly(client):
    """Each catalog entry goes through the real HTTP apply at least once."""
    for hero in PREBUILTS:
        headers, _ = _setup(client)
        r = client.post(f"/character/prebuilts/{hero.id}/apply", headers=headers, json={})
        assert r.status_code == 200, f"{hero.id}: {r.text}"
        assert r.json()["character"]["name"] == hero.name
