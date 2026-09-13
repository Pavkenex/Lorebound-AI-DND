"""NPC memory (systems slice 1): structured writes, retrieval, table mirror, UI payload.

Covers docs/SYSTEMS_DESIGN.md §2: PlayState.npc_memory_log + remember()/memories_for(),
beat write sites, the narrator prompt's "remembers about the player" block, the
GET /state `remembers` payload, and the npc_memories table mirror.
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
from app.modules.campaign.npc import NPC, NPCMemory
from app.modules.character import models as _char  # noqa: F401
from app.modules.inventory import models as _inv  # noqa: F401
from app.modules.memory.npc_memory import npc_slug
from app.modules.narrator.prompts import PromptContext, assemble_prompt
from app.modules.play import models as pm  # noqa: F401
from app.modules.play.models import PlayStateRow
from app.modules.play.state import NPC_MEMORY_CAP, PlayState

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


# ---------------------------------------------------------------- unit: state


def test_remember_dedupe_and_ordering():
    st = PlayState()
    assert st.remember("marla", "asked about the travelers", kind="conversation") is True
    assert st.remember("marla", "asked about the travelers") is False  # exact dupe
    assert st.remember("marla", "  ") is False  # empty text
    st.advance_minutes(90)
    assert st.remember("marla", "promised to look into it", kind="promise", sentiment=1, salience=4)
    st.remember("marla", "spilled wine on the bar", salience=1)

    top = st.memories_for("marla", limit=2)
    assert [m["text"] for m in top] == ["promised to look into it", "asked about the travelers"]
    assert top[0]["kind"] == "promise" and top[0]["sentiment"] == 1
    assert top[0]["day"] == 1 and top[0]["hour"] == 20  # stamped at record time
    assert st.memories_for("borin") == []


def test_memory_cap_prunes_weakest():
    st = PlayState()
    for i in range(10):
        st.remember("marla", f"petty thing {i}", salience=1)
    for i in range(NPC_MEMORY_CAP):
        st.remember("marla", f"deed {i}", salience=3)
    assert len(st.npc_memory_log) == NPC_MEMORY_CAP
    texts = {m["text"] for m in st.npc_memory_log}
    assert "petty thing 0" not in texts  # weakest went first
    assert "deed 0" in texts and "deed 199" in texts


def test_npc_slug_mapping():
    assert npc_slug("Marla Voss") == "marla"
    assert npc_slug("Borin") == "borin"
    assert npc_slug("Brother Anselm") == "anselm"
    assert npc_slug("Tomm Ash") == "tomm"


# ------------------------------------------------------------ API: write path


def test_steal_flow_writes_structured_memory(client: TestClient):
    h, cid = _setup(client, "npcmem-steal@example.com")
    _act(client, h, "I steal from the storeroom strongbox", seed=15)
    st = _state(cid)
    theft = [m for m in st.npc_memory_log if m["npc"] == "marla" and m["kind"] == "theft"]
    assert theft, "clean steal should be remembered as theft"
    assert theft[0]["sentiment"] == -2 and theft[0]["salience"] == 4
    assert "stole:storeroom-strongbox" in st.marla_memory  # legacy tag kept

    # Caught on a natural 1: same kind, sharper salience.
    h2, cid2 = _setup(client, "npcmem-caught@example.com")
    _act(client, h2, "I break into the strongbox in the pantry", seed=1)
    st2 = _state(cid2)
    caught = [m for m in st2.npc_memory_log if m["kind"] == "theft"]
    assert caught and caught[0]["salience"] == 5
    assert "saw:sneaking" in st2.marla_memory


def test_state_payload_carries_remembers(client: TestClient):
    h, _cid = _setup(client, "npcmem-payload@example.com")
    _act(client, h, "I ask Marla about the travelers")
    s = client.get("/state", headers=h).json()
    marla = next(n for n in s["npcs"] if n["name"] == "Marla Voss")
    assert any("asked about the travelers" in t for t in marla["remembers"])
    borin = next(n for n in s["npcs"] if n["name"] == "Borin")
    assert "remembers" not in borin  # nothing on him yet — keep payload lean


def test_npc_page_serves_the_present_sheet(client: TestClient):
    """The Present list's click-through: one character, full detail."""
    h, _cid = _setup(client, "npcmem-page@example.com")
    _act(client, h, "I ask Marla about the travelers")
    r = client.get("/npcs/marla", headers=h)
    assert r.status_code == 200, r.text
    page = r.json()
    assert page["slug"] == "marla"
    assert page["name"] == "Marla Voss"
    assert isinstance(page["attitude"], int)
    assert page["band"] in {"Hostile", "Wary", "Neutral", "Warm", "Bonded"}
    assert isinstance(page["mood"], str)
    assert any("asked about the travelers" in t for t in page["remembers"])


def test_npc_page_unknown_slug_is_a_404(client: TestClient):
    h, _cid = _setup(client, "npcmem-nopage@example.com")
    assert client.get("/npcs/ghost", headers=h).status_code == 404


def test_memories_mirror_to_table_and_seed_npcs(client: TestClient):
    h, cid = _setup(client, "npcmem-db@example.com")
    _act(client, h, "I ask Marla about the travelers")
    _act(client, h, "I ask Marla what she needs from me")
    db = TestingSession()
    try:
        npcs = {n.name: n for n in db.query(NPC).filter(NPC.campaign_id == cid)}
        assert {"Marla Voss", "Borin", "Sella Voss", "Tomm Ash", "Brother Anselm"} <= set(npcs)
        rows = db.query(NPCMemory).filter(NPCMemory.campaign_id == cid).all()
        assert rows, "memories should mirror into npc_memories"
        marla_rows = [r for r in rows if r.npc_id == npcs["Marla Voss"].id]
        assert any(r.kind in ("conversation", "promise") for r in marla_rows)
        assert all(r.day >= 1 and r.hour >= 0 for r in marla_rows)
    finally:
        db.close()

    # Idempotent across further stores: the mirror never duplicates.
    _act(client, h, "I look around the room")
    db = TestingSession()
    try:
        count = db.query(NPCMemory).filter(NPCMemory.campaign_id == cid).count()
        assert count == len(_state(cid).npc_memory_log)
    finally:
        db.close()


def test_resolution_writes_resolve_memories(client: TestClient):
    h, cid = _setup(client, "npcmem-arc@example.com")
    _act(client, h, "I ask Marla about the travelers")
    _act(client, h, "I ask Marla what she needs from me")
    _act(client, h, "I step out into the rain and take the northern road")
    _act(client, h, "I study the wagon ruts at the crossroads", seed=18)
    _act(client, h, "I follow the lanterns through the trees", seed=18)
    _act(client, h, "I head to the old monastery")
    fin = _act(client, h, "I confront what waits below and enter the cellar")
    assert fin["dialogue"] and fin["dialogue"][0]["speaker"] == "The elder traveler"
    st = _state(cid)
    resolve = [m for m in st.npc_memory_log if m["kind"] == "resolve"]
    assert {m["npc"] for m in resolve} == {"marla", "borin", "sella"}
    assert all(m["sentiment"] == 2 for m in resolve)


# --------------------------------------------------------------- narrator text


def test_narrator_prompt_renders_remembers():
    ctx = PromptContext(
        location="The Lantern Inn, Ravenford",
        npcs=[
            {
                "name": "Marla",
                "note": "wiping a cup",
                "remembers": [
                    "pored over her guest ledger",
                    "promised to look into the missing travelers",
                ],
            }
        ],
    )
    bundle = assemble_prompt(ctx)
    assert "[NPCs present]" in bundle.user
    assert (
        "remembers about the player: pored over her guest ledger; "
        "promised to look into the missing travelers"
    ) in bundle.user
    # And the cap holds: only the first MAX_NPC_MEMORIES are rendered.
    ctx_many = PromptContext(npcs=[{"name": "Marla", "remembers": ["a", "b", "c", "d", "e"]}])
    rendered = assemble_prompt(ctx_many).user
    line = next(l for l in rendered.splitlines() if l.startswith("- Marla"))
    assert "remembers about the player: a; b; c; d" in line
    assert line.count(";") == 3  # exactly four memories, no fifth
