"""Two-phase throws over /act: surfaced checks wait for the player's die.

Flow under test: an unseeded action that calls for a visible check answers with
``pending_check`` (skill, attribute, modifiers, DC, label, token) and persists
nothing; a second POST carrying the thrown face (``roll``) + ``pending_token``
resolves the beat exactly as a seeded call would. A moved board answers 409.
Quiet checks (hidden) never surface a die, but still report a verdict line.
"""
from __future__ import annotations

import json
import re

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
from app.modules.play.models import PlayStateRow
from app.modules.play.state import PlayState

engine = create_engine("sqlite://", connect_args={"check_same_thread": False}, poolclass=StaticPool)
TestingSession = sessionmaker(bind=engine, autoflush=False, autocommit=False)
Base.metadata.create_all(bind=engine)

STEAL = "I steal from the storeroom strongbox"
HIDDEN = "I sense motive beneath his words"          # Insight check, hidden by policy
SOCIAL = "I persuade the empty room to be quiet"     # social check, no stakes: no roll
CLIMB = "I climb the wall of the inn"                # pipeline fallback, visible Athletics check


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
    # The chronicle opens on the prologue's road (§intro); these tests play
    # inside the tavern, so the player walks in first.
    w = client.post(
        "/act", headers=headers, json={"text": "I head down to the inn and step inside"}
    )
    assert w.status_code == 200, w.text
    return headers, c.json()["id"]


def _state(campaign_id: str) -> PlayState:
    db = TestingSession()
    try:
        return PlayState.from_json(db.get(PlayStateRow, campaign_id).state_json)
    finally:
        db.close()


def _call(client: TestClient, h: dict, text: str, **extra) -> tuple[int, dict, dict]:
    """Raw /act leg: returns (status, payload, headers)."""
    r = client.post("/act", headers=h, json={"text": text, **extra})
    payload = r.json() if r.content else {}
    return r.status_code, payload, dict(r.headers)


def _throw(client: TestClient, h: dict, text: str, token: str, roll: int):
    return client.post("/act", headers=h, json={"text": text, "roll": roll, "pending_token": token})


def test_surfaced_check_waits_for_the_players_throw(client: TestClient):
    h, cid = _setup(client, "roll1@example.com")
    status, pending, headers = _call(client, h, STEAL)
    assert status == 200, pending

    # The response is the check's calling card — no roll has happened yet.
    spec = pending["pending_check"]
    assert spec["label"] == "Stealth — the storeroom strongbox"
    assert spec["skill"] == "Stealth"
    assert spec["attribute"] == "Finesse"
    assert spec["attribute_mod"] == 2          # (Finesse 15 - 10) // 2
    assert spec["skill_mod"] == 2              # trained
    assert spec["total_mod"] == 4
    assert spec["dc"] == 13                    # Moderate band
    assert spec["difficulty"] == "Moderate"
    assert len(spec["token"]) == 20
    assert pending["narration"] is None and pending["mechanics"] is None
    assert headers["x-ai-calls"] == "0"        # an authored check costs nothing

    # Nothing from the pending leg persisted: the die has not been thrown.
    st = _state(cid)
    assert st.actions_taken == 1 and st.silver == 8  # the walk-in alone landed
    assert all(e["kind"] != "dice" for e in st.feed)
    assert "stole:storeroom-strongbox" not in st.marla_memory

    # The throw: a natural 20 lands as an exceptional success, same math as ever.
    r = _throw(client, h, STEAL, spec["token"], roll=20)
    assert r.status_code == 200, r.text
    data = r.json()
    assert "pending_check" not in data
    assert data["mechanics"]["d20"] == 20
    assert data["mechanics"]["total"] == 24
    assert data["mechanics"]["dc"] == 13
    assert data["mechanics"]["outcome"] == "Exceptional"
    st = _state(cid)
    assert st.actions_taken == 2  # the walk-in + the thrown beat
    assert st.silver == 22                     # 8 + 14
    assert "stole:storeroom-strongbox" in st.marla_memory
    assert any(e["kind"] == "dice" for e in st.feed)  # the chronicle keeps the roll


def test_thrown_natural_one_is_the_critical_failure(client: TestClient):
    h, cid = _setup(client, "roll2@example.com")
    _, pending, _ = _call(client, h, STEAL)
    r = _throw(client, h, STEAL, pending["pending_check"]["token"], roll=1)
    assert r.status_code == 200, r.text
    data = r.json()
    assert data["mechanics"]["outcome"] == "CriticalFailure"
    st = _state(cid)
    assert st.silver == 8
    assert "saw:sneaking" in st.marla_memory


def test_stale_throw_is_rejected_and_lands_nothing(client: TestClient):
    h, cid = _setup(client, "roll3@example.com")
    _, pending, _ = _call(client, h, STEAL)
    token = pending["pending_check"]["token"]

    # The board moves: another action resolves first.
    status, rest, _ = _call(client, h, "I take a room and rest")
    assert status == 200 and rest["narration"]

    r = _throw(client, h, STEAL, token, roll=20)
    assert r.status_code == 409
    assert r.json()["detail"]["code"] == "check_expired"
    st = _state(cid)
    assert st.silver == 8                      # the stale throw changed nothing
    assert "stole:storeroom-strongbox" not in st.marla_memory

    # Called again from the moved board, the check resolves normally.
    _, again, _ = _call(client, h, STEAL)
    r = _throw(client, h, STEAL, again["pending_check"]["token"], roll=20)
    assert r.status_code == 200
    assert _state(cid).silver == 22


def test_pending_leg_never_persists_or_double_counts(client: TestClient):
    h, cid = _setup(client, "roll4@example.com")
    h = {**h, "Idempotency-Key": "pending-key"}
    _, first, _ = _call(client, h, STEAL)
    _, replay, _ = _call(client, h, STEAL)
    assert first["pending_check"]["token"] == replay["pending_check"]["token"]
    st = _state(cid)
    assert st.actions_taken == 1               # replays of a pending leg are free
    assert st.silver == 8


def test_hidden_checks_roll_behind_the_screen(client: TestClient):
    h, cid = _setup(client, "roll5@example.com")
    status, data, _ = _call(client, h, HIDDEN)
    assert status == 200
    assert "pending_check" not in data         # never surfaced, never thrown
    assert data["mechanics"] is None           # no dice card for a quiet check
    assert data["narration"]
    # ...but the player still gets the verdict in the chronicle.
    verdicts = [s for s in data["system"] if s.startswith("⚄ ")]
    assert len(verdicts) == 1
    assert re.fullmatch(r"⚄ Insight check — (passed|failed) · \d+ vs DC 13\.", verdicts[0])
    assert any("⚄ Insight check" in (e.get("text") or "") for e in _state(cid).feed)
    assert _state(cid).actions_taken == 2  # the walk-in + the quiet check


def test_hidden_check_verdict_reports_pass_and_fail(client: TestClient):
    h, _ = _setup(client, "roll10@example.com")
    ok = client.post("/act", headers=h, json={"text": HIDDEN, "seed_roll": 18})
    assert ok.status_code == 200, ok.text
    line = next(s for s in ok.json()["system"] if s.startswith("⚄ "))
    assert line == "⚄ Insight check — passed · 18 vs DC 13."
    bad = client.post("/act", headers=h, json={"text": HIDDEN, "seed_roll": 2})
    assert bad.status_code == 200, bad.text
    line = next(s for s in bad.json()["system"] if s.startswith("⚄ "))
    assert line == "⚄ Insight check — failed · 2 vs DC 13."


def test_social_checks_without_stakes_never_ask_for_a_die(client: TestClient):
    h, cid = _setup(client, "roll6@example.com")
    status, data, _ = _call(client, h, SOCIAL)
    assert status == 200
    assert "pending_check" not in data         # RP resolves it; no roll (t_2e94122b)
    assert data["narration"]
    assert _state(cid).actions_taken == 2  # the walk-in + the social beat


def test_pipeline_checks_also_wait_for_the_throw(client: TestClient):
    h, cid = _setup(client, "roll7@example.com")
    status, pending, headers = _call(client, h, CLIMB)
    assert status == 200, pending
    spec = pending["pending_check"]
    assert spec["skill"] == "Athletics" and spec["dc"] == 13
    assert spec["attribute_mod"] == 0 and spec["skill_mod"] == 0
    assert headers["x-ai-calls"] == "0"        # suspension happens before the narrator

    r = _throw(client, h, CLIMB, spec["token"], roll=18)
    assert r.status_code == 200, r.text
    data = r.json()
    assert data["mechanics"]["d20"] == 18
    assert data["mechanics"]["total"] == 18
    assert data["mechanics"]["outcome"] == "Success"
    assert data["narration"]
    assert int(r.headers["x-ai-calls"]) >= 1   # the resolve leg narrates
    assert _state(cid).actions_taken == 2  # the walk-in + the climbing beat


def test_seeded_calls_still_resolve_in_one_pass(client: TestClient):
    """The seed_roll test hook keeps its old single-shot semantics."""
    h, cid = _setup(client, "roll8@example.com")
    status, data, _ = _call(client, h, STEAL, seed_roll=15)
    assert status == 200
    assert "pending_check" not in data
    assert data["mechanics"]["d20"] == 15
    assert _state(cid).silver == 22


def test_roll_must_be_a_d20(client: TestClient):
    h, _cid = _setup(client, "roll9@example.com")
    for bad in (0, 21, -3):
        r = client.post("/act", headers=h, json={"text": STEAL, "roll": bad})
        assert r.status_code == 422


def test_narrator_payload_maps_into_dialogue_and_suggestions():
    """End-to-end: a fenced JSON completion is parsed, not shown raw."""
    from app.modules.ai.providers import StubProvider
    from app.modules.play.engine import ActEngine
    from app.modules.play.session import PlaySession
    from app.modules.play.state import seeded_state

    scripted = {
        "narration": "You set your tankard down and let the question sit in the air.",
        "npc_dialogue": [
            {"speaker": "Marla", "line": "Private. That's a word men use too cheaply."},
            {"speaker": "Borin", "line": "Aye, that'll end well."},
        ],
        "suggested_actions": [
            "Hold her gaze and let her finish deciding.",
            {"label": "Leave the question alone", "command": "I let it lie and drink."},
        ],
    }
    fenced = f"Here is the chronicler's answer:\n```json\n{json.dumps(scripted)}\n```"
    session = PlaySession("roll-map", seeded_state())
    provider = StubProvider(canned={"narrator": fenced})
    engine = ActEngine(session, provider=provider)
    payload, _ = engine.act("I sense motive beneath his words")

    assert payload["narration"] == scripted["narration"]      # prose, not scaffolding
    assert payload["dialogue"] == [
        {"speaker": "Marla", "line": "Private. That's a word men use too cheaply."},
        {"speaker": "Borin", "line": "Aye, that'll end well."},
    ]
    assert payload["suggestions"] == [
        {"label": "Hold her gaze and let her finish deciding.",
         "command": "Hold her gaze and let her finish deciding."},
        {"label": "Leave the question alone", "command": "I let it lie and drink."},
    ]
    # The chronicle feed mirrors the dialogue so a reload keeps it.
    assert any(e.get("kind") == "dialogue" and e.get("speaker") == "Marla"
               for e in session.state.feed)
