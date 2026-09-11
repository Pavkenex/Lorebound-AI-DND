"""Dynamic world event escalation (GDD §43).

Unresolved situations escalate on a timeline:
rumours -> dangerous trade -> rising prices -> mercenaries hired -> attack.
The player can intervene at any stage; ignoring a threat has visible consequences.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum


class EscalationStage(str, Enum):
    RUMOUR = "rumour"
    DANGEROUS_TRADE = "dangerous_trade"
    PRICE_SPIKE = "rising_prices"
    MERCENARIES = "mercenaries_hired"
    ATTACK = "attack"


STAGE_ORDER: list[EscalationStage] = [s for s in EscalationStage]

#: Visible consequences of ignoring each stage.
STAGE_EFFECTS: dict[EscalationStage, str] = {
    EscalationStage.RUMOUR: "Travellers whisper of trouble on the roads.",
    EscalationStage.DANGEROUS_TRADE: "Caravans pay protection money; some goods vanish.",
    EscalationStage.PRICE_SPIKE: "Market prices rise 25%; millers hoard grain.",
    EscalationStage.MERCENARIES: "Armed strangers drink in the Lantern; brawls nightly.",
    EscalationStage.ATTACK: "Ember Hollow is attacked; the valley counts its dead.",
}


@dataclass
class EscalationTrack:
    threat_id: str
    stage: EscalationStage = EscalationStage.RUMOUR
    resolved: bool = False
    history: list[str] = field(default_factory=list)

    def advance(self) -> EscalationStage | None:
        """Move one step down the timeline. Returns the new stage, or None if done."""
        if self.resolved:
            return None
        idx = STAGE_ORDER.index(self.stage)
        self.history.append(self.stage.value)
        if idx >= len(STAGE_ORDER) - 1:
            return None
        self.stage = STAGE_ORDER[idx + 1]
        return self.stage

    def intervene(self, note: str = "") -> None:
        """Player action at any stage halts the timeline and resolves it."""
        self.resolved = True
        self.history.append(f"intervened@{self.stage.value}:{note}")

    def current_effect(self) -> str:
        return STAGE_EFFECTS[self.stage]
