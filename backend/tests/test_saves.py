"""Save/load + autosave checkpoints (Stream A, t_68ac42be).

- manual save / autosave / load roundtrip
- saves are ownership-scoped
- a narration failure can never lose resolved state
"""
from __future__ import annotations

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from app.core.database import Base
from app.modules.auth.models import User
from app.modules.campaign import models as cm
from app.modules.campaign import npc as _npc  # noqa: F401
from app.modules.campaign import story as _story  # noqa: F401
from app.modules.campaign import world as _world  # noqa: F401
from app.modules.campaign.service import autosave, load_save, resolve_then_narrate, write_save
from app.modules.character import models as _char  # noqa: F401
from app.modules.inventory import models as _inv  # noqa: F401

engine = create_engine(
    "sqlite://", connect_args={"check_same_thread": False}, poolclass=StaticPool
)
TestingSession = sessionmaker(bind=engine, autoflush=False, autocommit=False)
Base.metadata.create_all(bind=engine)


def _user_and_campaign(email: str = "saver@example.com") -> tuple[str, str]:
    db = TestingSession()
    try:
        u = User(email=email, password_hash="x", display_name="saver")
        db.add(u)
        db.commit()
        db.refresh(u)
        c = cm.Campaign(owner_user_id=u.id, name="Saga", seed_key="custom")
        db.add(c)
        db.commit()
        db.refresh(c)
        return u.id, c.id
    finally:
        db.close()


def test_manual_save_load_roundtrip():
    uid, cid = _user_and_campaign("r1@example.com")
    db = TestingSession()
    try:
        row = write_save(db, campaign_id=cid, slot="manual", label="before the storm")
        snap = load_save(db, save_id=row.id, owner_user_id=uid)
        assert snap["campaign_id"] == cid
        assert "saved_at" in snap
    finally:
        db.close()


def test_autosave_checkpoint_recorded():
    _, cid = _user_and_campaign("r2@example.com")
    db = TestingSession()
    try:
        row = autosave(db, campaign_id=cid, checkpoint="scene_transition")
        assert row.slot == "autosave"
        assert row.checkpoint == "scene_transition"
        with pytest.raises(ValueError):
            autosave(db, campaign_id=cid, checkpoint="nope")
    finally:
        db.close()


def test_save_is_ownership_scoped():
    uid, cid = _user_and_campaign("r3@example.com")
    db = TestingSession()
    try:
        row = write_save(db, campaign_id=cid)
        with pytest.raises(PermissionError):
            load_save(db, save_id=row.id, owner_user_id="someone-else")
        with pytest.raises(LookupError):
            load_save(db, save_id="missing", owner_user_id=uid)
    finally:
        db.close()


def test_narration_failure_keeps_resolved_state():
    _, cid = _user_and_campaign("r4@example.com")
    db = TestingSession()
    try:
        resolved = {"hp_after": 21, "xp_gained": 14}

        def boom():
            raise RuntimeError("model exploded")

        out = resolve_then_narrate(
            db, campaign_id=cid, resolve=lambda: resolved, narrate=boom
        )
        assert out["resolved"] == resolved
        assert out["prose"] is None
        assert "model exploded" in out["narration_error"]
        # The resolved state was autosaved BEFORE narration was attempted.
        snap = load_save(db, save_id=out["save_id"], owner_user_id=_owner(db, cid))
        assert snap["campaign_id"] == cid
    finally:
        db.close()


def _owner(db, campaign_id: str) -> str:
    return db.query(cm.Campaign).filter(cm.Campaign.id == campaign_id).first().owner_user_id
