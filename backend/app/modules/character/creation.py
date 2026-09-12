"""6-stage character creation (Stream A).

Stages: 1 identity, 2 background, 3 drives, 4 attributes, 5 skills/traits,
6 review. Background grants history (skills/contacts/knowledge/equipment/hooks),
never gates options: any character may take any attribute/skill/trait/drive.
Resolve pool: spend to reroll a check, resist fear, push through exhaustion,
or activate an ability.
"""
from __future__ import annotations

import json
from dataclasses import dataclass, field

STAGES: tuple[str, ...] = ("identity", "background", "drives", "attributes", "skills_traits", "review")

AGE_RANGES: tuple[str, ...] = ("youth", "adult", "middle-aged", "elder")

BACKGROUNDS: dict[str, dict] = {
    "Former Soldier": {
        "skills": ["Athletics", "Intimidation"],
        "contacts": ["old war buddy"],
        "knowledge": ["military protocol"],
        "equipment": ["worn uniform", "short sword"],
        "hooks": ["a battle you never speak of"],
    },
    "Traveling Scholar": {
        "skills": ["Lore", "Investigation"],
        "contacts": ["a librarian in Ravenford"],
        "knowledge": ["old tongues"],
        "equipment": ["satchel of books", "ink and quills"],
        "hooks": ["a stolen manuscript"],
    },
    "Street Urchin": {
        "skills": ["Stealth", "Deception"],
        "contacts": ["a fence at the docks"],
        "knowledge": ["city back alleys"],
        "equipment": ["lockpicks", "patched cloak"],
        "hooks": ["a debt to the wrong people"],
    },
    "Village Healer": {
        "skills": ["Survival", "Concentration"],
        "contacts": ["a grateful midwife"],
        "knowledge": ["herbal remedies"],
        "equipment": ["healer's kit", "herb pouch"],
        "hooks": ["a patient who never woke"],
    },
}

RESOLVE_USES: tuple[str, ...] = ("reroll_check", "resist_fear", "push_exhaustion", "activate_ability")
BASE_RESOLVE_MAX = 2


@dataclass
class CreationState:
    stage: int = 1  # next stage to complete (1..6); 7 == done
    data: dict = field(default_factory=dict)

    def to_json(self) -> str:
        return json.dumps({"stage": self.stage, "data": self.data})

    @classmethod
    def from_json(cls, raw: str) -> CreationState:
        try:
            obj = json.loads(raw or "{}")
        except json.JSONDecodeError:
            obj = {}
        return cls(stage=int(obj.get("stage", 1)), data=dict(obj.get("data", {})))


def validate_identity(payload: dict) -> dict:
    name = str(payload.get("name", "")).strip()
    if not name:
        raise ValueError("identity.name is required")
    age_range = str(payload.get("age_range", "") or "")
    if age_range and age_range not in AGE_RANGES:
        raise ValueError(f"identity.age_range must be one of {list(AGE_RANGES)}")
    return {
        "name": name,
        "pronouns": str(payload.get("pronouns", "")),
        "appearance": str(payload.get("appearance", "")),
        "age_range": age_range,
        "homeland": str(payload.get("homeland", "")),
        "portrait_url": str(payload.get("portrait_url", "")),
    }


def validate_background(payload: dict) -> dict:
    bg = str(payload.get("background", ""))
    if bg not in BACKGROUNDS:
        raise ValueError(f"background must be one of {sorted(BACKGROUNDS)}")
    return {"background": bg, "grants": dict(BACKGROUNDS[bg])}


def validate_drives(payload: dict) -> dict:
    drives = payload.get("drives", [])
    if not isinstance(drives, list) or len(drives) != 2 or not all(isinstance(d, str) and d.strip() for d in drives):
        raise ValueError("drives: pick exactly two motivations")
    if drives[0].strip() == drives[1].strip():
        raise ValueError("drives must be two distinct motivations")
    return {"drives": [d.strip() for d in drives], "resolve_max": BASE_RESOLVE_MAX}


def validate_attributes(payload: dict) -> dict:
    from app.modules.character.attributes import (
        ATTRIBUTES,
        MAX_ATTRIBUTE_VALUE,
        MIN_ATTRIBUTE_VALUE,
    )

    attrs = payload.get("attributes", {})
    if not isinstance(attrs, dict):
        raise TypeError("attributes must be a mapping")
    cleaned: dict[str, int] = {}
    for name in ATTRIBUTES:
        if name not in attrs:
            raise ValueError(f"attributes.{name} is required")
        try:
            v = int(attrs[name])
        except (TypeError, ValueError):
            raise ValueError(f"attributes.{name} must be an integer")
        if not (MIN_ATTRIBUTE_VALUE <= v <= MAX_ATTRIBUTE_VALUE):
            raise ValueError(f"attributes.{name} must be {MIN_ATTRIBUTE_VALUE}..{MAX_ATTRIBUTE_VALUE}")
        cleaned[name] = v
    return {"attributes": cleaned}


def validate_skills_traits(payload: dict) -> dict:
    skills = payload.get("skills", [])
    traits = payload.get("traits", [])
    if not isinstance(skills, list) or not all(isinstance(s, str) and s.strip() for s in skills):
        raise ValueError("skills must be a list of names")
    if not isinstance(traits, list) or not all(isinstance(t, str) for t in traits):
        raise ValueError("traits must be a list of names")
    # Background never locks options: no cross-check against background grants.
    return {"skills": [s.strip() for s in skills], "traits": [t.strip() for t in traits]}


STAGE_VALIDATORS = {
    1: validate_identity,
    2: validate_background,
    3: validate_drives,
    4: validate_attributes,
    5: validate_skills_traits,
}


def advance(state: CreationState, stage: int, payload: dict) -> CreationState:
    """Validate one stage payload and merge it; stages must complete in order."""
    if stage != state.stage or stage not in STAGE_VALIDATORS:
        raise ValueError(f"expected stage {state.stage}")
    cleaned = STAGE_VALIDATORS[stage](payload)
    state.data[STAGES[stage - 1]] = cleaned
    state.stage = stage + 1
    return state


def is_complete(state: CreationState) -> bool:
    return state.stage > len(STAGES)


def spend_resolve(current: int, use: str) -> int:
    if use not in RESOLVE_USES:
        raise ValueError(f"unknown resolve use {use!r}; expected one of {list(RESOLVE_USES)}")
    if current < 1:
        raise ValueError("no resolve remaining")
    return current - 1
