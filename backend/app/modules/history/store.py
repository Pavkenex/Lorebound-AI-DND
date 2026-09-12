"""Event-driven history & debugging surface (GDD §101-102).

Every significant action becomes an event; the log supports debugging and
feeds campaign summaries.
"""
from __future__ import annotations

from datetime import UTC, datetime

from sqlalchemy import JSON, DateTime, String
from sqlalchemy.orm import Mapped, mapped_column

from app.core.database import Base
from app.core.events import EventKind, GameEvent


class HistoryEvent(Base):
    """Persistent event row (table owned by the history module)."""

    __tablename__ = "history_events"

    id: Mapped[int] = mapped_column(primary_key=True, autoincrement=True)
    kind: Mapped[str] = mapped_column(String(64))
    campaign_id: Mapped[str] = mapped_column(String(64), default="default")
    actor_id: Mapped[str | None] = mapped_column(String(64), nullable=True)
    payload: Mapped[dict] = mapped_column(JSON, default=dict)
    at: Mapped[datetime] = mapped_column(DateTime(timezone=True),
                                         default=lambda: datetime.now(UTC))


class HistoryStore:
    """In-memory event store with query helpers and summary builders."""

    def __init__(self) -> None:
        self._events: list[GameEvent] = []

    def append(self, event: GameEvent) -> GameEvent:
        self._events.append(event)
        return event

    def record(self, kind: EventKind | str, campaign_id: str = "default",
               actor_id: str | None = None, **payload: object) -> GameEvent:
        event = GameEvent(kind=kind, campaign_id=campaign_id,  # type: ignore[arg-type]
                          actor_id=actor_id, payload=dict(payload))
        return self.append(event)

    def by_kind(self, kind: EventKind | str) -> list[GameEvent]:
        key = kind.value if isinstance(kind, EventKind) else str(kind)
        return [e for e in self._events if str(e.kind) == key]

    def by_campaign(self, campaign_id: str) -> list[GameEvent]:
        return [e for e in self._events if e.campaign_id == campaign_id]

    def recent(self, n: int = 10) -> list[GameEvent]:
        return self._events[-n:]

    def summarize_campaign(self, campaign_id: str) -> dict:
        """Feed campaign summaries: counts per kind + timeline digest."""
        events = self.by_campaign(campaign_id)
        counts: dict[str, int] = {}
        for e in events:
            counts[str(e.kind)] = counts.get(str(e.kind), 0) + 1
        digest = [f"{e.at:%m-%d %H:%M} {e.kind}: {short_payload(e)}" for e in events[-20:]]
        return {
            "campaign_id": campaign_id,
            "total_events": len(events),
            "counts": counts,
            "digest": digest,
        }

    def __len__(self) -> int:
        return len(self._events)


def short_payload(event: GameEvent) -> str:
    payload = event.payload or {}
    for key in ("intent", "label", "name", "skill", "location", "lead", "note"):
        if key in payload:
            return f"{key}={payload[key]}"
    return ""
