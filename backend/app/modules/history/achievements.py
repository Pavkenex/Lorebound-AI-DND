"""Achievements celebrating stories, not grind (GDD §89)."""
from __future__ import annotations

from dataclasses import dataclass, field

from app.core.events import EventKind, GameEvent


@dataclass
class AchievementTracker:
    peaceful_resolutions: int = 0
    influential_favours: set[str] = field(default_factory=set)
    unlocked: set[str] = field(default_factory=set)

    def observe(self, event: GameEvent) -> list[str]:
        """Feed a GameEvent; returns newly unlocked achievement ids."""
        newly: list[str] = []
        kind = str(event.kind)
        if kind == EventKind.PLAYER_ACTION and event.payload.get("resolved_without_combat"):
            self.peaceful_resolutions += 1
            if self.peaceful_resolutions >= 10 and "silver-tongue" not in self.unlocked:
                self.unlocked.add("silver-tongue")
                newly.append("silver-tongue")  # Silver Tongue
        if kind == EventKind.RELATIONSHIP_CHANGED and event.payload.get("favour_from_influential"):
            npc = str(event.payload.get("npc", "unknown"))
            self.influential_favours.add(npc)
            if len(self.influential_favours) >= 5 and "everyone-owes-me" not in self.unlocked:
                self.unlocked.add("everyone-owes-me")
                newly.append("everyone-owes-me")  # Everyone Owes Me
        if kind == EventKind.LOCATION_DISCOVERED and event.payload.get("accidental") \
                and event.payload.get("major"):
            if "wrong-door" not in self.unlocked:
                self.unlocked.add("wrong-door")
                newly.append("wrong-door")  # Wrong Door
        return newly


ACHIEVEMENTS: dict[str, str] = {
    "silver-tongue": "Silver Tongue — ten conflicts resolved without combat",
    "everyone-owes-me": "Everyone Owes Me — favours from five influential NPCs",
    "wrong-door": "Wrong Door — accidentally discover a major hidden location",
}
