"""The five vertical-slice skills. All terms are in-house (see docs/IP_GLOSSARY.md)."""

from dataclasses import dataclass, field


@dataclass(frozen=True)
class Skill:
    key: str
    name: str
    description: str
    example_uses: tuple[str, ...] = field(default_factory=tuple)


SKILLS: tuple[Skill, ...] = (
    Skill(
        key="persuasion",
        name="Persuasion",
        description="Win people over with honest talk, bargaining, and pleas.",
        example_uses=(
            "Talk Marla into sharing what she heard about the missing travelers.",
            "Bargain the guild factor down to a fair price.",
        ),
    ),
    Skill(
        key="intimidation",
        name="Intimidation",
        description="Frighten or pressure someone into backing down or talking.",
        example_uses=(
            "Stare down the drunk mercenary before a bar fight starts.",
            "Pressure the guild factor into admitting who he pays.",
        ),
    ),
    Skill(
        key="investigation",
        name="Investigation",
        description="Search rooms, spot clues, and piece together what happened.",
        example_uses=(
            "Inspect the Lantern Inn common room for signs of the missing travelers.",
            "Match the mud on a cloak to the Northern Road.",
        ),
    ),
    Skill(
        key="stealth",
        name="Stealth",
        description="Move unseen, pocket small items, and follow people unnoticed.",
        example_uses=(
            "Lift the storeroom key without Marla noticing.",
            "Tail the lantern-bearer from the monastery path.",
        ),
    ),
    Skill(
        key="swordsmanship",
        name="Swordsmanship",
        description="Fight with blades: bar brawls, road ambushes, duels.",
        example_uses=(
            "Drive off the roadside ambushers on the Northern Road.",
            "Win the Lantern Inn brawl without killing anyone.",
        ),
    ),
)

SKILL_KEYS: tuple[str, ...] = tuple(s.key for s in SKILLS)


def get_skill(key: str) -> Skill:
    """Return the skill with this key, or raise KeyError."""
    for skill in SKILLS:
        if skill.key == key:
            return skill
    raise KeyError(f"unknown skill: {key!r}")
