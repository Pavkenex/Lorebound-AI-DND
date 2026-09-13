"""NPC memory service: slug mapping, the fixture roster, and state -> table sync.

Slugs are the stable keys used inside :class:`PlayState` (``npc_memory_log``);
the ``npc_memories`` table is the durable mirror used for querying beyond the
save file. The engine stays pure (state only) — this module is called from the
persistence path (:meth:`PlaySession.store`), so no DB is needed mid-beat.
"""
from __future__ import annotations

from sqlalchemy import inspect
from sqlalchemy.orm import Session

from app.modules.campaign.npc import NPC, NPCMemory

#: The five authored Ravenford characters the slice tracks memories for.
ROSTER: tuple[dict[str, str], ...] = (
    {"slug": "marla", "name": "Marla Voss", "role": "Innkeeper of the Lantern"},
    {"slug": "borin", "name": "Borin", "role": "Mercenary drinking at the Lantern"},
    {"slug": "sella", "name": "Sella Voss", "role": "Guild silver factor"},
    {"slug": "tomm", "name": "Tomm Ash", "role": "Peddler"},
    {"slug": "anselm", "name": "Brother Anselm", "role": "Porter of the Old Monastery"},
)

_HONORIFICS = {"brother", "sister", "sir", "lady", "mother", "father"}


def npc_slug(name: str) -> str:
    """Stable slug for a display name.

    ``Marla Voss`` -> ``marla``; ``Brother Anselm`` -> ``anselm``.
    """
    parts = [p for p in str(name).strip().split() if p]
    if not parts:
        return ""
    first = parts[0]
    if first.lower() in _HONORIFICS and len(parts) > 1:
        first = parts[1]
    return first.lower()


def ensure_npcs(db: Session, campaign_id: str) -> dict[str, NPC]:
    """Create any missing roster rows for this campaign; returns slug -> NPC."""
    existing = {n.name: n for n in db.query(NPC).filter(NPC.campaign_id == campaign_id)}
    out: dict[str, NPC] = {}
    for entry in ROSTER:
        npc = existing.get(entry["name"])
        if npc is None:
            npc = NPC(campaign_id=campaign_id, name=entry["name"], role=entry["role"])
            db.add(npc)
            db.flush()
        out[entry["slug"]] = npc
    return out


def sync_npc_memories(db: Session, campaign_id: str, state) -> int:
    """Mirror new ``PlayState.npc_memory_log`` entries into ``npc_memories``.

    Idempotent: a row is inserted only when that (npc, text) pair is not
    already stored. Returns the number of rows added.
    """
    if not inspect(db.get_bind()).has_table("npc_memories"):
        return 0  # partial test schema: nothing to mirror into
    npcs = ensure_npcs(db, campaign_id)
    log = getattr(state, "npc_memory_log", None) or []
    if not log:
        return 0
    seen = {
        (row.npc_id, row.memory)
        for row in db.query(NPCMemory).filter(NPCMemory.campaign_id == campaign_id)
    }
    added = 0
    for entry in log:
        npc = npcs.get(str(entry.get("npc", "")))
        text = str(entry.get("text", "")).strip()
        if npc is None or not text or (npc.id, text) in seen:
            continue
        db.add(
            NPCMemory(
                campaign_id=campaign_id,
                npc_id=npc.id,
                memory=text,
                kind=str(entry.get("kind", "")),
                sentiment=int(entry.get("sentiment", 0)),
                salience=int(entry.get("salience", 2)),
                day=int(entry.get("day", 1)),
                hour=int(entry.get("hour", 0)),
            )
        )
        seen.add((npc.id, text))
        added += 1
    return added
