"""Inspiration (table applause): earn on story beats, spend for advantage.

Earning is progress-gated (lead advanced, clue found, warmer band) — never
silver, HP, or idle chatter. Spending arms the next surfaced check with
advantage: two dice, keep the higher. Covers the earn rule, the cap, the
spend beat, the two-phase pending spec, and the keep-higher resolution.
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
from app.modules.auth.models import User  # noqa: F401  (register metadata)
from app.modules.campaign import models as cm  # noqa: F401
from app.modules.campaign import npc as _npc  # noqa: F401
from app.modules.campaign import story as _story  # noqa: F401
from app.modules.campaign import world as _world  # noqa: F401
from app.modules.character import models as _char  # noqa: F401
from app.modules.inventory import models as _inv  # noqa: F401
from app.modules.play import models as pm
from app.modules.play.engine import INSPIRATION_MAX
from app.modules.rules.checks import CheckRequest, Outcome, roll_check

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
def client() -> TestClient:
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
    assert c.status_code == 201, c.text
    cid = c.json()["id"]
    w = client.post(
        "/act", headers=headers, json={"text": "I head down to the inn and step inside"}
    )
    assert w.status_code == 200, w.text
    return headers, cid


def _act(client: TestClient, h: dict, text: str, **kw) -> dict:
    body = {"text": text}
    body.update(kw)
    r = client.post("/act", headers=h, json=body)
    assert r.status_code == 200, r.text
    return r.json()


def _state(client: TestClient, h: dict) -> dict:
    r = client.get("/state", headers=h)
    assert r.status_code == 200, r.text
    return r.json()


ASK = "I ask Marla about the travelers"
STEAL = "I steal from the storeroom strongbox"
SPEND = "I spend my inspiration"


def _set_inspiration(cid: str, value: int) -> None:
    db = TestingSession()
    try:
        row = db.query(pm.PlayStateRow).filter_by(campaign_id=cid).one()
        payload = json.loads(row.state_json)
        payload["inspiration"] = value
        row.state_json = json.dumps(payload)
        db.commit()
    finally:
        db.close()


# ------------------------------------------------------------------ advantage
def _req() -> CheckRequest:
    return CheckRequest(campaign_id="t", skill="Stealth", attribute_mod=0,
                        skill_mod=0, dc=10, difficulty="Standard")


def test_roll_check_advantage_keeps_the_higher():
    res = roll_check(_req(), roll=8, roll2=13)
    assert (res.roll, res.outcome) == (13, Outcome.Success)
    assert res.kept_from == [8, 13]


def test_roll_check_advantage_crit_uses_the_kept_die():
    assert roll_check(_req(), roll=20, roll2=3).outcome == Outcome.Exceptional
    assert roll_check(_req(), roll=3, roll2=20).outcome == Outcome.Exceptional
    assert roll_check(_req(), roll=3, roll2=4).outcome == Outcome.Failure


def test_roll_check_advantage_rejects_a_crooked_second_die():
    with pytest.raises(ValueError):
        roll_check(_req(), roll=10, roll2=21)


# --------------------------------------------------------------------- earning
def test_lead_advance_earns_one(client: TestClient):
    h, _ = _setup(client, "inspirearn@example.com")
    before = _state(client, h)["inspiration"]
    data = _act(client, h, ASK)  # Marla shares the rumor: lead none -> rumored
    after = _state(client, h)["inspiration"]
    assert after == before + 1
    assert any("Inspiration +1" in (line.get("text") if isinstance(line, dict) else line or "")
               for line in data.get("system", []))


def test_idle_chatter_earns_nothing(client: TestClient):
    h, _ = _setup(client, "inspiridle@example.com")
    before = _state(client, h)["inspiration"]
    _act(client, h, "I look around the common room")
    assert _state(client, h)["inspiration"] == before


def test_inspiration_caps(client: TestClient):
    h, cid = _setup(client, "inspircap@example.com")
    _set_inspiration(cid, INSPIRATION_MAX)
    _act(client, h, ASK)  # would earn — but the cup is full
    assert _state(client, h)["inspiration"] == INSPIRATION_MAX


# --------------------------------------------------------------------- spending
def test_spend_with_empty_hands(client: TestClient):
    h, _ = _setup(client, "inspirbroke@example.com")
    data = _act(client, h, SPEND)
    assert "No inspiration" in data["ack"]
    st = _state(client, h)
    assert st["inspiration"] == 0 and st["inspired"] is False


def test_spend_arms_the_next_roll(client: TestClient):
    h, _ = _setup(client, "inspirspend@example.com")
    _act(client, h, ASK)
    data = _act(client, h, SPEND)
    assert "burns" in data["ack"]
    st = _state(client, h)
    assert st["inspiration"] == 0 and st["inspired"] is True


def test_pending_spec_carries_advantage_and_resolve_keeps_higher(client: TestClient):
    h, _ = _setup(client, "inspirthrow@example.com")
    _act(client, h, ASK)
    _act(client, h, SPEND)
    spec = _act(client, h, STEAL)["pending_check"]
    assert spec["skill"] == "Stealth"
    assert spec.get("advantage") is True
    # 5 + 4 = 9 would fail DC 13; 19 + 4 = 23 (margin 10) stands instead.
    data = _act(client, h, STEAL, roll=5, roll2=19, pending_token=spec["token"])
    mech = data["mechanics"]
    assert mech["outcome"] == "Exceptional"
    assert mech["d20"] == 19 and mech["d20_second"] == 5
    assert mech.get("advantage") is True
    st = _state(client, h)
    assert st["inspired"] is False  # burned, win or lose


def test_second_face_ignored_without_inspiration(client: TestClient):
    h, _ = _setup(client, "inspirplain@example.com")
    spec = _act(client, h, STEAL)["pending_check"]
    assert not spec.get("advantage")
    data = _act(client, h, STEAL, roll=5, roll2=19, pending_token=spec["token"])
    assert data["mechanics"]["outcome"] == "Failure"
    assert data["mechanics"]["d20"] == 5
