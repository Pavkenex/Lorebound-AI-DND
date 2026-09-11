"""Anti-grind novelty decay (GDD §14, §121).

Repeated trivial actions decay to zero: 8 XP, 5 XP, ... 1 XP, 0 XP across
easy locks. The player must raise the challenge to keep learning.
"""
from __future__ import annotations

from dataclasses import dataclass, field

from app.modules.progression.xp import compute_skill_xp, novelty_factor


def _bucket(challenge: int) -> str:
    if challenge <= 2:
        return "trivial"
    if challenge <= 5:
        return "moderate"
    return "hard"


@dataclass
class NoveltyTracker:
    """Counts repeats of (skill, task, challenge-bucket); higher challenge resets."""

    counts: dict[tuple[str, str, str], int] = field(default_factory=dict)

    def record(self, skill: str, task: str, challenge: int) -> int:
        """Register one attempt; returns the repetition index (0 = fresh)."""
        key = (skill, task, _bucket(challenge))
        reps = self.counts.get(key, 0)
        self.counts[key] = reps + 1
        return reps

    def award(self, skill: str, task: str, challenge: int, outcome: object = "success") -> int:
        """XP for one attempt, with decay applied. Trivial repeats decay to 0."""
        reps = self.record(skill, task, challenge)
        return compute_skill_xp(challenge, outcome, repetitions=reps)

    def peek_multiplier(self, skill: str, task: str, challenge: int) -> float:
        """Current novelty multiplier without consuming a repetition."""
        return novelty_factor(self.counts.get((skill, task, _bucket(challenge)), 0))

    def reset(self, skill: str, task: str, challenge: int) -> None:
        self.counts.pop((skill, task, _bucket(challenge)), None)
