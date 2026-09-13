"""PlaySession: DB-backed live state for one campaign.

Responsibilities (card boundaries):
- load/store the :class:`PlayState` row;
- autosave checkpoints after state-changing beats (reusing campaign save slots);
- the idempotent act() engine itself lands in the /act stream — this module
  owns persistence and the small pure helpers both streams share.

Ordering rule (campaign service): resolved state is committed (and an autosave
written) BEFORE narration runs, so a narration failure never loses a resolved
action.
"""
from __future__ import annotations

import json

from sqlalchemy.orm import Session

from app.core.events import EventKind, GameEvent, event_log
from app.modules.campaign.service import autosave
from app.modules.memory.npc_memory import sync_npc_memories
from app.modules.play.models import PlayActionRow, PlayStateRow
from app.modules.play.state import PlayState, seeded_state

#: Beat kind -> autosave checkpoint name (must exist in AUTOSAVE_CHECKPOINTS).
CHECKPOINT_BY_KIND: dict[str, str] = {
    "talk": "important_dialogue",
    "inspect": "scene_transition",
    "steal": "inventory_change",
    "fight": "combat_resolution",
    "discover": "scene_transition",
    "travel": "travel",
    "rest": "rest",
    "resolve": "scene_transition",
}


class PlaySession:
    """Mutable wrapper around one campaign's play state."""

    def __init__(self, campaign_id: str, state: PlayState, *, persisted: bool = False) -> None:
        self.campaign_id = campaign_id
        self.state = state
        self.persisted = persisted

    # ------------------------------------------------------------- factory
    @classmethod
    def load(cls, db: Session, campaign_id: str) -> PlaySession:
        """Read the campaign's state; if absent, return a *seeded, unpersisted*
        session (a GET must not write) that persists on first store()."""
        row = db.get(PlayStateRow, campaign_id)
        if row is None:
            return cls(campaign_id, seeded_state(), persisted=False)
        return cls(campaign_id, PlayState.from_json(row.state_json), persisted=True)

    @classmethod
    def seed_for(cls, db: Session, campaign_id: str) -> PlaySession:
        """Create (or fetch) the initial play state for a fresh campaign."""
        session = cls.load(db, campaign_id)
        if not session.persisted:
            session.store(db)
        return session

    # ------------------------------------------------------------ persist
    def store(self, db: Session) -> None:
        row = db.get(PlayStateRow, self.campaign_id)
        payload = self.state.to_json()
        if row is None:
            row = PlayStateRow(campaign_id=self.campaign_id, state_json=payload)
            db.add(row)
        else:
            row.state_json = payload
        # Mirror structured NPC memories into the npc_memories table (idempotent;
        # the save file stays authoritative, the table is for querying beyond it).
        sync_npc_memories(db, self.campaign_id, self.state)
        db.commit()
        self.persisted = True

    def checkpoint(self, db: Session, kind: str) -> str | None:
        """Commit state, then write an autosave for a state-changing beat.

        Returns the checkpoint name used (or None when the beat needs none).
        """
        checkpoint = CHECKPOINT_BY_KIND.get(kind)
        self.store(db)
        if checkpoint:
            autosave(db, campaign_id=self.campaign_id, checkpoint=checkpoint)
            event_log.append(
                GameEvent(
                    kind=EventKind.SAVE_CREATED,
                    campaign_id=self.campaign_id,
                    payload={"checkpoint": checkpoint, "slot": "autosave"},
                )
            )
        return checkpoint

    # -------------------------------------------------------- idempotency
    def recall_action(self, db: Session, key: str | None) -> dict | None:
        """Return a previous response for this Idempotency-Key, if any."""
        if not key:
            return None
        row = (
            db.query(PlayActionRow)
            .filter(
                PlayActionRow.campaign_id == self.campaign_id,
                PlayActionRow.idempotency_key == key,
            )
            .first()
        )
        if row is None:
            return None
        try:
            return json.loads(row.response_json)
        except (TypeError, ValueError):
            return None

    def remember_action(self, db: Session, key: str | None, response: dict) -> None:
        """Record a response against its Idempotency-Key (best-effort)."""
        if not key:
            return
        db.add(
            PlayActionRow(
                campaign_id=self.campaign_id,
                idempotency_key=key[:120],
                response_json=json.dumps(response, ensure_ascii=False),
            )
        )
        db.commit()
