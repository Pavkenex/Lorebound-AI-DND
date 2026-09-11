"""Story Director pass & pacing rhythm (GDD §81-82).

Periodic evaluation: unresolved stories, shifting NPC goals, player lacking
meaningful choices, ignored threads, threads that could intersect.

The director proposes; the engine decides what becomes canonical. No tavern
conversation reveals an ancient conspiracy by accident.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum


class PacingStage(str, Enum):
    DISCOVERY = "Discovery"
    INVESTIGATION = "Investigation"
    ESCALATION = "Escalation"
    DECISION = "Decision"
    CONSEQUENCE = "Consequence"
    RECOVERY = "Recovery"
    NEW_DISCOVERY = "New Discovery"


PACING_ORDER: list[PacingStage] = [s for s in PacingStage]


@dataclass
class DirectorProposal:
    id: str
    suggestion: str
    rationale: str
    pacing: PacingStage
    context: str = ""  # e.g. 'tavern_conversation', 'wilderness', 'escalation:mercenaries_hired'
    supporting_leads: int = 0
    approved: bool | None = None  # None = pending engine decision

    def is_conspiracy_reveal(self) -> bool:
        text = (self.suggestion + " " + self.rationale).lower()
        return "ancient conspiracy" in text or "conspiracy" in text


@dataclass
class DirectorDecision:
    proposal_id: str
    approved: bool
    engine_note: str = ""


def validate_proposal(p: DirectorProposal) -> tuple[bool, str]:
    """Guardrails: no ancient conspiracy surfaces from idle tavern talk."""
    if p.is_conspiracy_reveal():
        if p.context == "tavern_conversation" and p.supporting_leads < 2:
            return False, "Conspiracy reveals need corroborating leads, not tavern talk."
    return True, "ok"


def evaluate_pass(world_state: dict) -> list[DirectorProposal]:
    """Periodic director evaluation over unresolved threads and pacing needs."""
    proposals: list[DirectorProposal] = []
    for thread in world_state.get("unresolved_threads", []):
        proposals.append(DirectorProposal(
            id=f"nudge-{thread}",
            suggestion=f"Surface a new lead on '{thread}' through an NPC with motive.",
            rationale="Thread stagnating; player lacks a meaningful next choice.",
            pacing=PacingStage.INVESTIGATION,
            context=world_state.get("context", ""),
        ))
    for thread in world_state.get("ignored_threats", []):
        proposals.append(DirectorProposal(
            id=f"escalate-{thread}",
            suggestion=f"Escalate '{thread}' one timeline step with visible consequences.",
            rationale="Ignored threat must visibly worsen.",
            pacing=PacingStage.ESCALATION,
            context=f"escalation:{thread}",
        ))
    if world_state.get("needs_recovery"):
        proposals.append(DirectorProposal(
            id="recovery-beat", suggestion="Offer a quiet scene: a meal, a letter, a calm watch.",
            rationale="Pacing demands recovery; quiet scenes are allowed.",
            pacing=PacingStage.RECOVERY, context="camp",
        ))
    return proposals


def engine_decide(proposal: DirectorProposal, approve: bool, note: str = "") -> DirectorDecision:
    """The engine — never the director — makes proposals canonical."""
    valid, reason = validate_proposal(proposal)
    if not valid:
        approve = False
        note = f"Vetoed: {reason} {note}".strip()
    proposal.approved = approve
    return DirectorDecision(proposal_id=proposal.id, approved=approve, engine_note=note)


def next_pacing(current: PacingStage) -> PacingStage:
    idx = PACING_ORDER.index(current)
    return PACING_ORDER[(idx + 1) % len(PACING_ORDER)]
