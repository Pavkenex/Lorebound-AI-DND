"""The prologue (§intro): a chronicle opens on the road above Ravenford.

P14 governs every test here. The seed carries no prose at all: the opening is
the model's, written from the player's own sheet by ``POST /opening``, and the
road's beats hand the narrator facts. So these tests assert two things — the
*facts the engine handed over* (the prompt) and that the words the player reads
came from the provider (never a constant in the play path).
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
from app.modules.play.engine import ActEngine
from app.modules.play.models import PlayStateRow
from app.modules.play.session import PlaySession
from app.modules.play.state import reopen_prologue_opening, seeded_state
from app.modules.story.scenes import PROLOGUE, PROLOGUE_GOAL, PROLOGUE_LABEL
from tests.narrator_fake import MARK, RecordingNarrator

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


def _engine(campaign_id: str, narrator: RecordingNarrator) -> ActEngine:
    return ActEngine(PlaySession(campaign_id, seeded_state()), provider=narrator)


def _register_headers(client: TestClient, email: str) -> dict:
    r = client.post(
        "/auth/register",
        json={"email": email, "password": "password123", "display_name": email.split("@")[0]},
    )
    assert r.status_code == 201, r.text
    return {"Authorization": f"Bearer {r.json()['token']['access_token']}"}


def _facts(narrator: RecordingNarrator, index: int = -1) -> str:
    return " | ".join(RecordingNarrator.facts_of(narrator.prompts[index]))


# ------------------------------------------------------------------ the seed


def test_the_seed_writes_no_prose_at_all():
    """P14: nothing pre-written may stand as narration — not even the opening."""
    st = seeded_state()
    assert st.location == PROLOGUE
    assert st.feed == []
    assert st.opening_pending is True


# ---------------------------------------------------------- the opening text


def test_the_opening_is_the_models_and_reads_the_players_sheet():
    narrator = RecordingNarrator()
    e = _engine("opening-sheet", narrator)
    out = e.narrate_opening()

    assert MARK in out["narration"]                      # the model's words
    assert out["already"] is False
    prompt = narrator.prompts[-1]
    assert RecordingNarrator.beat_of(prompt) == "prologue.opening"
    facts = _facts(narrator)
    assert "Kaelis Thorn" in facts and "the Lantern-Bearer" in facts
    assert "Find the missing caravan" in facts            # the drives
    assert "Alder shortbow" in facts                      # what they carry
    assert "the inn" in facts                             # the town below
    # One narration in the chronicle, and the flag is spent.
    assert [x["kind"] for x in e.state.feed] == ["narration"]
    assert e.state.opening_pending is False


def test_the_opening_is_written_once():
    narrator = RecordingNarrator()
    e = _engine("opening-once", narrator)
    e.narrate_opening()
    calls = narrator.narrator_calls
    again = e.narrate_opening()
    assert again == {"narration": "", "dialogue": [], "suggestions": [], "already": True}
    assert narrator.narrator_calls == calls               # a reload costs nothing
    assert [x["kind"] for x in e.state.feed] == ["narration"]


def test_a_custom_sheet_opens_as_itself():
    narrator = RecordingNarrator()
    e = _engine("opening-custom", narrator)
    e.state.pc.update({
        "name": "Brann of the Marches",
        "epithet": "the hedge-knight",
        "equipment": ["a notched blade"],
        "drives": ["Recover what was taken"],
    })
    e.narrate_opening()
    facts = _facts(narrator)
    assert "Brann of the Marches" in facts and "the hedge-knight" in facts
    assert "a notched blade" in facts and "Recover what was taken" in facts


def test_a_sparse_sheet_still_opens():
    narrator = RecordingNarrator()
    e = _engine("opening-sparse", narrator)
    e.state.pc.update({"name": "Nova", "epithet": "", "equipment": [], "drives": []})
    out = e.narrate_opening()
    assert MARK in out["narration"]
    assert "Nova" in _facts(narrator)


def test_a_late_sheet_hands_the_opening_back_to_the_narrator():
    narrator = RecordingNarrator()
    e = _engine("opening-refresh", narrator)
    e.narrate_opening()
    assert [x["kind"] for x in e.state.feed] == ["narration"]

    e.state.pc["name"], e.state.pc["epithet"] = "Nova", "the finder"
    assert reopen_prologue_opening(e.state) is True
    assert e.state.opening_pending is True
    assert [x["kind"] for x in e.state.feed] == []         # the first take is dropped

    e.narrate_opening()
    assert "Nova" in _facts(narrator)
    assert "Kaelis Thorn" not in _facts(narrator)
    e.state.location = "lantern-inn"
    assert reopen_prologue_opening(e.state) is False       # only the road's arrival


def test_a_late_sheet_keeps_the_opening_already_played():
    """History is never rewritten: writing the opening again needs a fresh run."""
    narrator = RecordingNarrator()
    e = _engine("opening-played", narrator)
    e.narrate_opening()
    e.act("I look over the town below")                    # a beat lands
    assert reopen_prologue_opening(e.state) is True        # still on the road
    kinds = [x["kind"] for x in e.state.feed]
    assert kinds[0] == "system"                            # the action echo stays
    assert e.state.opening_pending is True


# ------------------------------------------------------------------ the door


def test_the_roads_door_routes_into_town():
    for text in (
        "I enter the inn",
        "I go inside",
        "I head down to the inn and step inside",
        "I walk down into town",
        "I push through the door",
    ):
        e = _engine("prologue-door", RecordingNarrator())
        e.act(text)
        assert e.state.location == "lantern-inn", text
        assert e.state.scene == "lantern-inn", text


def test_walking_on_north_stays_honest():
    narrator = RecordingNarrator()
    e = _engine("prologue-north", narrator)
    out, _checkpoint = e.act("I step out and take the northern road")
    assert e.state.location == "northern-road"
    assert RecordingNarrator.beat_of(narrator.prompts[-1]) == "prologue.north"
    facts = _facts(narrator)
    assert "Ravenford gathers itself behind them" in facts
    assert MARK in out["narration"]


def test_a_bare_leave_takes_the_last_stretch_down():
    e = _engine("prologue-leave", RecordingNarrator())
    e.act("I leave")
    assert e.state.location == "lantern-inn"


def test_the_roads_own_moves_never_move_the_player():
    narrator = RecordingNarrator()
    e = _engine("prologue-beats", narrator)
    look, checkpoint = e.act("I look over the town below")
    assert checkpoint == ""                                # a look is no transition
    assert RecordingNarrator.beat_of(narrator.prompts[-1]) == "prologue.look"
    assert "rooftops descending to the river" in _facts(narrator)
    assert MARK in look["narration"]
    assert e.state.location == PROLOGUE

    _listen, _ = e.act("I listen to the night")
    assert RecordingNarrator.beat_of(narrator.prompts[-1]) == "prologue.listen"
    assert "the river's low argument" in _facts(narrator)
    assert e.state.location == PROLOGUE


def test_the_door_is_never_blocked_by_the_anti_loop():
    narrator = RecordingNarrator()
    e = _engine("prologue-guard", narrator)
    for _ in range(6):
        e.act("I look over the town below")                # linger until the guard is on
    out, checkpoint = e.act("I head down to the inn and step inside")
    assert checkpoint == "travel"
    assert e.state.location == "lantern-inn"
    # The live transition is never answered with a diminishing brief (§7): the
    # lingering looks diminish (that is the guard doing its job), the door does
    # not — the door's own beat is what the narrator was handed last.
    assert RecordingNarrator.beat_of(narrator.prompts[-1]) == "inn.first"
    assert MARK in out["narration"]


# ---------------------------------------------------------------- the surface


def test_the_live_state_shows_the_road_and_then_the_inn(
    client: TestClient, recording_narrator: RecordingNarrator
):
    headers = _register_headers(client, "prologue@example.com")
    client.post("/campaigns", headers=headers, json={})

    s = client.get("/state", headers=headers).json()
    assert s["location"] == "The road to Ravenford"
    assert s["scene"]["id"] == PROLOGUE
    assert (s["scene"]["label"], s["scene"]["goal"]) == (PROLOGUE_LABEL, PROLOGUE_GOAL)
    assert s["npcs"] == []
    assert s["opening_pending"] is True      # the page knows to ask for the first page

    opening = client.post("/opening", headers=headers)
    assert opening.status_code == 200, opening.text
    assert MARK in opening.json()["narration"]
    assert opening.headers["x-ai-calls"] == "1"
    # A reload reads the chronicle: the second call narrates nothing.
    again = client.post("/opening", headers=headers)
    assert again.json()["already"] is True
    assert client.get("/state", headers=headers).json()["opening_pending"] is False

    move = client.post(
        "/act", headers=headers, json={"text": "I head down to the inn and step inside"}
    ).json()
    assert move["dialogue"][0]["speaker"] == "Marla Voss"
    s = client.get("/state", headers=headers).json()
    assert s["location"] == "The Lantern Inn, Ravenford"
    assert [n["name"] for n in s["npcs"]] == ["Marla Voss", "Borin"]
    assert move["suggestions"][0]["label"] == "Ask Marla about the road"


def test_the_opening_needs_a_connected_model(client: TestClient):
    """/opening obeys the /act contract: no model, no narration (P12)."""
    headers = _register_headers(client, "prologue-nomodel@example.com")
    client.post("/campaigns", headers=headers, json={})
    r = client.post("/opening", headers=headers)
    assert r.status_code == 400
    assert r.json()["detail"] == "connect_your_ai"


def test_a_new_journey_keeps_the_built_character_on_the_road(
    client: TestClient, recording_narrator: RecordingNarrator
):
    headers = _register_headers(client, "prologue-new@example.com")
    cid = client.post("/campaigns", headers=headers, json={}).json()["id"]
    client.post("/opening", headers=headers)

    # Build a character directly into the live sheet (the creation endpoint's
    # own coverage lives elsewhere): enough for the opening to be theirs.
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
    assert s["location"] == "The road to Ravenford"     # a new journey starts on the road
    assert s["character"]["name"] == "Nova"
    assert s["opening_pending"] is True                 # and its opening is unwritten
    assert s["feed"] == []                              # the old prose is not carried over

    fresh = client.post("/opening", headers=headers).json()
    assert MARK in fresh["narration"]
    facts = " | ".join(RecordingNarrator.facts_of(recording_narrator.prompts[-1]))
    assert "Nova" in facts and "the finder" in facts    # written for the built sheet
