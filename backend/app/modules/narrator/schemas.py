"""Narrator output schema + length controls (t_ad5b30b4, GDD §25, §104, §106)."""
from __future__ import annotations

from enum import Enum

from pydantic import BaseModel, Field


class Length(str, Enum):
    CONCISE = "Concise"
    STANDARD = "Standard"
    DETAILED = "Detailed"


#: Word-count targets per length setting. Standard (default): 100-250 words
#: per major action.
WORD_TARGETS: dict[str, tuple[int, int]] = {
    Length.CONCISE.value: (40, 80),
    Length.STANDARD.value: (100, 250),
    Length.DETAILED.value: (250, 450),
}
DEFAULT_LENGTH = Length.STANDARD.value


class NpcDialogue(BaseModel):
    npc: str
    line: str


class SuggestedActionOut(BaseModel):
    label: str
    command: str


class NarratorOutput(BaseModel):
    """Structured narrator response contract."""

    narration: str
    npc_dialogue: list[NpcDialogue] = Field(default_factory=list)
    suggested_actions: list[SuggestedActionOut] = Field(default_factory=list)
    proposed_events: list[dict] = Field(default_factory=list)
    proposed_lead_changes: list[dict] = Field(default_factory=list)
    length: str = DEFAULT_LENGTH
    streamed: bool = False

    def word_count(self) -> int:
        return len(self.narration.split())

    def within_target(self) -> bool:
        lo, hi = WORD_TARGETS.get(self.length, WORD_TARGETS[DEFAULT_LENGTH])
        return lo <= self.word_count() <= hi


def ack(action_summary: str) -> dict:
    """Immediate UI acknowledgement so the player never stares at an empty
    interface while the model responds (§106)."""
    return {"type": "ack", "message": f"Noted — {action_summary}. The tale unfolds…"}
