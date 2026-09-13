"""PlaySession: seeding, persistence, checkpoints, idempotency ledger."""
from __future__ import annotations

from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from app.core.database import Base
from app.modules.auth.models import User
from app.modules.campaign import models as cm
from app.modules.campaign import npc as _npc  # noqa: F401
from app.modules.campaign import story as _story  # noqa: F401
from app.modules.campaign import world as _world  # noqa: F401
from app.modules.character import models as _char  # noqa: F401
from app.modules.inventory import models as _inv  # noqa: F401
from app.modules.play import models as pm
from app.modules.play.session import PlaySession
from app.modules.play.state import CLUES, LEAD_STAGES, PlayState, seeded_state

engine = create_engine("sqlite://", connect_args={"check_same_thread": False}, poolclass=StaticPool)
TestingSession = sessionmaker(bind=engine, autoflush=False, autocommit=False)
Base.metadata.create_all(bind=engine)


def _campaign() -> str:
    import uuid

    db = TestingSession()
    try:
        user = User(
            email=f"keeper-{uuid.uuid4().hex[:8]}@example.com", password_hash="x", display_name="Keeper"
        )
        db.add(user)
        db.flush()
        campaign = cm.Campaign(owner_user_id=user.id, name="Saga", seed_key="custom")
        db.add(campaign)
        db.commit()
        db.refresh(campaign)
        return campaign.id
    finally:
        db.close()


def test_seeded_state_defaults():
    st = seeded_state()
    assert st.location == "road-to-ravenford"  # the prologue (§intro)
    assert st.scene == "road-to-ravenford"
    assert st.lead_stage == "unheard"
    assert st.completed is False
    assert st.pc["name"] == "Kaelis Thorn"
    assert [e["kind"] for e in st.feed] == ["narration"]
    assert "Kaelis Thorn" in st.feed[0]["text"]  # the arrival reads the sheet
    assert st.silver == 8 and st.visits == 1


def test_store_and_load_round_trip():
    cid = _campaign()
    db = TestingSession()
    try:
        session = PlaySession.seed_for(db, cid)
        st = session.state
        st.find_clue("ledger")
        st.note("talked:marla")
        st.advance_minutes(90)
        st.append_feed("system", text="❧ you ask about the wagon")
        st.silver += 5
        session.store(db)

        fresh = PlaySession.load(TestingSession(), cid)
        assert fresh.persisted is True
        assert fresh.state.clues == ["ledger"]
        assert fresh.state.marla_memory == ["talked:marla"]
        assert (fresh.state.day, fresh.state.hour, fresh.state.minute) == (1, 20, 30)
        assert fresh.state.silver == 13
        assert fresh.state.feed[-1]["text"].endswith("wagon")
    finally:
        db.close()


def test_load_without_row_does_not_write():
    cid = _campaign()
    db = TestingSession()
    try:
        session = PlaySession.load(db, cid)
        assert session.persisted is False
        assert db.get(pm.PlayStateRow, cid) is None  # a GET must not write
    finally:
        db.close()


def test_checkpoint_writes_autosave_after_state_commit():
    cid = _campaign()
    db = TestingSession()
    try:
        session = PlaySession.seed_for(db, cid)
        session.state.silver += 10
        name = session.checkpoint(db, "steal")
        assert name == "inventory_change"

        saves = db.query(cm.SaveGame).filter(cm.SaveGame.campaign_id == cid).all()
        assert len(saves) == 1
        assert saves[0].slot == "autosave"
        assert saves[0].checkpoint == "inventory_change"

        row = db.get(pm.PlayStateRow, cid)
        assert PlayState.from_json(row.state_json).silver == 18
    finally:
        db.close()


def test_checkpoint_unknown_kind_persists_without_autosave():
    cid = _campaign()
    db = TestingSession()
    try:
        session = PlaySession.seed_for(db, cid)
        assert session.checkpoint(db, "chatter") is None
        assert db.query(cm.SaveGame).filter(cm.SaveGame.campaign_id == cid).count() == 0
        assert db.get(pm.PlayStateRow, cid) is not None
    finally:
        db.close()


def test_idempotent_action_memory_round_trip():
    cid = _campaign()
    db = TestingSession()
    try:
        session = PlaySession.seed_for(db, cid)
        assert session.recall_action(db, "k-1") is None
        session.remember_action(db, "k-1", {"narration": "rolled", "d20": 17})
        assert session.recall_action(db, "k-1") == {"narration": "rolled", "d20": 17}
        assert session.recall_action(db, "k-2") is None
        assert session.recall_action(db, None) is None
        session.remember_action(db, None, {"x": 1})  # best-effort: no key, no row
    finally:
        db.close()


def test_state_json_is_tolerant():
    assert PlayState.from_json("not json").pc["name"] == "Kaelis Thorn"
    assert PlayState.from_json('{"unknown_key": 1, "silver": 4}').silver == 4
    assert PlayState.from_json("[]").location == "lantern-inn"


def test_lead_stages_monotonic_and_clues_once():
    st = PlayState()
    assert st.lead_stage == LEAD_STAGES[0]
    assert st.set_lead_stage("rumored") is True
    assert st.set_lead_stage("rumored") is False
    assert st.set_lead_stage("unheard") is False  # no regression
    assert st.lead_stage == "rumored"
    try:
        st.set_lead_stage("legendary")
        raise AssertionError("expected ValueError")
    except ValueError:
        pass

    assert set(CLUES) == {"ledger", "tracks", "lanterns"}
    assert st.find_clue("tracks") is True
    assert st.find_clue("tracks") is False
