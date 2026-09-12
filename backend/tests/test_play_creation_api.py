"""6-stage character creation over HTTP: draft, advance, commit -> live sheet."""
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
from app.modules.character.attributes import derived_max_hp, derived_max_stamina
from app.modules.inventory import models as _inv  # noqa: F401
from app.modules.play import models as pm  # noqa: F401
from app.modules.play.models import PlayStateRow
from app.modules.play.state import PlayState

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


def _advance(client: TestClient, h: dict, stage: int, payload: dict) -> dict:
    r = client.post("/character/creation/advance", headers=h, json={"stage": stage, "payload": payload})
    return {"status": r.status_code, "body": r.json()}


STAGES: list[tuple[int, dict]] = [
    (1, {"name": "Berrick Vane", "pronouns": "he/him", "age_range": "adult", "homeland": "Ravenford", "appearance": "rain-worn coat"}),
    (2, {"background": "Former Soldier"}),
    (3, {"drives": ["Repay an old debt", "Keep the road open"]}),
    (4, {"attributes": {"Might": 12, "Agility": 11, "Intellect": 10, "Awareness": 9, "Will": 10, "Presence": 8}}),
    (5, {"skills": ["Swordsmanship", "Athletics"], "traits": ["Iron stomach", "Light sleeper"]}),
]


def test_options_and_state(client: TestClient):
    h, _cid = _setup(client, "creation0@example.com")
    r = client.get("/character/creation", headers=h)
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["stage"] == 1 and body["complete"] is False and body["applied"] is False
    opts = body["options"]
    assert "Former Soldier" in [b["name"] for b in opts["backgrounds"]]
    assert opts["attributes"]["names"] and opts["attributes"]["pool"] == 60
    assert "Swordsmanship" in opts["skills"]


def test_stage_order_is_enforced(client: TestClient):
    h, _cid = _setup(client, "creation1@example.com")
    out = _advance(client, h, 2, {"background": "Former Soldier"})
    assert out["status"] == 400
    assert "expected stage 1" in out["body"]["detail"]


def test_validation_errors_surface_clearly(client: TestClient):
    h, _cid = _setup(client, "creation2@example.com")
    assert _advance(client, h, 1, {"name": "  "})["status"] == 400
    _advance(client, h, 1, {"name": "Berrick Vane"})
    assert _advance(client, h, 2, {"background": "Space Marine"})["status"] == 400
    _advance(client, h, 2, {"background": "Former Soldier"})
    assert _advance(client, h, 3, {"drives": ["Only one"]})["status"] == 400


def test_full_path_commits_to_live_sheet(client: TestClient):
    h, _cid = _setup(client, "creation3@example.com")
    for stage, payload in STAGES:
        out = _advance(client, h, stage, payload)
        assert out["status"] == 200, out
        assert out["body"]["stage"] == stage + 1

    state = client.get("/character/creation", headers=h).json()
    assert state["stage"] == 6 and state["complete"] is False  # review pending
    assert state["data"]["identity"]["name"] == "Berrick Vane"

    commit = client.post("/character/creation/commit", headers=h)
    assert commit.status_code == 200, commit.text
    applied = commit.json()
    assert applied["applied"] is True
    after = client.get("/character/creation", headers=h).json()
    assert after["complete"] is True and after["applied"] is True

    # Live state: character page and adventure left panel read from here.
    c = client.get("/character", headers=h).json()
    assert c["name"] == "Berrick Vane"
    assert c["attributes"]["Might"] == 12 and c["attributes"]["Finesse"] == 11
    assert c["drives"] == ["Repay an old debt", "Keep the road open"]
    expected_hp = derived_max_hp(12, 10, level=1)
    expected_st = derived_max_stamina(12, 11, level=1)
    assert c["hp"] == {"cur": expected_hp, "max": expected_hp}
    assert c["stamina"]["max"] == expected_st
    assert "short sword" in [e.lower() for e in c["equipment"]]  # background grant
    assert c["traits"] == ["Iron stomach", "Light sleeper"]

    # The same sheet is what /state renders.
    s = client.get("/state", headers=h).json()
    assert s["character"]["name"] == "Berrick Vane"

    # Double commit is refused.
    again = client.post("/character/creation/commit", headers=h)
    assert again.status_code == 400


def test_commit_before_complete_is_refused(client: TestClient):
    h, _cid = _setup(client, "creation4@example.com")
    _advance(client, h, 1, {"name": "Too Early"})
    r = client.post("/character/creation/commit", headers=h)
    assert r.status_code == 400
    assert "not complete" in r.json()["detail"]


def test_creation_requires_auth(client: TestClient):
    assert client.get("/character/creation").status_code == 401
    assert client.post("/character/creation/advance", json={"stage": 1, "payload": {}}).status_code == 401


def test_created_character_plays(client: TestClient):
    """A created character can immediately act in the world."""
    h, cid = _setup(client, "creation5@example.com")
    for stage, payload in STAGES:
        _advance(client, h, stage, payload)
    client.post("/character/creation/commit", headers=h)

    r = client.post("/act", headers=h, json={"text": "I ask Marla about the travelers", "seed_roll": 18})
    assert r.status_code == 200, r.text

    db = TestingSession()
    try:
        st = PlayState.from_json(db.get(PlayStateRow, cid).state_json)
        assert st.pc["name"] == "Berrick Vane"
        assert st.pc["created"] is True
    finally:
        db.close()
