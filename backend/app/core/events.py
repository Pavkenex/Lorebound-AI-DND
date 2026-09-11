"""Event-driven history: every significant action becomes a typed GameEvent.

Contract shared by all modules. The engine appends events; the narrator only reads them.
"""
from datetime import datetime, timezone
from enum import Enum
from typing import Any
from pydantic import BaseModel, Field


class EventKind(str, Enum):
    PLAYER_ACTION = "PLAYER_ACTION"
    CHECK_RESOLVED = "CHECK_RESOLVED"
    ITEM_GAINED = "ITEM_GAINED"
    ITEM_LOST = "ITEM_LOST"
    NPC_MET = "NPC_MET"
    RELATIONSHIP_CHANGED = "RELATIONSHIP_CHANGED"
    LEAD_DISCOVERED = "LEAD_DISCOVERED"
    LEAD_UPDATED = "LEAD_UPDATED"
    SKILL_IMPROVED = "SKILL_IMPROVED"
    LOCATION_DISCOVERED = "LOCATION_DISCOVERED"
    WORLD_EVENT_OCCURRED = "WORLD_EVENT_OCCURRED"
    COMBAT_RESOLVED = "COMBAT_RESOLVED"
    SAVE_CREATED = "SAVE_CREATED"


class GameEvent(BaseModel):
    kind: EventKind
    campaign_id: str = "default"
    actor_id: str | None = None
    payload: dict[str, Any] = Field(default_factory=dict)
    at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))

    model_config = {"use_enum_values": True}


class EventLog:
    """In-memory log (persistent store is owned by the history module)."""

    def __init__(self) -> None:
        self._events: list[GameEvent] = []

    def append(self, event: GameEvent) -> GameEvent:
        self._events.append(event)
        return event

    def for_campaign(self, campaign_id: str) -> list[GameEvent]:
        return [e for e in self._events if e.campaign_id == campaign_id]

    def __len__(self) -> int:
        return len(self._events)


event_log = EventLog()
