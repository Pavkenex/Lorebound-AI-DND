"""Specializations at mastery milestones (GDD §16).

Two characters with identical base skill play measurably differently once
they choose different paths.
"""
from __future__ import annotations

from dataclasses import dataclass

from app.modules.progression.skills import MasteryTier, SkillProgress


@dataclass(frozen=True)
class Specialization:
    name: str
    skill: str
    requires: MasteryTier
    description: str
    #: Additive modifiers keyed by combat facet.
    modifiers: dict[str, int]


SPEC_TREES: dict[str, list[Specialization]] = {
    "Swordsmanship": [
        Specialization(
            "Duelist", "Swordsmanship", MasteryTier.SKILLED,
            "Unmatched in single combat; thrives when facing one foe alone.",
            {"duel": 6, "riposte": 2, "guard": 0, "cleave": -2},
        ),
        Specialization(
            "Guardian", "Swordsmanship", MasteryTier.SKILLED,
            "A shield for others; excels at holding ground and protecting allies.",
            {"duel": 0, "riposte": 0, "guard": 6, "cleave": -2},
        ),
        Specialization(
            "Greatblade", "Swordsmanship", MasteryTier.SKILLED,
            "Sweeping heavy cuts that carve through groups of enemies.",
            {"duel": -2, "riposte": 0, "guard": 0, "cleave": 6},
        ),
        Specialization(
            "Counterfighter", "Swordsmanship", MasteryTier.SKILLED,
            "Turns enemy aggression back on them with punishing ripostes.",
            {"duel": 0, "riposte": 6, "guard": 2, "cleave": -2},
        ),
    ],
}

_ORDER = [t for t in MasteryTier]


def available_specializations(skill: str, tier: MasteryTier) -> list[Specialization]:
    return [s for s in SPEC_TREES.get(skill, []) if _ORDER.index(tier) >= _ORDER.index(s.requires)]


def choose_specialization(progress: SkillProgress, spec_name: str) -> Specialization:
    """Lock in a milestone path. Requires the milestone tier; one per skill."""
    if progress.specialization is not None:
        raise ValueError(f"{progress.name} already specialised as {progress.specialization}")
    for spec in SPEC_TREES.get(progress.name, []):
        if spec.name == spec_name:
            if _ORDER.index(progress.tier) < _ORDER.index(spec.requires):
                raise ValueError(
                    f"{spec_name} requires {spec.requires.value} (currently {progress.tier.value})"
                )
            progress.specialization = spec.name
            return spec
    raise ValueError(f"Unknown specialization {spec_name!r} for {progress.name}")


def apply_specialization(base: dict[str, int], spec: Specialization) -> dict[str, int]:
    """Apply a specialization's modifiers to identical base stats."""
    out = dict(base)
    for facet, delta in spec.modifiers.items():
        out[facet] = out.get(facet, 0) + delta
    return out
