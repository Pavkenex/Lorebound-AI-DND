"""Traits, including story-derived traits (GDD §17).

Story-derived traits are written by the event layer, never proposed
arbitrarily by the narrator.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum

from app.core.events import EventKind, GameEvent


class TraitKind(str, Enum):
    POSITIVE = "positive"
    NEGATIVE = "negative"
    MIXED = "mixed"
    STORY = "story-derived"


class NarratorAuthorityError(PermissionError):
    """Raised when the narrator tries to author story-derived traits."""


@dataclass
class Trait:
    name: str
    kind: TraitKind
    description: str
    source_event_id: str | None = None
    _from_event: bool = field(default=False, repr=False)

    def __post_init__(self) -> None:
        if self.kind == TraitKind.STORY and not self._from_event:
            raise NarratorAuthorityError(
                "Story-derived traits must be written by the event layer "
                "(Trait.from_event), never constructed directly."
            )

    @classmethod
    def from_event(cls, event: GameEvent, name: str, description: str) -> "Trait":
        """Event-layer constructor: the only legal source of story traits."""
        return cls(
            name=name,
            kind=TraitKind.STORY,
            description=description,
            source_event_id=f"{event.kind}:{event.campaign_id}",
            _from_event=True,
        )


def narrator_propose_story_trait(*args: object, **kwargs: object) -> Trait:
    """Narrator entry-point: always refused. The event layer decides."""
    raise NarratorAuthorityError(
        "The narrator may never propose story-derived traits; "
        "they emerge from resolved events only."
    )


def derive_undead_trait(events: list[GameEvent]) -> Trait | None:
    """Repeated undead exposure resolves into Hardened or Fearful.

    Many survived exposures -> 'Hardened Against the Dead';
    exposures ending in failure/terror -> 'Fear of the Restless Dead'.
    """
    exposures = [
        e for e in events
        if e.kind == EventKind.PLAYER_ACTION and e.payload.get("foe") == "undead"
    ]
    if len(exposures) < 3:
        return None
    failures = sum(1 for e in exposures if e.payload.get("outcome") in ("failure", "terror"))
    source = exposures[-1]
    if failures * 2 >= len(exposures):
        return Trait.from_event(
            source, "Fear of the Restless Dead",
            "Shaken by the dead: -2 against undead until confronted in safety.",
        )
    return Trait.from_event(
        source, "Hardened Against the Dead",
        "Long watch against the dead: +2 resolve when facing undead.",
    )
