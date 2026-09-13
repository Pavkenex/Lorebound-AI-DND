"""The mystery arc over HTTP: clues -> investigating -> five solution paths -> epilogue."""
from __future__ import annotations

import pytest
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
    # The chronicle opens on the prologue's road (§intro); these tests play
    # inside the tavern, so the player walks in first.
    _act(client, headers, "I head down to the inn and step inside")
    return headers, c.json()["id"]


def _act(client: TestClient, headers: dict, text: str, seed: int = 18) -> dict:
    r = client.post("/act", headers=headers, json={"text": text, "seed_roll": seed})
    assert r.status_code == 200, r.text
    return r.json()


def _state(campaign_id: str) -> PlayState:
    db = TestingSession()
    try:
        return PlayState.from_json(db.get(PlayStateRow, campaign_id).state_json)
    finally:
        db.close()


def _learn_and_go(client: TestClient, h: dict) -> None:
    """Common opening: hear the lead, then walk to the road."""
    _act(client, h, "I ask Marla about the travelers")
    _act(client, h, "I step out into the rain and take the northern road")


# ---------------------------------------------------------------- clues

def test_clues_progress_to_investigating_and_open_the_trail(client: TestClient):
    h, cid = _setup(client, "clues@example.com")
    _learn_and_go(client, h)

    r = _act(client, h, "I study the wagon ruts at the crossroads")
    assert r["mechanics"]["outcome"] in ("Success", "Exceptional", "SuccessWithCost")
    st = _state(cid)
    assert "tracks" in st.clues and st.lead_stage == "investigating"
    assert st.solution_path is None  # one clue is not yet a route

    _act(client, h, "I watch the treeline for the monastery lights")
    assert "lanterns" in _state(cid).clues
    st = _state(cid)
    assert st.solution_path == "follow-the-clues"  # two clues = the Ledger Trail opens

    # A third clue is fine but not required; re-finding is a no-op.
    assert len(st.clues) == 2


def test_clue_checks_fail_soft_and_retry(client: TestClient):
    h, cid = _setup(client, "retry@example.com")
    _learn_and_go(client, h)
    bad = _act(client, h, "I search the wagon ruts", seed=2)
    assert bad["mechanics"]["outcome"] in ("Failure", "CriticalFailure")
    assert "tracks" not in _state(cid).clues
    good = _act(client, h, "I examine the road ruts closely", seed=18)
    assert "tracks" in _state(cid).clues
    assert good["narration"]


# ---------------------------------------------------------------- solution paths

def _walk_to_market(client: TestClient, h: dict) -> None:
    _act(client, h, "I walk to the market")


def _resolve_at_monastery(client: TestClient, h: dict) -> dict:
    _act(client, h, "I head to the old monastery")
    return _act(client, h, "I confront what waits in the cellar and go down")


def test_path_talk_it_out(client: TestClient):
    h, cid = _setup(client, "plea@example.com")
    _act(client, h, "I ask Marla about the travelers")
    _walk_to_market(client, h)
    r = _act(client, h, "I persuade Sella the factor to confide in me")
    assert r["mechanics"]["outcome"] in ("Success", "Exceptional", "SuccessWithCost")
    st = _state(cid)
    assert st.solution_path == "talk-it-out" and st.lead_stage == "investigating"

    out = _resolve_at_monastery(client, h)
    st = _state(cid)
    assert st.completed is True and st.travelers_freed is True
    assert st.lead_stage == "solved"
    assert "alive" in out["narration"]
    assert any("Freed the missing travelers" in a for a in st.pc["achievements"])
    assert out["dialogue"][0]["speaker"] == "The elder traveler"


def test_path_lean_on_them(client: TestClient):
    h, cid = _setup(client, "stare@example.com")
    _act(client, h, "I ask Marla about the travelers")
    _walk_to_market(client, h)
    r = _act(client, h, "I intimidate Sella until she talks")
    assert r["mechanics"]["outcome"] in ("Success", "Exceptional", "SuccessWithCost")
    assert _state(cid).solution_path == "lean-on-them"
    _resolve_at_monastery(client, h)
    assert _state(cid).completed is True


def test_path_follow_the_clues(client: TestClient):
    h, cid = _setup(client, "trail@example.com")
    _learn_and_go(client, h)
    _act(client, h, "I study the wagon ruts")
    _act(client, h, "I watch for the lights in the trees")
    assert _state(cid).solution_path == "follow-the-clues"
    _resolve_at_monastery(client, h)
    assert _state(cid).completed is True


def test_path_shadow_them(client: TestClient):
    h, cid = _setup(client, "watch@example.com")
    _learn_and_go(client, h)
    r = _act(client, h, "I follow the lanterns through the trees")
    assert r["mechanics"]["label"].startswith("Stealth")
    assert any("The way in" in s for s in r["system"])
    st = _state(cid)
    assert "lanterns" in st.clues and st.solution_path == "shadow-them"
    _resolve_at_monastery(client, h)
    assert _state(cid).completed is True


def test_path_blades_out_and_loss_continues_story(client: TestClient):
    h, cid = _setup(client, "blades@example.com")
    _learn_and_go(client, h)
    _act(client, h, "I study the wagon ruts")  # investigating
    r = _act(client, h, "I lie in wait to ambush the carriers")
    assert r["mechanics"]["outcome"] in ("Success", "Exceptional", "SuccessWithCost")
    assert _state(cid).solution_path == "blades-out"
    _resolve_at_monastery(client, h)
    assert _state(cid).completed is True

    # Losing the ambush: hurt, home, but the story continues.
    h2, cid2 = _setup(client, "blades2@example.com")
    _learn_and_go(client, h2)
    _act(client, h2, "I study the wagon ruts")
    ambush = _act(client, h2, "I ambush the carriers on the road", seed=5)
    st2 = _state(cid2)
    assert st2.completed is False
    assert st2.location == "lantern-inn"  # carried home, Marla remembers
    assert st2.pc["hp"]["cur"] < st2.pc["hp"]["max"]
    assert "fought:road-carriers" in st2.marla_memory
    assert ambush["narration"]


def test_confront_without_a_route_does_not_resolve(client: TestClient):
    h, cid = _setup(client, "noroute@example.com")
    _learn_and_go(client, h)
    _act(client, h, "I head to the old monastery")
    r = _act(client, h, "I enter the cellar")
    st = _state(cid)
    assert st.completed is False and st.solution_path is None
    assert "threads" in r["narration"] or "way in" in r["narration"]


def test_epilogue_memory_and_marla_greeting(client: TestClient):
    h, cid = _setup(client, "epilogue@example.com")
    _act(client, h, "I ask Marla about the travelers")
    _walk_to_market(client, h)
    _act(client, h, "I persuade Sella the factor to confide in me")
    _resolve_at_monastery(client, h)
    _act(client, h, "I return to the inn")
    st = _state(cid)
    assert "resolved:travelers" in st.marla_memory
    s = client.get("/state", headers=h).json()
    assert s["completed"] is True
    # The campaign row flips to completed (board-visible lifecycle).
    db = TestingSession()
    try:
        row = db.query(cm.Campaign).filter(cm.Campaign.id == cid).first()
        assert row.status == "completed"
    finally:
        db.close()


def test_post_completion_play_is_allowed_and_peaceful(client: TestClient):
    h, cid = _setup(client, "after@example.com")
    _act(client, h, "I ask Marla about the travelers")
    _walk_to_market(client, h)
    _act(client, h, "I persuade Sella the factor to confide in me")
    _resolve_at_monastery(client, h)

    r = _act(client, h, "I enter the cellar again")
    assert "quiet" in r["narration"]
    talk = _act(client, h, "I ask Marla what she needs from me")
    assert talk["narration"]
    assert _state(cid).completed is True


def test_arc_narrations_within_budget(client: TestClient):
    """Every arc beat obeys the narration budget (<= 600 chars, as the playtest report)."""
    h, _cid = _setup(client, "arcbudget@example.com")
    script = [
        ("I ask Marla about the travelers", 18),
        ("I step out into the rain and take the northern road", 18),
        ("I study the wagon ruts", 18),
        ("I follow the lanterns through the trees", 18),
        ("I lie in wait to ambush the carriers", 18),
        ("I head to the old monastery", 18),
        ("I enter the cellar", 18),
        ("I return to the inn", 18),
    ]
    texts: list[str] = []
    for text, seed in script:
        r = _act(client, h, text, seed)
        texts.append(r["narration"])
        for d in r["dialogue"]:
            texts.append(d["line"])
    over = [t for t in texts if len(t) > 600]
    assert not over, over

