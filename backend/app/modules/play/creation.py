"""Character creation over HTTP: state, stage advance, commit (6-stage flow).

The creation module owns validation; this service persists the draft in the
campaign's play state and, on commit, applies the finished character to both
the DB rows (Character + CharacterAttribute) and the live play sheet.
"""
from __future__ import annotations

import json
from typing import Any

from sqlalchemy.orm import Session

from app.modules.character.attributes import (
    ATTRIBUTES,
    MAX_ATTRIBUTE_VALUE,
    MIN_ATTRIBUTE_VALUE,
    derived_max_hp,
    derived_max_stamina,
)
from app.modules.character.creation import (
    AGE_RANGES,
    BACKGROUNDS,
    STAGES,
    CreationState,
    advance,
    is_complete,
    review,
)
from app.modules.character.models import Character, CharacterAttribute
from app.modules.play.state import PlayState, refresh_prologue_opening
from app.modules.progression.skills import SKILL_REGISTRY

#: Frontend play-sheet attribute names mapped from the creation attribute set.
_SHEET_ATTRIBUTES = {
    "Might": "Might",
    "Agility": "Finesse",
    "Intellect": "Wits",
    "Will": "Resolve",
    "Presence": "Presence",
}

SKILL_NAMES: tuple[str, ...] = tuple(SKILL_REGISTRY.keys())
SUGGESTED_TRAITS: tuple[str, ...] = (
    "Night-eyed", "Soft-footed", "Iron stomach", "Quick hands", "Keen hearing",
    "Keeps every promise twice", "Weather-wise", "Light sleeper",
)


def options() -> dict[str, Any]:
    """Catalogs the wizard needs (no campaign required)."""
    return {
        "stages": list(STAGES),
        "backgrounds": [{"name": name, "grants": dict(grants)} for name, grants in BACKGROUNDS.items()],
        "age_ranges": list(AGE_RANGES),
        "attributes": {
            "names": list(ATTRIBUTES),
            "min": MIN_ATTRIBUTE_VALUE,
            "max": MAX_ATTRIBUTE_VALUE,
            "default": 8,
            "pool": 60,  # total points across the six attributes
        },
        "skills": list(SKILL_NAMES),
        "traits": list(SUGGESTED_TRAITS),
    }


def get_creation(state: PlayState) -> dict[str, Any]:
    draft = _draft(state)
    return {
        "stage": draft.stage,
        "complete": is_complete(draft),
        "applied": bool(state.pc.get("created")),
        "data": draft.data,
    }


def _draft(state: PlayState) -> CreationState:
    raw = state.creation or {}
    return CreationState.from_json(json.dumps(raw)) if raw else CreationState()


def advance_creation(state: PlayState, stage: int, payload: dict) -> dict[str, Any]:
    """Validate + merge one stage; store the draft in the play state."""
    draft = _draft(state)
    if is_complete(draft) and state.pc.get("created"):
        raise ValueError("character already created")
    draft = advance(draft, stage, payload)
    state.creation = json.loads(draft.to_json())
    return get_creation(state)


def apply_creation(db: Session, campaign_id: str, state: PlayState) -> dict[str, Any]:
    """Commit the reviewed draft: DB rows + live play sheet."""
    draft = _draft(state)
    if draft.stage == len(STAGES):
        draft = review(draft)  # the commit IS the review acceptance
    if not is_complete(draft):
        raise ValueError("creation is not complete; finish all six stages first")
    data = draft.data
    if state.pc.get("created"):
        raise ValueError("character already created")
    state.creation = json.loads(draft.to_json())

    identity = data.get("identity", {})
    background = data.get("background", {})
    drives = data.get("drives", {})
    attributes = dict(data.get("attributes", {}).get("attributes", {}))
    skills_traits = data.get("skills_traits", {})
    grants = background.get("grants", {}) or {}

    might = int(attributes.get("Might", 8))
    will = int(attributes.get("Will", 8))
    agility = int(attributes.get("Agility", 8))
    hp_max = derived_max_hp(might, will, level=1)
    stamina_max = derived_max_stamina(might, agility, level=1)
    resolve_max = int(drives.get("resolve_max", 2))

    # -- DB rows ------------------------------------------------------------
    row = (
        db.query(Character)
        .filter(Character.campaign_id == campaign_id)
        .first()
    )
    if row is None:
        row = Character(campaign_id=campaign_id, name=identity.get("name", "the hero"))
        db.add(row)
        db.flush()
    row.name = identity.get("name", row.name)
    row.pronouns = identity.get("pronouns", "")
    row.appearance = identity.get("appearance", "")
    row.age_range = identity.get("age_range", "")
    row.homeland = identity.get("homeland", "")
    row.background = background.get("background", "")
    row.drives_json = json.dumps(drives.get("drives", []))
    row.hp_current = row.hp_max = hp_max
    row.stamina_current = row.stamina_max = stamina_max
    row.resolve_current = row.resolve_max = resolve_max
    row.creation_stage = len(STAGES)
    row.creation_json = json.dumps(data)
    for name, value in attributes.items():
        attr = (
            db.query(CharacterAttribute)
            .filter(
                CharacterAttribute.campaign_id == campaign_id,
                CharacterAttribute.character_id == row.id,
                CharacterAttribute.name == name,
            )
            .first()
        )
        if attr is None:
            db.add(
                CharacterAttribute(
                    campaign_id=campaign_id, character_id=row.id, name=name, value=int(value)
                )
            )
        else:
            attr.value = int(value)

    # -- live play sheet -----------------------------------------------------
    pc = state.pc
    pc["name"] = identity.get("name", pc.get("name", "the hero"))
    pc["epithet"] = f"the {background.get('background', 'wanderer')}"
    pc["attributes"] = {
        sheet_name: int(attributes.get(src, 8))
        for src, sheet_name in _SHEET_ATTRIBUTES.items()
    }
    pc["hp"] = {"cur": hp_max, "max": hp_max}
    pc["stamina"] = {"cur": stamina_max, "max": stamina_max}
    pc["resolve"] = {"cur": resolve_max, "max": resolve_max}
    pc["drives"] = list(drives.get("drives", []))
    pc["traits"] = list(skills_traits.get("traits", []))
    equipment = list(pc.get("equipment", []))
    for item in grants.get("equipment", []):
        if item not in equipment:
            equipment.append(item)
    pc["equipment"] = equipment
    pc["background"] = (
        f"{identity.get('name')} came to the road from {identity.get('homeland') or 'the vale'}, "
        f"{background.get('background', 'a wanderer')} by trade — "
        f"{'; '.join(grants.get('hooks', ['a story still unfolding']))}."
    )
    pc["created"] = True
    # The chronicle may already be open on the prologue's road: the arrival
    # text is the player's own sheet, so it is recomposed the moment it lands.
    refresh_prologue_opening(state)
    db.commit()
    return {
        "applied": True,
        "character": pc,
        "level": row.level,
        "hp": hp_max,
        "stamina": stamina_max,
        "resolve": resolve_max,
    }
