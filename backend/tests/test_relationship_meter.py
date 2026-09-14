"""Relationship meter (systems slice 2): values, deltas, feed feedback, mirror, UI.

Covers docs/SYSTEMS_DESIGN.md §3: PlayState.attitudes + adjust_attitude() with the
band ladder (Hostile/Wary/Neutral/Warm/Bonded), the beat delta sites slice 1 writes
memories from, the chronicle feedback system line, the GET /state attitude+band
payload, the npc_relationships table mirror, and the save/load round-trip.
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
from app.modules.campaign.npc import NPC, NPCRelationship
from app.modules.character import models as _char  # noqa: F401
from app.modules.inventory import models as _inv  # noqa: F401
from app.modules.play import models as pm  # noqa: F401
from app.modules.play.models import PlayStateRow
from app.modules.play.state import PlayState, attitude_band

pytestmark = pytest.mark.usefixtures("recording_narrator")

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
    # The chronicle opens on the prologue's road (§intro); these tests play
    # inside the tavern, so the player walks in first.
    _act(client, headers, "I head down to the inn and step inside")
    return headers, c.json()["id"]


def _act(client: TestClient, headers: dict, text: str, *, seed: int | None = None) -> dict:
    body: dict = {"text": text}
    if seed is not None:
        body["seed_roll"] = seed
    r = client.post("/act", headers=headers, json=body)
    assert r.status_code == 200, r.text
    data = r.json()
    if data.get("pending_check"):
        r = client.post("/act", headers=headers, json={
            "text": text, "roll": 18, "pending_token": data["pending_check"]["token"],
        })
        assert r.status_code == 200, r.text
        data = r.json()
    return data


def _state(campaign_id: str) -> PlayState:
    db = TestingSession()
    try:
        row = db.get(PlayStateRow, campaign_id)
        return PlayState.from_json(row.state_json)
    finally:
        db.close()


def _meter_lines(payload: dict) -> list[str]:
    return [line for line in payload.get("system", []) if line.startswith("❖")]


# ------------------------------------------------------------------ unit: bands


def test_band_thresholds():
    assert [attitude_band(v) for v in (-100, -60)] == ["Hostile", "Hostile"]
    assert [attitude_band(v) for v in (-59, -20)] == ["Wary", "Wary"]
    assert [attitude_band(v) for v in (-19, 0, 19)] == ["Neutral"] * 3
    assert [attitude_band(v) for v in (20, 59)] == ["Warm", "Warm"]
    assert [attitude_band(v) for v in (60, 100)] == ["Bonded", "Bonded"]
    # Out-of-range input is clamped before banding, never invented.
    assert attitude_band(-140) == "Hostile" and attitude_band(140) == "Bonded"


# ------------------------------------------------------- unit: meter primitives


def test_adjust_attitude_clamps_and_reports_applied_delta():
    st = PlayState()
    assert st.attitude_for("marla") == 0 and st.attitude_band_for("marla") == "Neutral"

    assert st.adjust_attitude("marla", -25, "robbed the storeroom strongbox") == -25
    assert st.adjust_attitude("marla", -90, "kept robbing") == -100  # clamped
    lines = [e["text"] for e in st.feed if e["kind"] == "system"]
    assert lines == ["❖ Marla −25 — Wary (-25)", "❖ Marla −75 — Hostile (-100)"]

    # At the cap a further outward move changes nothing — and stays silent.
    capped = PlayState()
    capped.attitudes["marla"] = 100
    assert capped.adjust_attitude("marla", +5, "kindness in the dark") == 100
    assert [e["text"] for e in capped.feed if e["kind"] == "system"] == []
    assert capped.attitude_reasons["marla"] == "kindness in the dark"  # last cause recorded


def test_meter_rides_playstate_json():
    st = PlayState()
    st.adjust_attitude("marla", -25, "robbed the storeroom strongbox")
    st.adjust_attitude("borin", +5, "asked about carts north of the oak")
    restored = PlayState.from_json(st.to_json())
    assert restored.attitudes == {"marla": -25, "borin": 5}
    assert restored.attitude_reasons["marla"] == "robbed the storeroom strongbox"
    assert restored.attitude_band_for("marla") == "Wary"


# --------------------------------------------------------- deltas from the beats


def test_polite_talk_and_promise_move_marla(client: TestClient):
    h, cid = _setup(client, "rel-talk@example.com")
    first = _act(client, h, "I ask Marla about the travelers")
    assert _meter_lines(first) == ["❖ Marla +5 — Neutral (5)"]
    second = _act(client, h, "I ask Marla what she needs from me")
    assert _meter_lines(second) == ["❖ Marla +10 — Neutral (15)"]
    assert _state(cid).attitudes == {"marla": 15}


def test_small_beats_nudge_both_ways(client: TestClient):
    # Reading her ledger while she watches: polite ask (+5) then snooping (−5).
    h, cid = _setup(client, "rel-ledger@example.com")
    _act(client, h, "I ask Marla about the travelers")
    res = _act(client, h, "I read Marla's guest ledger", seed=18)
    assert _meter_lines(res) == ["❖ Marla −5 — Neutral (0)"]
    assert _state(cid).attitudes == {"marla": 0}

    # Eyeing the barred cellar door once the lead is accepted: −5.
    h2, cid2 = _setup(client, "rel-cellar@example.com")
    _act(client, h2, "I ask Marla about the travelers")
    _act(client, h2, "I ask Marla what she needs from me")
    res2 = _act(client, h2, "I inspect the cellar door")
    assert _meter_lines(res2) == ["❖ Marla −5 — Neutral (10)"]
    assert _state(cid2).attitudes == {"marla": 10}


def test_steal_deltas_clean_seen_caught(client: TestClient):
    # Stealth +4 vs Moderate 13: seed 15 = clean, seed 10 = seen, seed 1 = caught.
    h, cid = _setup(client, "rel-steal-clean@example.com")
    clean = _act(client, h, "I steal from the storeroom strongbox", seed=15)
    assert _meter_lines(clean) == ["❖ Marla −25 — Wary (-25)"]
    assert _state(cid).attitudes == {"marla": -25}

    h2, _cid2 = _setup(client, "rel-steal-seen@example.com")
    seen = _act(client, h2, "I steal from the storeroom strongbox", seed=10)
    assert _meter_lines(seen) == ["❖ Marla −30 — Wary (-30)"]

    h3, cid3 = _setup(client, "rel-steal-caught@example.com")
    caught = _act(client, h3, "I break into the strongbox in the pantry", seed=1)
    assert _meter_lines(caught) == ["❖ Marla −35 — Wary (-35)"]
    assert _state(cid3).attitudes == {"marla": -35}


def test_brawl_and_intimidation_deltas(client: TestClient):
    # Brawl won (seed 18): Marla hates the fight itself, Borin carries the grudge.
    h, cid = _setup(client, "rel-brawl-win@example.com")
    won = _act(client, h, "I fight Borin by the fire", seed=18)
    assert _meter_lines(won) == ["❖ Marla −15 — Neutral (-15)", "❖ Borin −15 — Neutral (-15)"]
    assert _state(cid).attitudes == {"marla": -15, "borin": -15}

    # Brawl lost (seed 8): Marla −15, Borin −5 (he kept the upper hand).
    h2, _cid2 = _setup(client, "rel-brawl-lose@example.com")
    lost = _act(client, h2, "I attack Borin", seed=8)
    assert _meter_lines(lost) == ["❖ Marla −15 — Neutral (-15)", "❖ Borin −5 — Neutral (-5)"]

    # Cowing him costs −20.
    h3, cid3 = _setup(client, "rel-intimidate@example.com")
    cowed = _act(client, h3, "I intimidate Borin", seed=18)
    assert _meter_lines(cowed) == ["❖ Borin −20 — Wary (-20)"]
    assert _state(cid3).attitudes == {"borin": -20}


def test_resolution_moves_all_three(client: TestClient):
    h, cid = _setup(client, "rel-resolve@example.com")
    _act(client, h, "I ask Marla about the travelers")
    _act(client, h, "I ask Marla what she needs from me")
    _act(client, h, "I step out into the rain and take the northern road")
    _act(client, h, "I study the wagon ruts at the crossroads", seed=18)
    _act(client, h, "I follow the lanterns through the trees", seed=18)
    _act(client, h, "I head to the old monastery")
    fin = _act(client, h, "I confront what waits below and enter the cellar")
    assert fin["dialogue"] and fin["dialogue"][0]["speaker"] == "The elder traveler"

    assert _meter_lines(fin) == [
        "❖ Marla +35 — Warm (50)",
        "❖ Borin +35 — Warm (35)",
        "❖ Sella +35 — Warm (35)",
    ]
    assert _state(cid).attitudes == {"marla": 50, "borin": 35, "sella": 35}


# ------------------------------------------------------------------- GET /state


def test_state_payload_carries_attitude_and_band(client: TestClient):
    h, _cid = _setup(client, "rel-payload@example.com")
    s = client.get("/state", headers=h).json()
    marla = next(n for n in s["npcs"] if n["name"] == "Marla Voss")
    assert marla["attitude"] == 0 and marla["band"] == "Neutral"
    borin = next(n for n in s["npcs"] if n["name"] == "Borin")
    assert borin["attitude"] == 0 and borin["band"] == "Neutral"

    _act(client, h, "I steal from the storeroom strongbox", seed=15)
    s = client.get("/state", headers=h).json()
    marla = next(n for n in s["npcs"] if n["name"] == "Marla Voss")
    assert marla["attitude"] == -25 and marla["band"] == "Wary"
    # The feedback line is persisted in the chronicle like any other system line.
    assert any(line["kind"] == "system" and line["text"].startswith("❖ Marla −25") for line in s["feed"])


# ------------------------------------------------------------- npc_relationships


def test_meter_mirrors_to_relationship_rows_and_stays_idempotent(client: TestClient):
    h, cid = _setup(client, "rel-rows@example.com")
    _act(client, h, "I ask Marla about the travelers")
    _act(client, h, "I steal from the storeroom strongbox", seed=15)
    db = TestingSession()
    try:
        npcs = {n.name: n for n in db.query(NPC).filter(NPC.campaign_id == cid)}
        rows = db.query(NPCRelationship).filter(NPCRelationship.campaign_id == cid).all()
        assert len(rows) == 1  # only NPCs whose meter has moved get a row
        row = rows[0]
        assert row.from_npc_id == npcs["Marla Voss"].id and row.to_entity_id == "pc"
        assert row.attitude == -20  # +5 polite ask, then the −25 robbery
        assert row.note == "robbed the storeroom strongbox"  # last cause
    finally:
        db.close()

    # Another store with no meter movement writes nothing new (idempotent)…
    _act(client, h, "I look around the room")
    # …and a repeat of the same remembered robbery changes neither value nor note.
    _act(client, h, "I steal from the storeroom strongbox", seed=15)
    db = TestingSession()
    try:
        rows = db.query(NPCRelationship).filter(NPCRelationship.campaign_id == cid).all()
        assert len(rows) == 1 and rows[0].attitude == -20
        assert rows[0].note == "robbed the storeroom strongbox"
    finally:
        db.close()


# -------------------------------------------------------------- save/load ride


def test_saveload_round_trips_the_meter(client: TestClient):
    h, cid = _setup(client, "rel-saveload@example.com")
    _act(client, h, "I steal from the storeroom strongbox", seed=15)
    save = client.post(f"/campaigns/{cid}/saves", headers=h, json={"label": "After the theft"}).json()

    # Move the meter on: the promise lands after the save.
    _act(client, h, "I ask Marla about the travelers")
    _act(client, h, "I ask Marla what she needs from me")
    st = client.get("/state", headers=h).json()
    marla = next(n for n in st["npcs"] if n["name"] == "Marla Voss")
    assert marla["attitude"] == -10 and marla["band"] == "Neutral"

    r = client.post(f"/saves/{save['id']}/load", headers=h)
    assert r.status_code == 200 and r.json()["loaded"] is True
    st = client.get("/state", headers=h).json()
    marla = next(n for n in st["npcs"] if n["name"] == "Marla Voss")
    assert marla["attitude"] == -25 and marla["band"] == "Wary"  # as of the save
