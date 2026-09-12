"""Campaign service: creation from seed, hidden truths, save/load + autosave (Stream A).

Resolve-then-narrate rule: resolved state is committed to the DB (and an
autosave checkpoint written) BEFORE narration runs, so a narration failure
can never lose a resolved action or corrupt the save.
"""
from __future__ import annotations

import json
from datetime import UTC, datetime

from sqlalchemy.orm import Session

from app.core.events import EventKind, GameEvent, event_log
from app.modules.campaign.models import Campaign, CampaignSummary, GameTime, SaveGame
from app.modules.campaign.story import Scene
from app.modules.campaign.world import Location, Region, World, WorldFact

AUTOSAVE_CHECKPOINTS: tuple[str, ...] = (
    "scene_transition",
    "combat_resolution",
    "inventory_change",
    "important_dialogue",
    "travel",
    "rest",
)

# Seed definitions: each seed writes its hidden truths as canonical WorldFacts
# with restricted visibility BEFORE play begins.
SEEDS: dict[str, dict] = {
    "hollow_crown": {
        "name": "The Hollow Crown",
        "world_name": "Valdria",
        "region_name": "The Hollow Vale",
        "location_name": "Ravenford",
        "hidden_truths": [
            {
                "subject_type": "faction",
                "subject_id": "monastery-cult",
                "predicate": "serves",
                "object": "an imprisoned spirit beneath the monastery",
                "truth_state": "true",
                "confidence": 1.0,
            },
            {
                "subject_type": "npc",
                "subject_id": "captain-varen",
                "predicate": "murdered",
                "object": "the previous magistrate",
                "truth_state": "true",
                "confidence": 1.0,
            },
        ],
        "public_facts": [
            {
                "subject_type": "location",
                "subject_id": "ravenford",
                "predicate": "ruled_by",
                "object": "Captain Varen, acting magistrate",
                "truth_state": "true",
                "confidence": 1.0,
            },
        ],
    },
    "ash_on_the_roads": {
        "name": "Ash on the Roads",
        "world_name": "Valdria",
        "region_name": "The Ashen Marches",
        "location_name": "Cinder Post",
        "hidden_truths": [
            {
                "subject_type": "npc",
                "subject_id": "caravan-master",
                "predicate": "smuggles_for",
                "object": "the cinder cult",
                "truth_state": "true",
                "confidence": 0.9,
            },
        ],
        "public_facts": [
            {
                "subject_type": "location",
                "subject_id": "cinder-post",
                "predicate": "known_for",
                "object": "ash storms every third night",
                "truth_state": "rumor",
                "confidence": 0.5,
            },
        ],
    },
    "beneath_ravenford": {
        "name": "Beneath Ravenford",
        "world_name": "Valdria",
        "region_name": "Ravenford Depths",
        "location_name": "The Old Watchtower",
        "hidden_truths": [
            {
                "subject_type": "location",
                "subject_id": "old-watchtower",
                "predicate": "hides",
                "object": "a sealed stair to the undercity",
                "truth_state": "true",
                "confidence": 1.0,
            },
        ],
        "public_facts": [],
    },
}


def _utcnow() -> datetime:
    return datetime.now(UTC)


def create_campaign_from_seed(db: Session, *, owner_user_id: str, name: str, seed_key: str) -> Campaign:
    """Create a campaign and write its hidden truths as restricted WorldFacts."""
    if seed_key not in SEEDS:
        raise ValueError(f"unknown seed {seed_key!r}; expected one of {sorted(SEEDS)}")
    seed = SEEDS[seed_key]
    campaign = Campaign(owner_user_id=owner_user_id, name=name, seed_key=seed_key, status="active")
    db.add(campaign)
    db.flush()

    world = World(campaign_id=campaign.id, name=seed["world_name"], description=f"Seed: {seed['name']}")
    db.add(world)
    db.flush()
    region = Region(
        campaign_id=campaign.id, world_id=world.id, name=seed["region_name"], description=""
    )
    db.add(region)
    db.flush()
    location = Location(
        campaign_id=campaign.id, region_id=region.id, name=seed["location_name"], description=""
    )
    db.add(location)
    db.flush()

    for truth in seed["hidden_truths"]:
        db.add(
            WorldFact(
                campaign_id=campaign.id,
                subject_type=truth["subject_type"],
                subject_id=truth["subject_id"],
                predicate=truth["predicate"],
                object=truth["object"],
                truth_state=truth.get("truth_state", "true"),
                confidence=truth.get("confidence", 1.0),
                visibility="hidden",  # restricted: narrator must never leak these
                source_event_id="campaign_seed",
            )
        )
    for fact in seed.get("public_facts", []):
        db.add(
            WorldFact(
                campaign_id=campaign.id,
                subject_type=fact["subject_type"],
                subject_id=fact["subject_id"],
                predicate=fact["predicate"],
                object=fact["object"],
                truth_state=fact.get("truth_state", "true"),
                confidence=fact.get("confidence", 1.0),
                visibility="public",
                source_event_id="campaign_seed",
            )
        )
    db.add(GameTime(campaign_id=campaign.id))
    db.add(CampaignSummary(campaign_id=campaign.id, text=""))
    scene = Scene(campaign_id=campaign.id, location_id=location.id, title="Arrival", state="active")
    db.add(scene)
    db.flush()
    campaign.current_scene_id = scene.id
    db.commit()
    db.refresh(campaign)
    return campaign


def list_campaigns(db: Session, *, owner_user_id: str) -> list[Campaign]:
    return db.query(Campaign).filter(Campaign.owner_user_id == owner_user_id).all()


def get_owned_campaign(db: Session, *, campaign_id: str, owner_user_id: str) -> Campaign | None:
    """Ownership-scoped fetch: returns None for other accounts' campaigns."""
    return (
        db.query(Campaign)
        .filter(Campaign.id == campaign_id, Campaign.owner_user_id == owner_user_id)
        .first()
    )


def visible_facts(db: Session, *, campaign_id: str, party_view: bool = False) -> list[WorldFact]:
    """Hidden truths are excluded unless explicitly requested (GM view)."""
    q = db.query(WorldFact).filter(WorldFact.campaign_id == campaign_id)
    if not party_view:
        q = q.filter(WorldFact.visibility != "hidden")
    return q.all()


# ---- save / load / autosave ----

def build_snapshot(db: Session, *, campaign_id: str) -> dict:
    """Collect resolved state into a JSON-serializable snapshot (no prose)."""
    from app.modules.play.models import PlayStateRow

    scenes = db.query(Scene).filter(Scene.campaign_id == campaign_id).all()
    facts = db.query(WorldFact).filter(WorldFact.campaign_id == campaign_id).all()
    clock = db.query(GameTime).filter(GameTime.campaign_id == campaign_id).first()
    summary = db.query(CampaignSummary).filter(CampaignSummary.campaign_id == campaign_id).first()
    play_row = db.get(PlayStateRow, campaign_id)
    try:
        play = json.loads(play_row.state_json) if play_row is not None else None
    except (TypeError, ValueError):
        play = None
    return {
        "campaign_id": campaign_id,
        "saved_at": _utcnow().isoformat(),
        "scenes": [{"id": s.id, "title": s.title, "state": s.state, "state_json": s.state_json} for s in scenes],
        "facts": [
            {
                "subject_type": f.subject_type,
                "subject_id": f.subject_id,
                "predicate": f.predicate,
                "object": f.object,
                "truth_state": f.truth_state,
                "visibility": f.visibility,
            }
            for f in facts
        ],
        "clock": {"day": clock.day, "hour": clock.hour, "minute": clock.minute} if clock else None,
        "summary": summary.text if summary else "",
        "play": play,
    }


def write_save(
    db: Session,
    *,
    campaign_id: str,
    slot: str = "manual",
    label: str = "",
    checkpoint: str = "manual",
) -> SaveGame:
    snapshot = build_snapshot(db, campaign_id=campaign_id)
    row = SaveGame(
        campaign_id=campaign_id,
        slot=slot,
        label=label or f"{slot} @ {snapshot['saved_at']}",
        checkpoint=checkpoint,
        snapshot=json.dumps(snapshot),
    )
    db.add(row)
    db.commit()
    db.refresh(row)
    event_log.append(
        GameEvent(kind=EventKind.SAVE_CREATED, campaign_id=campaign_id, payload={"save_id": row.id, "slot": slot})
    )
    return row


def autosave(db: Session, *, campaign_id: str, checkpoint: str) -> SaveGame:
    if checkpoint not in AUTOSAVE_CHECKPOINTS:
        raise ValueError(f"unknown checkpoint {checkpoint!r}")
    return write_save(db, campaign_id=campaign_id, slot="autosave", checkpoint=checkpoint)


def load_save(db: Session, *, save_id: str, owner_user_id: str) -> dict:
    """Load a save only if its campaign belongs to the requesting user."""
    row = db.query(SaveGame).filter(SaveGame.id == save_id).first()
    if row is None:
        raise LookupError("save not found")
    campaign = get_owned_campaign(db, campaign_id=row.campaign_id, owner_user_id=owner_user_id)
    if campaign is None:
        raise PermissionError("not your campaign")
    return json.loads(row.snapshot)


def resolve_then_narrate(
    db: Session,
    *,
    campaign_id: str,
    resolve: object,
    narrate: object,
    checkpoint: str = "scene_transition",
) -> dict:
    """Commit resolved mechanics first, then attempt narration.

    `resolve` is a zero-arg callable returning the resolved-state dict (it must
    flush/commit its own DB writes or mutate passed-in rows). `narrate` is a
    zero-arg callable returning prose. Narration failure never loses resolved
    state: an autosave is written after resolve and before narrate.
    """
    resolved = resolve()  # type: ignore[operator]
    db.commit()
    save = autosave(db, campaign_id=campaign_id, checkpoint=checkpoint)
    try:
        prose = narrate()  # type: ignore[operator]
    except Exception as exc:  # noqa: BLE001 - narration failure must never lose resolved state
        return {"resolved": resolved, "prose": None, "narration_error": str(exc), "save_id": save.id}
    return {"resolved": resolved, "prose": prose, "save_id": save.id}
