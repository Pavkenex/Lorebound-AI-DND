"""Six attributes, derived stats, check formula (Stream A).

Attributes: Might, Agility, Intellect, Awareness, Will, Presence.
Modifier: (value - 10) // 2. Check total: d20 + attribute modifier +
skill rank + situational modifiers. No special-casing per attribute.
"""
from __future__ import annotations

ATTRIBUTES: tuple[str, ...] = ("Might", "Agility", "Intellect", "Awareness", "Will", "Presence")

# Example attribute -> skill mappings (GDD section 10).
ATTRIBUTE_SKILLS: dict[str, list[str]] = {
    "Might": ["Athletics", "Intimidation", "Smithing"],
    "Agility": ["Stealth", "Acrobatics", "Archery"],
    "Intellect": ["Lore", "Investigation", "Alchemy"],
    "Awareness": ["Perception", "Survival", "Tracking"],
    "Will": ["Resolve", "Concentration", "Resist Fear"],
    "Presence": ["Persuasion", "Deception", "Performance"],
}

DEFAULT_ATTRIBUTE_VALUE = 10
MIN_ATTRIBUTE_VALUE = 1
MAX_ATTRIBUTE_VALUE = 20


def attribute_modifier(value: int) -> int:
    """Standard modifier curve shared by all six attributes."""
    return (int(value) - 10) // 2


def check_total(*, roll: int, attribute_value: int, skill_rank: int = 0, situational: int = 0) -> int:
    """Check formula: d20 + attr mod + skill rank + situational. No special cases."""
    return int(roll) + attribute_modifier(attribute_value) + int(skill_rank) + int(situational)


def derived_max_hp(might: int, will: int, level: int = 1) -> int:
    """HP baseline: 10 + Might mod*2 + Will mod + (level-1)*2, minimum 4."""
    return max(4, 10 + attribute_modifier(might) * 2 + attribute_modifier(will) + (int(level) - 1) * 2)


def derived_max_stamina(might: int, agility: int, level: int = 1) -> int:
    """Stamina baseline: 6 + best(Might, Agility) mod + (level-1), minimum 2."""
    return max(2, 6 + max(attribute_modifier(might), attribute_modifier(agility)) + (int(level) - 1))


def apply_attributes_summary(attributes: dict[str, int], level: int = 1) -> dict[str, int]:
    """Convenience: modifiers + derived pools for a full attribute block."""
    mods = {name: attribute_modifier(attributes.get(name, DEFAULT_ATTRIBUTE_VALUE)) for name in ATTRIBUTES}
    out = {f"{k}_mod": v for k, v in mods.items()}
    out["hp_max"] = derived_max_hp(
        attributes.get("Might", DEFAULT_ATTRIBUTE_VALUE),
        attributes.get("Will", DEFAULT_ATTRIBUTE_VALUE),
        level,
    )
    out["stamina_max"] = derived_max_stamina(
        attributes.get("Might", DEFAULT_ATTRIBUTE_VALUE),
        attributes.get("Agility", DEFAULT_ATTRIBUTE_VALUE),
        level,
    )
    return out
