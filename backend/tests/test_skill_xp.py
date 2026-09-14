"""Skill XP from play checks: every resolved attempt trains — failure included.

A failed check used to award nothing; the play loop now pays the skill by
outcome through ``play_skill_xp`` (success/exceptional keep their amounts,
failures train at the graded scale). Covers the scale itself, the authored-
beat storeroom throw, and that the awaiting-throw leg awards nothing.
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
from app.modules.progression.xp import PLAY_SKILL_XP, play_skill_xp
from app.modules.rules.checks import Outcome

pytestmark = pytest.mark.usefixtures("stub_play_provider")

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


def _setup(client: TestClient, email: str) -> dict:
    r = client.post(
        "/auth/register",
        json={"email": email, "password": "password123", "display_name": email.split("@")[0]},
    )
    headers = {"Authorization": f"Bearer {r.json()['token']['access_token']}"}
    c = client.post("/campaigns", headers=headers, json={})
    assert c.status_code == 201, c.text
    # The chronicle opens on the prologue's road (§intro); walk in first.
    w = client.post(
        "/act", headers=headers, json={"text": "I head down to the inn and step inside"}
    )
    assert w.status_code == 200, w.text
    return headers


STEAL = "I steal from the storeroom strongbox"


def _call_steal(client: TestClient, h: dict) -> dict:
    """Ask for the storeroom throw; returns the pending check's spec."""
    r = client.post("/act", headers=h, json={"text": STEAL})
    assert r.status_code == 200, r.text
    spec = r.json()["pending_check"]
    assert spec["skill"] == "Stealth"
    return spec


def _throw(client: TestClient, h: dict, spec: dict, roll: int) -> dict:
    r = client.post(
        "/act",
        headers=h,
        json={"text": STEAL, "roll": roll, "pending_token": spec["token"]},
    )
    assert r.status_code == 200, r.text
    return r.json()


def _stealth_row(client: TestClient, h: dict) -> dict:
    rows = client.get("/skills", headers=h).json()["list"]
    return next(s for s in rows if s["name"] == "Stealth")


# ------------------------------------------------------------------ the scale

def test_play_scale_grades_every_outcome():
    assert play_skill_xp(Outcome.Exceptional) == 30
    assert play_skill_xp(Outcome.Success) == 20
    assert play_skill_xp(Outcome.SuccessWithCost) == 20
    assert play_skill_xp(Outcome.Failure) == 8
    assert play_skill_xp(Outcome.CriticalFailure) == 5
    assert all(v > 0 for v in PLAY_SKILL_XP.values())  # nothing pays nothing


def test_unknown_outcome_pays_nothing():
    assert play_skill_xp("a passing mood") == 0


# ------------------------------------------------------------- the play loop

def test_awaiting_throw_learns_nothing_yet(client: TestClient):
    h = _setup(client, "xpawait@example.com")
    _call_steal(client, h)
    assert _stealth_row(client, h)["xp"] == 0  # the die has not been thrown


def test_failed_check_still_trains_the_skill(client: TestClient):
    h = _setup(client, "xpfail@example.com")
    spec = _call_steal(client, h)
    data = _throw(client, h, spec, roll=5)  # 5 + 4 = 9 vs DC 13: margin -4
    assert data["mechanics"]["outcome"] == "Failure"
    row = _stealth_row(client, h)
    assert row["xp"] == 8
    assert row["recent"] and row["recent"][-1] == "Stealth +8"


def test_critical_failure_trains_a_little(client: TestClient):
    h = _setup(client, "xpcrit@example.com")
    spec = _call_steal(client, h)
    data = _throw(client, h, spec, roll=1)
    assert data["mechanics"]["outcome"] == "CriticalFailure"
    assert _stealth_row(client, h)["xp"] == 5


def test_exceptional_success_keeps_its_amount(client: TestClient):
    h = _setup(client, "xpwin@example.com")
    spec = _call_steal(client, h)
    data = _throw(client, h, spec, roll=20)
    assert data["mechanics"]["outcome"] == "Exceptional"
    assert _stealth_row(client, h)["xp"] == 30
