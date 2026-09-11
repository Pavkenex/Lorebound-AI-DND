"""Skill registry & mastery tiers (GDD §11-12).

Six categories, six mastery tiers with an internal numeric score and a
player-facing tier label.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum


class SkillCategory(str, Enum):
    COMBAT = "Combat"
    PHYSICAL = "Physical"
    SURVIVAL = "Survival"
    KNOWLEDGE = "Knowledge"
    SOCIAL = "Social"
    PRACTICAL = "Practical"


class MasteryTier(str, Enum):
    UNTRAINED = "Untrained"
    NOVICE = "Novice"
    COMPETENT = "Competent"
    SKILLED = "Skilled"
    EXPERT = "Expert"
    MASTER = "Master"


TIER_ORDER: list[MasteryTier] = [
    MasteryTier.UNTRAINED,
    MasteryTier.NOVICE,
    MasteryTier.COMPETENT,
    MasteryTier.SKILLED,
    MasteryTier.EXPERT,
    MasteryTier.MASTER,
]

#: XP required to *reach* each tier.
TIER_THRESHOLDS: dict[MasteryTier, int] = {
    MasteryTier.UNTRAINED: 0,
    MasteryTier.NOVICE: 200,
    MasteryTier.COMPETENT: 600,
    MasteryTier.SKILLED: 1200,
    MasteryTier.EXPERT: 2000,
    MasteryTier.MASTER: 3200,
}

#: Canonical skill registry: skill name -> category.
SKILL_REGISTRY: dict[str, SkillCategory] = {
    "Swordsmanship": SkillCategory.COMBAT,
    "Archery": SkillCategory.COMBAT,
    "Athletics": SkillCategory.PHYSICAL,
    "Stealth": SkillCategory.PHYSICAL,
    "Foraging": SkillCategory.SURVIVAL,
    "Tracking": SkillCategory.SURVIVAL,
    "Lore": SkillCategory.KNOWLEDGE,
    "Medicine": SkillCategory.KNOWLEDGE,
    "Persuasion": SkillCategory.SOCIAL,
    "Insight": SkillCategory.SOCIAL,
    "Lockpicking": SkillCategory.PRACTICAL,
    "Crafting": SkillCategory.PRACTICAL,
}


def tier_for_xp(xp: int) -> MasteryTier:
    """Highest tier whose threshold is met by ``xp``."""
    xp = max(0, xp)
    tier = MasteryTier.UNTRAINED
    for t in TIER_ORDER:
        if xp >= TIER_THRESHOLDS[t]:
            tier = t
    return tier


def next_threshold(xp: int) -> int | None:
    """XP needed for the next tier, or None at Master."""
    current = tier_for_xp(xp)
    idx = TIER_ORDER.index(current)
    if idx >= len(TIER_ORDER) - 1:
        return None
    return TIER_THRESHOLDS[TIER_ORDER[idx + 1]]


def tier_progress(xp: int) -> tuple[int, int]:
    """(xp_into_current_tier, xp_needed_for_next_tier); (0, 0) at Master."""
    current = tier_for_xp(xp)
    base = TIER_THRESHOLDS[current]
    nxt = next_threshold(xp)
    if nxt is None:
        return (0, 0)
    return (xp - base, nxt - base)


def format_skill_display(name: str, xp: int) -> str:
    """Player-facing line, e.g. 'Swordsmanship — Skilled, 1,420 / 2,000 mastery XP'."""
    tier = tier_for_xp(xp)
    nxt = next_threshold(xp)
    if nxt is None:
        return f"{name} — {tier.value}, {xp:,} mastery XP (max)"
    return f"{name} — {tier.value}, {xp:,} / {nxt:,} mastery XP"


@dataclass
class SkillProgress:
    """Mutable per-character skill state."""

    name: str
    xp: int = 0
    specialization: str | None = None
    recent_lines: list[str] = field(default_factory=list)

    @property
    def category(self) -> SkillCategory:
        return SKILL_REGISTRY[self.name]

    @property
    def tier(self) -> MasteryTier:
        return tier_for_xp(self.xp)

    def add_xp(self, amount: int, line: str | None = None) -> MasteryTier:
        before = self.tier
        self.xp = max(0, self.xp + amount)
        if line:
            self.recent_lines.append(line)
            del self.recent_lines[:-5]
        return before
