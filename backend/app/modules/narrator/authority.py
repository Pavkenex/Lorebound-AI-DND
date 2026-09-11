"""AI authority boundary: proposals approved by the engine (t_a65af7fa).

GDD §26, §61, §108: the AI may NEVER write HP, currency, inventory, skill
XP, location, quest completion, NPC death, reputation, time, or combat
state. Change requests arrive as typed proposals; the engine decides.
A proposal lacking engine approval is discarded and logged — no model path
to authoritative columns exists by construction: this module holds the only
``apply`` function and it requires explicit engine approval.

Contract shared with other streams: import ``EngineProposal`` (exact name)
from ``app.modules.narrator.authority``.
"""
from __future__ import annotations

from datetime import UTC, datetime
from enum import Enum
from typing import Any

from pydantic import BaseModel, Field

from app.core.events import EventKind, GameEvent


class ProposalKind(str, Enum):
    ADD_ITEM = "ADD_ITEM"
    REMOVE_ITEM = "REMOVE_ITEM"
    GRANT_CURRENCY = "GRANT_CURRENCY"
    HEAL_OR_DAMAGE = "HEAL_OR_DAMAGE"
    GRANT_XP = "GRANT_XP"
    MOVE_PLAYER = "MOVE_PLAYER"
    UPDATE_LEAD = "UPDATE_LEAD"
    NPC_STATE = "NPC_STATE"
    REPUTATION = "REPUTATION"
    ADVANCE_TIME = "ADVANCE_TIME"
    COMBAT_STATE = "COMBAT_STATE"


#: Authoritative columns no model output may touch directly. Only the
#: engine's apply() writes these, and only for approved proposals.
AUTHORITATIVE_COLUMNS = frozenset({
    "hp", "currency", "inventory", "skill_xp", "location",
    "quest_completion", "npc_alive", "reputation", "time", "combat_state",
})

#: Which state key each proposal kind may write (allowlist). Anything else
#: is rejected even when "approved" — defense in depth.
PROPOSAL_WRITES: dict[str, set[str]] = {
    ProposalKind.ADD_ITEM.value: {"inventory"},
    ProposalKind.REMOVE_ITEM.value: {"inventory"},
    ProposalKind.GRANT_CURRENCY.value: {"currency"},
    ProposalKind.HEAL_OR_DAMAGE.value: {"hp"},
    ProposalKind.GRANT_XP.value: {"skill_xp"},
    ProposalKind.MOVE_PLAYER.value: {"location"},
    ProposalKind.UPDATE_LEAD.value: {"quest_completion"},
    ProposalKind.NPC_STATE.value: {"npc_alive"},
    ProposalKind.REPUTATION.value: {"reputation"},
    ProposalKind.ADVANCE_TIME.value: {"time"},
    ProposalKind.COMBAT_STATE.value: {"combat_state"},
}


class EngineProposal(BaseModel):
    """A typed change request from the AI. Data only — applies nothing."""

    kind: ProposalKind
    reason: str
    payload: dict[str, Any] = Field(default_factory=dict)
    proposer: str = "narrator"

    def target_keys(self) -> set[str]:
        return set(self.payload.get("writes", [])) | PROPOSAL_WRITES.get(self.kind.value, set())


class ProposalDecision(BaseModel):
    proposal: EngineProposal
    approved: bool
    applied: bool = False
    discard_reason: str | None = None
    at: datetime = Field(default_factory=lambda: datetime.now(UTC))


class AuthorityEngine:
    """Reviews proposals against engine state; owns all authoritative writes."""

    def __init__(self) -> None:
        self.discarded: list[ProposalDecision] = []
        self.applied: list[ProposalDecision] = []
        self.events: list[GameEvent] = []

    def _log(self, kind: EventKind, payload: dict[str, Any],
             campaign_id: str = "default") -> GameEvent:
        ev = GameEvent(kind=kind, campaign_id=campaign_id, payload=payload)
        self.events.append(ev)
        return ev

    def review(self, proposals: list[EngineProposal], state: dict[str, Any],
               campaign_id: str = "default",
               approve: bool = True) -> list[ProposalDecision]:
        """Engine-side review. By default the engine may approve sane
        proposals; pass ``approve=False`` for strict/manual modes.

        Rules: reason required; kind must be known; payload writes must stay
        inside the kind's allowlist; referenced targets must exist in state
        (e.g. item must exist to remove; NPC must exist for NPC_STATE).
        """
        decisions: list[ProposalDecision] = []
        for p in proposals:
            ok, reason = self._check(p, state, approve)
            d = ProposalDecision(proposal=p, approved=ok,
                                 discard_reason=None if ok else reason)
            decisions.append(d)
            if ok:
                self.applied.append(d)
                self._log(EventKind.WORLD_EVENT_OCCURRED,
                          {"proposal": p.kind.value, "approved": True,
                           "reason": p.reason, "payload": p.payload}, campaign_id)
            else:
                self.discarded.append(d)
                self._log(EventKind.WORLD_EVENT_OCCURRED,
                          {"proposal": p.kind.value, "approved": False,
                           "discard_reason": reason, "payload": p.payload}, campaign_id)
        return decisions

    def _check(self, p: EngineProposal, state: dict[str, Any], approve: bool) -> tuple[bool, str]:
        if not p.reason or not p.reason.strip():
            return False, "missing reason"
        if p.kind.value not in PROPOSAL_WRITES:
            return False, f"unknown proposal kind {p.kind.value}"
        if not approve:
            return False, "engine approval withheld (strict mode)"
        allowed = PROPOSAL_WRITES[p.kind.value]
        writes = set(p.payload.get("writes", [])) or allowed
        if not writes <= allowed:
            return False, f"writes {sorted(writes)} exceed allowlist {sorted(allowed)}"
        if p.kind == ProposalKind.REMOVE_ITEM:
            inv = set(state.get("inventory", []))
            if p.payload.get("item") not in inv:
                return False, "item not in inventory"
        if p.kind == ProposalKind.NPC_STATE:
            npcs = state.get("npcs", {})
            if p.payload.get("npc") not in npcs:
                return False, "unknown npc"
        if p.kind == ProposalKind.MOVE_PLAYER and p.payload.get("location") not in set(
            state.get("known_locations", [])
        ):
            return False, "unknown location (no teleport)"
        return True, ""

    def apply(self, decision: ProposalDecision, state: dict[str, Any]) -> dict[str, Any]:
        """The ONLY model-adjacent write path to authoritative columns — and
        it requires an approved decision. Unapproved => discarded + logged,
        state untouched."""
        if not decision.approved:
            self._log(EventKind.WORLD_EVENT_OCCURRED,
                      {"proposal": decision.proposal.kind.value,
                       "discard_reason": decision.discard_reason or "unapproved apply blocked"})
            return state
        p = decision.proposal
        allowed = PROPOSAL_WRITES[p.kind.value]
        state = dict(state)
        if p.kind == ProposalKind.ADD_ITEM and "inventory" in allowed:
            inv = list(state.get("inventory", []))
            if p.payload.get("item") and p.payload["item"] not in inv:
                inv.append(p.payload["item"])
            state["inventory"] = inv
        elif p.kind == ProposalKind.REMOVE_ITEM and "inventory" in allowed:
            state["inventory"] = [i for i in state.get("inventory", []) if i != p.payload.get("item")]
        elif p.kind == ProposalKind.GRANT_CURRENCY:
            state["currency"] = int(state.get("currency", 0)) + int(p.payload.get("amount", 0))
        elif p.kind == ProposalKind.HEAL_OR_DAMAGE:
            state["hp"] = int(state.get("hp", 10)) + int(p.payload.get("delta", 0))
        elif p.kind == ProposalKind.GRANT_XP:
            xp = dict(state.get("skill_xp", {}))
            xp[p.payload.get("skill", "General")] = xp.get(p.payload.get("skill", "General"), 0) + int(p.payload.get("amount", 0))
            state["skill_xp"] = xp
        elif p.kind == ProposalKind.MOVE_PLAYER:
            state["location"] = p.payload.get("location", state.get("location"))
        elif p.kind == ProposalKind.UPDATE_LEAD:
            leads = dict(state.get("leads", {}))
            leads[p.payload.get("lead", "?")] = p.payload.get("status", "updated")
            state["leads"] = leads
        elif p.kind == ProposalKind.NPC_STATE:
            npcs = dict(state.get("npcs", {}))
            npcs[p.payload.get("npc", "?")] = p.payload.get("state", "met")
            state["npcs"] = npcs
        decision.applied = True
        return state
