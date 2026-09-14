"""The prologue (§intro): a chronicle opens on the road above Ravenford.

Covers the arrival that now precedes the tavern: the opening text composed
from the player's own sheet, the walk-in as the inn's one first arrival, the
road's routing, and a New Journey keeping the built character.
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
from app.modules.campaign import models as cm  # noqa: F401  (register metadata)
from app.modules.campaign import npc as _npc  # noqa: F401
from app.modules.campaign import story as _story  # noqa: F401
from app.modules.campaign import world as _world  # noqa: F401
from app.modules.character import models as _char  # noqa: F401
from app.modules.inventory import models as _inv  # noqa: F401
from app.modules.play import models as pm  # noqa: F401
from app.modules.play.engine import INN_ARRIVAL, ActEngine
from app.modules.play.models import PlayStateRow
from app.modules.play.session import PlaySession
from app.modules.play.state import (
    OPENING_BEAT,
    prologue_opening,
    refresh_prologue_opening,
    seeded_state,
)
from app.modules.story.scenes import PROLOGUE, PROLOGUE_GOAL, PROLOGUE_LABEL

pytestmark = pytest.mark.usefixtures("stub_play_provider")

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
    previous = app.dependency_overrides.get(get_db)
    app.dependency_overrides[get_db] = _override
    try:
        yield TestClient(app, raise_server_exceptions=False)
    finally:
        if previous is None:
            app.dependency_overrides.pop(get_db, None)
        else:
            app.dependency_overrides[get_db] = previous


def _engine(campaign_id: str) -> ActEngine:
    return ActEngine(PlaySession(campaign_id, seeded_state()))


def _register_headers(client: TestClient, email: str) -> dict:
    r = client.post(
        "/auth/register",
        json={"email": email, "password": "password123", "display_name": email.split("@")[0]},
    )
    assert r.status_code == 201, r.text
    return {"Authorization": f"Bearer {r.json()['token']['access_token']}"}


# ---------------------------------------------------------- the arrival text


def test_the_arrival_reads_the_players_sheet():
    st = seeded_state()
    text = st.feed[0]["text"]
    assert st.location == PROLOGUE
    assert text == prologue_opening(st.pc)
    assert "Kaelis Thorn" in text and "the Lantern-Bearer" in text
    assert "Find the missing caravan" in text                 # the drives, verbatim
    assert "Alder shortbow" in text                           # what they carry
    assert "the rain has settled in for the night" in text    # the world first
    assert OPENING_BEAT not in text                           # the room waits inside


def test_a_custom_sheet_opens_as_itself():
    text = prologue_opening({
        "name": "Brann of the Marches",
        "epithet": "the hedge-knight",
        "equipment": ["a notched blade"],
        "drives": ["Recover what was taken"],
    })
    assert "You are Brann of the Marches, the hedge-knight." in text
    assert "a notched blade" in text
    assert "Recover what was taken" in text


def test_a_sparse_sheet_degrades_gracefully():
    text = prologue_opening({"name": "Nova"})
    assert "You are Nova." in text
    assert "What you carry" not in text and "brought you here" not in text


def test_the_refresh_rewrites_the_arrival_once_the_sheet_lands():
    st = seeded_state()
    st.pc["name"], st.pc["epithet"] = "Nova", "the finder"
    assert refresh_prologue_opening(st) is True
    assert "You are Nova, the finder." in st.feed[0]["text"]
    assert len([e for e in st.feed if e["kind"] == "narration"]) == 1  # in place
    st.location = "lantern-inn"
    assert refresh_prologue_opening(st) is False  # only the road's arrival


# ------------------------------------------------------------------ the door


def test_the_roads_door_routes_into_town():
    for text in (
        "I enter the inn",
        "I go inside",
        "I head down to the inn and step inside",
        "I walk down into town",
        "I push through the door",
    ):
        engine_obj = _engine("prologue-door")
        engine_obj.act(text)
        assert engine_obj.state.location == "lantern-inn", text
        assert engine_obj.state.scene == "lantern-inn", text


def test_walking_on_north_stays_honest():
    engine_obj = _engine("prologue-north")
    out, _checkpoint = engine_obj.act("I step out and take the northern road")
    assert engine_obj.state.location == "northern-road"
    assert "the Northern Road takes the rain quietly" in out["narration"]


def test_a_bare_leave_takes_the_last_stretch_down():
    engine_obj = _engine("prologue-leave")
    engine_obj.act("I leave")
    assert engine_obj.state.location == "lantern-inn"


def test_the_roads_own_moves_are_authored_and_never_move_the_player():
    engine_obj = _engine("prologue-beats")
    look, checkpoint = engine_obj.act("I look over the town below")
    assert checkpoint == ""  # a look is not a scene transition
    assert "Ravenford from the ridge" in look["narration"]
    assert engine_obj.state.location == PROLOGUE
    listen, _ = engine_obj.act("I listen to the rain and the river")
    assert "A fire. Voices. A door worth opening." in listen["narration"]
    assert engine_obj.state.location == PROLOGUE


def test_the_door_is_never_blocked_by_the_anti_loop():
    engine_obj = _engine("prologue-guard")
    for _ in range(6):
        engine_obj.act("I look over the town below")  # linger until the guard is on
    out, checkpoint = engine_obj.act("I head down to the inn and step inside")
    assert checkpoint == "travel"
    assert engine_obj.state.location == "lantern-inn"
    assert out["narration"] == INN_ARRIVAL
    assert "Still open:" not in out["narration"]


# ---------------------------------------------------------------- the surface


def test_the_live_state_shows_the_road_and_then_the_inn(client: TestClient):
    headers = _register_headers(client, "prologue@example.com")
    client.post("/campaigns", headers=headers, json={})

    s = client.get("/state", headers=headers).json()
    assert s["location"] == "The road to Ravenford"
    assert s["scene"]["id"] == PROLOGUE
    assert (s["scene"]["label"], s["scene"]["goal"]) == (PROLOGUE_LABEL, PROLOGUE_GOAL)
    assert s["npcs"] == []

    move = client.post(
        "/act", headers=headers, json={"text": "I head down to the inn and step inside"}
    ).json()
    assert move["dialogue"][0]["speaker"] == "Marla Voss"
    s = client.get("/state", headers=headers).json()
    assert s["location"] == "The Lantern Inn, Ravenford"
    assert [n["name"] for n in s["npcs"]] == ["Marla Voss", "Borin"]
    assert move["suggestions"][0]["label"] == "Ask Marla about the road"


def test_a_new_journey_keeps_the_built_character_on_the_road(client: TestClient):
    headers = _register_headers(client, "prologue-new@example.com")
    cid = client.post("/campaigns", headers=headers, json={}).json()["id"]

    # Build a character directly into the live sheet (the creation endpoint's
    # own coverage lives elsewhere): enough for the arrival text to be theirs.
    db = TestingSession()
    try:
        row = db.get(PlayStateRow, cid)
        state = json.loads(row.state_json)
        state["pc"].update({"created": True, "name": "Nova", "epithet": "the finder"})
        row.state_json = json.dumps(state)
        db.commit()
    finally:
        db.close()

    r = client.post(f"/campaigns/{cid}/restart", headers=headers)
    assert r.status_code == 200, r.text
    s = client.get("/state", headers=headers).json()
    assert s["location"] == "The road to Ravenford"  # a new journey starts on the road
    assert s["character"]["name"] == "Nova"
    assert "You are Nova, the finder." in s["feed"][0]["text"]
