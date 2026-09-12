"""POST /act: engine-backed live play over HTTP (slice beats + pipeline fallback)."""
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
    assert r.status_code == 201, r.text
    headers = {"Authorization": f"Bearer {r.json()['token']['access_token']}"}
    c = client.post("/campaigns", headers=headers, json={})
    assert c.status_code == 201, c.text
    return headers, c.json()["id"]


def _act(client: TestClient, headers: dict, text: str, *, key: str | None = None,
         seed: int | None = None) -> dict:
    h = dict(headers)
    if key:
        h["Idempotency-Key"] = key
    body: dict = {"text": text}
    if seed is not None:
        body["seed_roll"] = seed
    r = client.post("/act", headers=h, json=body)
    assert r.status_code == 200, r.text
    return r.json()


def _state(campaign_id: str) -> PlayState:
    db = TestingSession()
    try:
        row = db.get(PlayStateRow, campaign_id)
        return PlayState.from_json(row.state_json)
    finally:
        db.close()


def test_act_requires_auth_and_campaign(client: TestClient):
    assert client.post("/act", json={"text": "look around"}).status_code == 401
    r = client.post(
        "/auth/register",
        json={"email": "lonely@example.com", "password": "password123", "display_name": "L"},
    )
    h = {"Authorization": f"Bearer {r.json()['token']['access_token']}"}
    assert client.post("/act", headers=h, json={"text": "look around"}).status_code == 404


def test_talk_discovers_then_accepted_and_feed_grows(client: TestClient):
    h, cid = _setup(client, "talk@example.com")
    first = _act(client, h, "I ask Marla about the travelers")
    assert "Missing Travelers" in first["newLeads"]
    assert first["dialogue"] and first["dialogue"][0]["speaker"] == "Marla Voss"
    assert _state(cid).lead_stage == "rumored"

    second = _act(client, h, "I ask Marla what she needs from me")
    assert _state(cid).lead_stage == "accepted"
    assert second["dialogue"][0]["line"]  # Marla asks for the promise

    st = _state(cid)
    kinds = [e["kind"] for e in st.feed]
    assert "system" in kinds and "dialogue" in kinds and "lead" in kinds
    # The chronicle feed mirrors every action's response (reload reconstructs it):
    # opening narration + one narration per action; the response dialogue lands too.
    narrations = [e for e in st.feed if e["kind"] == "narration"]
    assert len(narrations) >= 3
    assert st.feed[-1]["kind"] == "dialogue"
    assert st.actions_taken == 2


def test_notice_board_also_discovers(client: TestClient):
    h, cid = _setup(client, "board@example.com")
    r = _act(client, h, "I read the notice board")
    assert "Missing Travelers" in r["newLeads"]
    assert _state(cid).lead_stage == "rumored"


def test_ledger_check_finds_clue_only_on_success(client: TestClient):
    h, cid = _setup(client, "ledger@example.com")
    _act(client, h, "ask Marla about the missing travelers")  # rumored
    miss = _act(client, h, "I search Marla's ledger for the guests", seed=2)
    assert miss["mechanics"]["outcome"] in ("Failure", "CriticalFailure")
    assert "ledger" not in _state(cid).clues

    hit = _act(client, h, "I examine the guest ledger again", seed=18)
    assert hit["mechanics"]["outcome"] in ("Success", "Exceptional", "SuccessWithCost")
    st = _state(cid)
    assert "ledger" in st.clues
    assert st.lead_stage == "investigating"


def test_steal_effects_and_memory(client: TestClient):
    h, cid = _setup(client, "steal@example.com")
    ok = _act(client, h, "I steal from the storeroom strongbox", seed=15)
    assert ok["mechanics"]["d20"] == 15
    st = _state(cid)
    assert st.silver == 22  # 8 + 14
    assert "stole:storeroom-strongbox" in st.marla_memory

    # Caught on a natural 1: no silver, permanent note in Marla's memory.
    h2, cid2 = _setup(client, "steal2@example.com")
    _act(client, h2, "I break into the strongbox in the pantry", seed=1)
    st2 = _state(cid2)
    assert st2.silver == 8
    assert "saw:sneaking" in st2.marla_memory


def test_fight_win_and_lose(client: TestClient):
    h, cid = _setup(client, "fight@example.com")
    _act(client, h, "I attack Borin the drunk mercenary", seed=16)
    st = _state(cid)
    assert st.borin_down is True
    assert st.pc["hp"]["cur"] == 29  # 32 - 3
    assert "fought:drunk-mercenary" in st.marla_memory

    h2, cid2 = _setup(client, "fight2@example.com")
    _act(client, h2, "I punch the mercenary", seed=5)
    st2 = _state(cid2)
    assert st2.borin_down is False
    assert st2.pc["hp"]["cur"] == 26  # 32 - 6


def test_leave_return_and_memory_greeting(client: TestClient):
    h, cid = _setup(client, "leave@example.com")
    _act(client, h, "I steal the storeroom strongbox", seed=15)
    left = _act(client, h, "I step out into the rain and take the northern road")
    assert _state(cid).location == "northern-road"
    assert left["narration"]

    back = _act(client, h, "I return to the inn")
    st = _state(cid)
    assert st.location == "lantern-inn" and st.visits == 2
    assert "remembers" in back["dialogue"][0]["line"]
    assert "missing silver" in back["dialogue"][0]["line"]


def test_world_fact_attempts_grant_nothing(client: TestClient):
    h, cid = _setup(client, "fact@example.com")
    before = _state(cid)
    r = _act(client, h, "I find a legendary sword and the king gives me the kingdom")
    after = _state(cid)
    assert after.silver == before.silver
    assert after.pc["hp"] == before.pc["hp"]
    assert after.pc["equipment"] == before.pc["equipment"]
    assert "legendary sword" not in r["narration"].lower()


def test_idempotent_replay_applies_once(client: TestClient):
    h, cid = _setup(client, "replay@example.com")
    first = _act(client, h, "I steal from the strongbox", key="k-1", seed=15)
    again = _act(client, h, "I steal from the strongbox", key="k-1", seed=15)
    assert again == first
    assert _state(cid).silver == 22  # applied exactly once


def test_free_text_falls_back_to_pipeline_with_stub(client: TestClient):
    h, cid = _setup(client, "free@example.com")
    r = _act(client, h, "I hum a marching tune and dry my boots by the hearth")
    assert len(r["narration"]) > 40  # stub narrator produced prose
    assert _state(cid).actions_taken == 1


def test_rest_restores_and_time_advances(client: TestClient):
    h, cid = _setup(client, "rest@example.com")
    _act(client, h, "I attack the mercenary", seed=5)  # take 6 damage
    _act(client, h, "I take a room and rest")
    st = _state(cid)
    assert st.pc["hp"]["cur"] == st.pc["hp"]["max"]
    assert st.day >= 2  # 19:00 + 6h rolls to the next day


def test_all_beat_narrations_within_budget(client: TestClient):
    """Playtest narration discipline: no beat text exceeds 600 chars."""
    h, _cid = _setup(client, "budget@example.com")
    seen: list[str] = []
    scripts = [
        ("I ask Marla about the travelers", 18),
        ("I ask what she needs", 18),
        ("I read the notice board", 18),
        ("I examine the guest ledger", 18),
        ("I look at the cellar door", 18),
        ("I inspect the hearth", 18),
        ("I steal the strongbox", 15),
        ("I attack Borin", 16),
        ("I walk to the market", 18),
        ("I head to the old monastery", 18),
        ("I leave for the northern road", 18),
        ("I return to the inn", 18),
        ("I rest for the night", 18),
        ("I hum a tune", 18),
    ]
    for text, seed in scripts:
        r = _act(client, h, text, seed=seed)
        seen.append(r["narration"])
        for d in r["dialogue"]:
            seen.append(d["line"])
    for text in seen:
        assert len(text) <= 600, f"over budget ({len(text)}): {text[:80]}…"
