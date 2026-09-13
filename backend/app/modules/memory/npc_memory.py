"""NPC memory service: slug mapping, the fixture roster, and state -> table sync.

Slugs are the stable keys used inside :class:`PlayState` (``npc_memory_log``);
the ``npc_memories`` table is the durable mirror used for querying beyond the
save file. The engine stays pure (state only) — this module is called from the
persistence path (:meth:`PlaySession.store`), so no DB is needed mid-beat.
"""
from __future__ import annotations

import re

from sqlalchemy import inspect
from sqlalchemy.orm import Session

from app.modules.campaign.npc import NPC, NPCMemory, NPCRelationship

#: The five authored Ravenford characters the slice tracks memories for.
ROSTER: tuple[dict[str, str], ...] = (
    {"slug": "marla", "name": "Marla Voss", "role": "Innkeeper of the Lantern"},
    {"slug": "borin", "name": "Borin", "role": "Mercenary drinking at the Lantern"},
    {"slug": "sella", "name": "Sella Voss", "role": "Guild silver factor"},
    {"slug": "tomm", "name": "Tomm Ash", "role": "Peddler"},
    {"slug": "anselm", "name": "Brother Anselm", "role": "Porter of the Old Monastery"},
)

_HONORIFICS = {"brother", "sister", "sir", "lady", "mother", "father"}

#: How the player's text names each roster character besides their own name —
#: the alias table the routing and pipeline paths share (slice 4).
NAME_ALIASES: dict[str, tuple[str, ...]] = {
    "marla": ("marla", "innkeeper", "landlady", "innkeep"),
    "borin": ("borin", "mercenary", "sellsword"),
    "sella": ("sella", "factor", "scales"),
    "tomm": ("tomm", "peddler", "tinker"),
    "anselm": ("anselm", "porter", "monk", "brother"),
}


def named_npc(text: str, present: list[str] | None = None) -> str | None:
    """The roster slug the text names, or None.

    Aliases are matched on word boundaries ("the peddler" names Tomm). When
    ``present`` (display names or slugs) is given, only those characters can
    match — nobody answers to a shout in an empty room — and the earliest in
    the roster wins if several are named.
    """
    t = str(text or "").lower()
    allowed = None if present is None else {npc_slug(n) for n in present}
    for slug, aliases in NAME_ALIASES.items():
        if allowed is not None and slug not in allowed:
            continue
        for alias in aliases:
            if re.search(rf"\b{re.escape(alias)}\b", t):
                return slug
    return None


def _has_table(db: Session, table: str) -> bool:
    """True when ``table`` exists in the schema this session writes to.

    Inspected through the session's own connection: inspecting the *engine*
    checks a connection in and out of the pool, and on a shared connection
    (StaticPool in tests) that reset silently rolls back the session's open
    transaction — flushed writes vanish without an error.
    """
    return inspect(db.connection()).has_table(table)


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


def display_name(slug: str) -> str:
    """Short display name for a roster slug, for player-visible lines.

    ``marla`` -> ``Marla``; ``anselm`` -> ``Anselm`` (honorific dropped).
    Unknown slugs fall back to a title-cased reading of the slug itself.
    """
    for entry in ROSTER:
        if entry["slug"] == slug:
            parts = [p for p in entry["name"].split() if p and p.lower() not in _HONORIFICS]
            if parts:
                return parts[0]
            break
    return slug.replace("-", " ").capitalize()


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
    if not _has_table(db, "npc_memories"):
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


def sync_npc_relationships(db: Session, campaign_id: str, state) -> int:
    """Mirror ``PlayState.attitudes`` into ``npc_relationships``.

    One directed row per NPC toward the player character
    (``to_entity_id='pc'``), upserted: attitude and the last reason are written
    only when they changed. Idempotent — re-storing the same state writes
    nothing. Returns the number of rows inserted or updated.
    """
    if not _has_table(db, "npc_relationships"):
        return 0  # partial test schema: nothing to mirror into
    attitudes = getattr(state, "attitudes", None) or {}
    if not attitudes:
        return 0
    npcs = ensure_npcs(db, campaign_id)
    reasons = getattr(state, "attitude_reasons", None) or {}
    rows = {
        (row.from_npc_id, row.to_entity_id): row
        for row in db.query(NPCRelationship).filter(NPCRelationship.campaign_id == campaign_id)
    }
    changed = 0
    for slug, raw in attitudes.items():
        npc = npcs.get(str(slug))
        if npc is None:
            continue
        attitude = max(-100, min(100, int(raw)))
        note = str(reasons.get(slug, ""))
        row = rows.get((npc.id, "pc"))
        if row is None:
            db.add(
                NPCRelationship(
                    campaign_id=campaign_id,
                    from_npc_id=npc.id,
                    to_entity_id="pc",
                    attitude=attitude,
                    note=note,
                )
            )
            changed += 1
        elif row.attitude != attitude or row.note != note:
            row.attitude = attitude
            row.note = note
            changed += 1
    return changed
