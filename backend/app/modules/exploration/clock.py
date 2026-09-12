"""Authoritative game clock & action time costs (GDD §41, §62).

Time is an authoritative engine value; the AI may never advance it on its own.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta


class ClockAuthorityError(PermissionError):
    """Raised when anything but the engine tries to advance time."""


#: Canonical action time costs (minutes).
CONVERSATION_MIN = (5, 20)  # short exchange .. long parley
SEARCH_MIN = 20
TRAINING_HOURS = (1, 4)
TRAVEL_MIN_PER_KM = 12
SLEEP_MIN = 480  # 8 hours
CAMP_MIN = 60
FORAGE_MIN = 45
HUNT_MIN = 120
TRACK_MIN = 30
CRAFT_MIN_PER_ITEM = 60


@dataclass
class GameClock:
    """In-game clock. Only ``actor='engine'`` may advance it."""

    now: datetime = field(default_factory=lambda: datetime(1487, 4, 1, 8, 0, tzinfo=UTC))
    log: list[dict] = field(default_factory=list)

    def advance_minutes(self, minutes: int, *, actor: str, reason: str) -> datetime:
        if actor != "engine":
            raise ClockAuthorityError(
                f"Only the engine may advance time (attempted by {actor!r} for {reason!r})."
            )
        if minutes < 0:
            raise ValueError("Time cannot run backwards")
        self.now = self.now + timedelta(minutes=minutes)
        self.log.append({"minutes": minutes, "reason": reason, "at": self.now.isoformat()})
        return self.now

    # Convenience wrappers — all engine-attributed.
    def conversation(self, length: str = "typical") -> datetime:
        minutes = {"brief": 5, "typical": 10, "parley": 20}.get(length, 10)
        if not (CONVERSATION_MIN[0] <= minutes <= CONVERSATION_MIN[1]):
            raise ValueError("Conversation must cost 5-20 minutes")
        return self.advance_minutes(minutes, actor="engine", reason=f"conversation:{length}")

    def search(self) -> datetime:
        return self.advance_minutes(SEARCH_MIN, actor="engine", reason="search area")

    def training(self, hours: int) -> datetime:
        if not (TRAINING_HOURS[0] <= hours <= TRAINING_HOURS[1]):
            raise ValueError("Training must be 1-4 hours")
        return self.advance_minutes(hours * 60, actor="engine", reason="training")

    def travel(self, km: float) -> datetime:
        return self.advance_minutes(int(km * TRAVEL_MIN_PER_KM), actor="engine", reason="travel")

    def sleep(self) -> datetime:
        return self.advance_minutes(SLEEP_MIN, actor="engine", reason="sleep")

    @property
    def hour(self) -> int:
        return self.now.hour

    def display(self) -> str:
        return self.now.strftime("%Y-%m-%d %H:%M")
