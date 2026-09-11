"""Magic schools & approved techniques. The AI cannot invent powers mid-scene.

Every magical effect resolves through a Technique in APPROVED_TECHNIQUES.
Casting an unknown id raises UnknownTechniqueError — the caller must surface
that failure, never hallucinate the effect.
"""
from __future__ import annotations

from enum import Enum

from pydantic import BaseModel


class School(str, Enum):
    ELEMENTALISM = "Elementalism"
    WARDING = "Warding"
    RESTORATION = "Restoration"
    ILLUSION = "Illusion"
    SPIRIT = "Spirit"
    TRANSFORMATION = "Transformation"


class Technique(BaseModel):
    id: str
    name: str
    school: School
    stamina_cost: int = 20
    damage: int = 0
    healing: int = 0
    # Farthest range band this technique can reach, as a string.
    reach: str = "Far"
    description: str = ""


class UnknownTechniqueError(ValueError):
    pass


def _t(id: str, name: str, school: School, **kw) -> Technique:
    return Technique(id=id, name=name, school=school, **kw)


APPROVED_TECHNIQUES: dict[str, Technique] = {
    t.id: t for t in [
        _t("emberbolt", "Emberbolt", School.ELEMENTALISM, damage=10,
           description="A hurled bolt of fire."),
        _t("frostbind", "Frostbind", School.ELEMENTALISM, damage=7,
           description="Cold that slows and bites."),
        _t("ward_sigil", "Ward Sigil", School.WARDING,
           description="A glowing sigil that turns one strike aside."),
        _t("bulwark", "Bulwark", School.WARDING,
           description="A broad shield-wall of force over an ally."),
        _t("mend_wounds", "Mend Wounds", School.RESTORATION, healing=10,
           description="Knits flesh; closes cuts, not curses."),
        _t("purge_fever", "Purge Fever", School.RESTORATION, healing=4,
           description="Burns poison and fever out of the blood."),
        _t("veil", "Veil", School.ILLUSION,
           description="Bends light; the veiled is easily overlooked."),
        _t("phantasm", "Phantasm", School.ILLUSION,
           description="A false horror only the target can see."),
        _t("speak_with_echo", "Speak with Echo", School.SPIRIT,
           description="Hears the last words of the recently dead."),
        _t("wisp_guide", "Wisp Guide", School.SPIRIT,
           description="A pale wisp lights hidden paths and spirits."),
        _t("oakskin", "Oakskin", School.TRANSFORMATION,
           description="Skin takes on bark; blows land softer."),
        _t("hawk_eyes", "Hawk Eyes", School.TRANSFORMATION,
           description="Eyes reshape for far sight in dark and distance."),
    ]
}


def get_technique(technique_id: str) -> Technique:
    try:
        return APPROVED_TECHNIQUES[technique_id]
    except KeyError:
        raise UnknownTechniqueError(
            f"'{technique_id}' is not an approved technique. "
            "No new powers may be invented mid-scene."
        ) from None


def techniques_for_school(school: School) -> list[Technique]:
    return [t for t in APPROVED_TECHNIQUES.values() if t.school == school]


class TechniqueDefinition(BaseModel):
    """An approved definition GMs can add between sessions (never mid-scene)."""

    id: str
    name: str
    school: School
    stamina_cost: int = 20
    damage: int = 0
    healing: int = 0
    reach: str = "Far"
    description: str = ""


def approve_technique(definition: TechniqueDefinition) -> Technique:
    """Register a new approved definition out-of-scene. Mid-scene invention
    stays forbidden: cast() only reads APPROVED_TECHNIQUES, and this function
    is the only writer studios should call during prep."""
    technique = Technique(**definition.model_dump())
    APPROVED_TECHNIQUES[technique.id] = technique
    return technique
