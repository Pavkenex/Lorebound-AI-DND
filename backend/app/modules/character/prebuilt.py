"""Standard prebuilt hero sheets (SRD-flavoured archetypes) for new games.

Every sheet is built from the same catalogs and the same stage validators the
6-stage wizard uses (``advance``/``review``), so a prebuilt can never contain
an option a custom character could not take. Players either take one of these
or forge their own.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from app.modules.character.creation import (
    BACKGROUNDS,
    CreationState,
    advance,
    review,
)

#: Attribute pool budget the wizard displays; prebuilt sheets stay within it.
ATTRIBUTE_POOL = 60


@dataclass(frozen=True)
class PrebuiltHero:
    """One pickable standard hero: identity + the five creation stages."""

    id: str
    name: str
    klass: str
    blurb: str
    pronouns: str
    age_range: str
    homeland: str
    appearance: str
    background: str
    drives: tuple[str, str]
    attributes: dict[str, int]
    skills: tuple[str, ...]
    traits: tuple[str, ...]

    def stage_payloads(self) -> dict[int, dict[str, Any]]:
        return {
            1: {
                "name": self.name,
                "pronouns": self.pronouns,
                "age_range": self.age_range,
                "homeland": self.homeland,
                "appearance": self.appearance,
            },
            2: {"background": self.background},
            3: {"drives": list(self.drives)},
            4: {"attributes": dict(self.attributes)},
            5: {"skills": list(self.skills), "traits": list(self.traits)},
        }

    def build_creation_state(self) -> CreationState:
        """Advance a fresh draft through all six stages via the real validators."""
        state = CreationState()
        for stage, payload in self.stage_payloads().items():
            state = advance(state, stage, payload)
        return review(state)

    def to_doc(self) -> dict[str, Any]:
        """Client-facing card + sheet summary."""
        return {
            "id": self.id,
            "name": self.name,
            "class": self.klass,
            "blurb": self.blurb,
            "pronouns": self.pronouns,
            "age_range": self.age_range,
            "homeland": self.homeland,
            "appearance": self.appearance,
            "background": self.background,
            "background_grants": dict(BACKGROUNDS.get(self.background, {})),
            "drives": list(self.drives),
            "attributes": dict(self.attributes),
            "skills": list(self.skills),
            "traits": list(self.traits),
        }


PREBUILTS: tuple[PrebuiltHero, ...] = (
    PrebuiltHero(
        id="knight",
        name="Ser Aldric Vane",
        klass="Knight",
        blurb="A seasoned blade who mustered out of the king's war; holds the line so others don't have to.",
        pronouns="he/him",
        age_range="middle-aged",
        homeland="Ravenford",
        appearance="Broad-shouldered, grey at the temples, armour kept in better repair than his temper.",
        background="Former Soldier",
        drives=("Repay a debt of honour", "Keep the road safe for the unarmed"),
        attributes={"Might": 16, "Agility": 8, "Intellect": 8, "Awareness": 10, "Will": 12, "Presence": 6},
        skills=("Swordsmanship", "Athletics", "Insight"),
        traits=("Iron stomach", "Keeps every promise twice"),
    ),
    PrebuiltHero(
        id="rogue",
        name="Wren Ashgrove",
        klass="Rogue",
        blurb="Raised by the docks, quick with locks and quicker with exits; trusts a ledger more than a face.",
        pronouns="she/her",
        age_range="adult",
        homeland="the docks of Ravenford",
        appearance="Cropped dark hair, a patched cloak that never quite fits, hands that are never still.",
        background="Street Urchin",
        drives=("Settle an old score", "Never be owned again"),
        attributes={"Might": 8, "Agility": 16, "Intellect": 10, "Awareness": 12, "Will": 6, "Presence": 8},
        skills=("Stealth", "Lockpicking", "Persuasion"),
        traits=("Soft-footed", "Quick hands"),
    ),
    PrebuiltHero(
        id="wizard",
        name="Maelis Orr",
        klass="Wizard",
        blurb="A scholar chasing a stolen manuscript and the cold lights over the northern road.",
        pronouns="they/them",
        age_range="middle-aged",
        homeland="the university town of Highmere",
        appearance="Ink-stained cuffs, spectacles cracked at one corner, a satchel of half-read books.",
        background="Traveling Scholar",
        drives=("Recover the stolen manuscript", "Understand what the lights want"),
        attributes={"Might": 6, "Agility": 8, "Intellect": 16, "Awareness": 10, "Will": 12, "Presence": 8},
        skills=("Lore", "Insight", "Medicine"),
        traits=("Keen hearing", "Night-eyed"),
    ),
    PrebuiltHero(
        id="cleric",
        name="Sister Bramble",
        klass="Cleric",
        blurb="A field chaplain of the Quiet Order; carries mercy in one hand and a ledger of the dead in the other.",
        pronouns="she/her",
        age_range="adult",
        homeland="the Quiet Order's cloister",
        appearance="Grey habit cut for walking, healer's satchel, eyes that miss nothing at a sickbed.",
        background="Village Healer",
        drives=("Atone for a patient who never woke", "Walk where the need is greatest"),
        attributes={"Might": 6, "Agility": 8, "Intellect": 10, "Awareness": 10, "Will": 16, "Presence": 10},
        skills=("Medicine", "Insight", "Persuasion"),
        traits=("Light sleeper", "Weather-wise"),
    ),
    PrebuiltHero(
        id="ranger",
        name="Kestrel Dune",
        klass="Ranger",
        blurb="A road warden of the Greywood verge; reads weather, tracks, and lies with the same eye.",
        pronouns="she/her",
        age_range="adult",
        homeland="the Greywood verge",
        appearance="Trail-worn cloak, a yew bow strung for rain, mud to the knee and no complaint about it.",
        background="Road Warden",
        drives=("Chart the northern road", "See every wayfarer home"),
        attributes={"Might": 10, "Agility": 12, "Intellect": 6, "Awareness": 16, "Will": 10, "Presence": 6},
        skills=("Tracking", "Foraging", "Archery"),
        traits=("Weather-wise", "Night-eyed"),
    ),
    PrebuiltHero(
        id="bard",
        name="Lior Fenn",
        klass="Bard",
        blurb="A wandering skald who trades songs for suppers and secrets; the road is his stage and his debt.",
        pronouns="he/him",
        age_range="adult",
        homeland="a wagon that never stopped",
        appearance="A patched lute on his back, threadbare finery, a smile kept for rooms with locked doors.",
        background="Wandering Player",
        drives=("Collect every story worth keeping", "Outrun a patron's patience"),
        attributes={"Might": 6, "Agility": 12, "Intellect": 10, "Awareness": 8, "Will": 8, "Presence": 16},
        skills=("Persuasion", "Insight", "Lore"),
        traits=("Quick hands", "Keeps every promise twice"),
    ),
)

_BY_ID: dict[str, PrebuiltHero] = {hero.id: hero for hero in PREBUILTS}


def get_hero(hero_id: str) -> PrebuiltHero:
    """Look up a prebuilt by id; raises ``KeyError`` for unknown ids."""
    return _BY_ID[(hero_id or "").strip().lower()]


def list_heroes() -> list[dict[str, Any]]:
    return [hero.to_doc() for hero in PREBUILTS]


def prebuilt_creation_state(hero_id: str) -> CreationState:
    """The completed creation draft a prebuilt applies (validated by design)."""
    return get_hero(hero_id).build_creation_state()


__all__ = [
    "ATTRIBUTE_POOL",
    "PREBUILTS",
    "PrebuiltHero",
    "get_hero",
    "list_heroes",
    "prebuilt_creation_state",
]
