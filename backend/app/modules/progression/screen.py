"""Skills screen data API + subtle notifications (GDD §73, §76).

Skill gain is legible in hindsight without interrupting flow during play:
no constant reward popups — only tier-ups and milestones notify.
"""
from __future__ import annotations

from dataclasses import dataclass, field

from app.modules.progression.skills import (
    SkillProgress,
    format_skill_display,
    next_threshold,
    tier_progress,
)
from app.modules.progression.specializations import available_specializations
from app.modules.progression.training import INSTRUCTORS, Instructor


@dataclass
class NotificationCenter:
    """Coalesced, subtle notifications. Small gains never pop up."""

    _pending: list[str] = field(default_factory=list)

    def notify_gain(self, name: str, amount: int, tier_before: str, tier_after: str) -> None:
        if tier_before != tier_after:
            self._pending.append(f"{name} improved to {tier_after}")
        # Small gains are visible on the screen's recent-lines, not as popups.

    def notify(self, message: str) -> None:
        self._pending.append(message)

    def drain(self) -> list[str]:
        out = list(self._pending)
        self._pending.clear()
        return out


def skill_entry(
    progress: SkillProgress,
    trainers: list[Instructor] | None = None,
) -> dict:
    nxt = next_threshold(progress.xp)
    into, needed = tier_progress(progress.xp)
    return {
        "name": progress.name,
        "category": progress.category.value,
        "tier": progress.tier.value,
        "xp": progress.xp,
        "xp_into_tier": into,
        "xp_for_next": needed,
        "xp_bar": (into / needed) if needed else 1.0,
        "display": format_skill_display(progress.name, progress.xp),
        "recent_lines": list(progress.recent_lines[-5:]),
        "specialization": progress.specialization,
        "available_specializations": [
            s.name for s in available_specializations(progress.name, progress.tier)
            if s.name != progress.specialization
        ],
        "trainers": [t.name for t in (trainers or INSTRUCTORS.values())],
        "practice_options": [1, 2, 3, 4],
    }


def build_skills_screen(
    skills: list[SkillProgress],
    notifications: list[str] | None = None,
    trainers: list[Instructor] | None = None,
) -> dict:
    """Full skills-screen payload for the frontend."""
    return {
        "skills": [skill_entry(s, trainers) for s in skills],
        "notifications": list(notifications or []),
    }
