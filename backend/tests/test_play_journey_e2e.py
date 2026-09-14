"""The playable journey, start to finish, over HTTP with the recording narrator.

register -> login -> campaign -> opening state -> narrated opening -> seven
flows -> clues -> resolution -> epilogue -> save -> reload -> completed; plus
idempotent replay and post-completion free play. Runs in CI like any other
suite. P14: every word the player reads in the journey is written by the
narrator double — the seed's own page (POST /opening) included.
"""
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
from tests.narrator_fake import MARK, RecordingNarrator

pytestmark = pytest.mark.usefixtures("recording_narrator")


def _last_facts(narrator: RecordingNarrator) -> str:
    """The facts the engine handed the narrator for the beat just resolved."""
    return " | ".join(RecordingNarrator.facts_of(narrator.narrator_prompts[-1]))

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


def test_full_journey_start_to_finish(client: TestClient,
                                      recording_narrator: RecordingNarrator):
    # -- 1. Account: register, then log in with a real credentials round-trip.
    email, pw = "journey@example.com", "password123"
    reg = client.post("/auth/register", json={"email": email, "password": pw, "display_name": "Journey"})
    assert reg.status_code == 201, reg.text
    login = client.post("/auth/login", data={"username": email, "password": pw})
    assert login.status_code == 200, login.text
    h = {"Authorization": f"Bearer {login.json()['access_token']}"}
    assert client.get("/auth/me", headers=h).json()["email"] == email

    # Fresh account: nothing to play yet.
    assert client.get("/state", headers=h).status_code == 404
    assert client.post("/act", headers=h, json={"text": "look around"}).status_code == 404

    # -- 2. Campaign: create, then the opening state is live.
    c = client.post("/campaigns", headers=h, json={"name": "The Long Road"})
    assert c.status_code == 201, c.text
    cid = c.json()["id"]

    st = client.get("/state", headers=h).json()
    assert st["location"] == "The road to Ravenford"  # the prologue (§intro)
    assert st["lead_stage"] == "unheard"
    # P14: the seed carries no prose; the state says the opening is owed.
    assert st["feed"] == [] and st["opening_pending"] is True
    assert st["character"]["name"] == "Kaelis Thorn"

    # -- 2a. The chronicle's opening page is asked for once, written once.
    opening = client.post("/opening", headers=h, json={})
    assert opening.status_code == 200, opening.text
    assert MARK in opening.json()["narration"]           # the model's words
    assert client.post("/opening", headers=h, json={}).json()["already"] is True
    st = client.get("/state", headers=h).json()
    assert st["feed"][0]["kind"] == "narration"
    assert st["opening_pending"] is False

    def act(text: str, seed: int = 18, key: str | None = None) -> dict:
        headers = dict(h)
        if key:
            headers["Idempotency-Key"] = key
        r = client.post("/act", headers=headers, json={"text": text, "seed_roll": seed})
        assert r.status_code == 200, r.text
        return r.json()

    # -- 2b. The walk-in: the road lets go, the Lantern takes you in (§intro).
    arrive = act("I head down to the inn and step inside")
    assert arrive["dialogue"][0]["speaker"] == "Marla Voss"
    assert "Lantern Inn" in client.get("/state", headers=h).json()["location"]

    # -- 3. The seven flows (talk / inspect / steal / fight / discover-lead / leave / return)
    talk = act("I ask Marla about the travelers")
    assert "Missing Travelers" in talk["newLeads"] and talk["dialogue"]
    accept = act("I ask Marla what she needs from me")
    assert accept["dialogue"][0]["speaker"] == "Marla Voss"

    board = act("I read the notice board")
    assert board["narration"]

    hearth = act("I inspect the hearth")
    assert "hearth" in hearth["narration"]

    steal = act("I steal from the storeroom strongbox", seed=15)
    assert steal["mechanics"]["outcome"] in ("Success", "Exceptional", "SuccessWithCost")
    inv = client.get("/inventory", headers=h).json()
    coin = next(i for i in inv if i["kind"] == "Coin")
    assert coin["name"] == "22 guilders"  # 8 to start, 14 lifted

    fight = act("I attack Borin the drunk mercenary", seed=16)
    assert fight["mechanics"]["d20"] == 16
    st = client.get("/state", headers=h).json()
    assert st["character"]["hp"]["cur"] == 29  # took three, won the room

    act("I step out into the rain and take the northern road")
    assert "Northern Road" in client.get("/state", headers=h).json()["location"]
    back = act("I return to the inn")
    assert back["dialogue"] and back["dialogue"][0]["speaker"] == "Marla Voss"
    # The welcome is the model's now; what it is given is the engine's —
    # Marla's working memory: the theft and the brawl persist (P14).
    facts = _last_facts(recording_narrator)
    assert "missing silver" in facts
    assert "brawl" in facts

    # -- 4. Investigation: clues -> investigating -> a route opens.
    act("I step out and take the northern road")
    tracks = act("I study the wagon ruts at the crossroads")
    assert tracks["mechanics"]["outcome"] in ("Success", "Exceptional", "SuccessWithCost")
    lights = act("I follow the lanterns through the trees")  # stealth route
    assert lights["mechanics"]["label"].startswith("Stealth")
    st = client.get("/state", headers=h).json()
    assert st["lead_stage"] == "investigating" and set(st["clues"]) == {"tracks", "lanterns"}

    # -- 5. Save mid-journey (snapshot carries the whole live state).
    save = client.post(f"/campaigns/{cid}/saves", headers=h, json={"label": "Before the cellar"})
    assert save.status_code == 201, save.text
    save_id = save.json()["id"]
    assert save.json()["snapshot"]["play"]["clues"] == ["tracks", "lanterns"]

    # -- 6. Wander off, then reload: the world rolls back to the save.
    act("I return to the inn")
    _ = act("I steal from the strongbox again", seed=15)  # silver up, position changed
    loaded = client.post(f"/saves/{save_id}/load", headers=h)
    assert loaded.status_code == 200 and loaded.json()["had_play_state"] is True
    st = client.get("/state", headers=h).json()
    assert "Northern Road" in st["location"]
    assert st["clues"] == ["tracks", "lanterns"]

    # -- 7. Resolution: the road ends at the monastery cellar.
    act("I head to the old monastery")
    fin = act("I confront what waits below and enter the cellar")
    facts = _last_facts(recording_narrator)
    assert "alive" in facts and "cuts them loose" in facts
    assert fin["dialogue"][0]["speaker"] == "The elder traveler"
    # P14: even the epilogue is the model's — carried by the resolution facts
    # (the travelers walk the north road home; Ravenford wakes to the story),
    # never a code-owned closing line.
    assert "north road" in facts or "over Ravenford" in facts

    st = client.get("/state", headers=h).json()
    assert st["completed"] is True
    assert st["lead_stage"] == "solved"
    assert any("Freed the missing travelers" in a for a in st["character"]["achievements"])

    campaigns = client.get("/campaigns", headers=h).json()
    assert campaigns[0]["status"] == "completed"

    # -- 8. Idempotent replay is safe; the cost headers tell the truth.
    body = {"text": "I ask Marla about the travelers", "seed_roll": 18}
    first = client.post("/act", headers={**h, "Idempotency-Key": "journey-1"}, json=body)
    again = client.post("/act", headers={**h, "Idempotency-Key": "journey-1"}, json=body)
    assert first.json() == again.json()
    assert again.headers["x-cache"] == "hit"

    # -- 9. Post-completion: the tale is told, the world stays open.
    after = act("I return to the inn and ask Marla for a cup")
    assert after["narration"]
    st = client.get("/state", headers=h).json()
    assert st["completed"] is True and st["lead_stage"] == "solved"

    # -- 10. Autosaves accumulated along the way and are loadable.
    rows = client.get(f"/campaigns/{cid}/saves", headers=h).json()
    assert any(r["slot"] == "autosave" for r in rows)
    assert len(rows) >= 2

    # -- 11. Every screen renders live data by the end of the journey.
    for path in ("/character", "/skills", "/journal", "/map", "/inventory", "/companions"):
        assert client.get(path, headers=h).status_code == 200, path
    journal = client.get("/journal", headers=h).json()
    assert journal["nodes"], "the thread should be visible by now"
    skills = client.get("/skills", headers=h).json()
    assert any(s["xp"] > 0 for s in skills["list"]), "the journey taught something"
