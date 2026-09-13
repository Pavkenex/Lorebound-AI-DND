"""Check engine: d20 + modifiers vs DC, difficulty bands, hidden checks.

Tasks: t_4d8fddef (resolution + bands + hidden), t_7a355b22 (5 outcomes),
t_2e94122b (social check policy).
"""
from __future__ import annotations

import random
from enum import Enum
from typing import Any, Literal

from pydantic import BaseModel

from app.modules.npc.personality import SocialContext, social_adjustment


class CheckSuspension(Exception):
    """Control-flow signal: a surfaced check awaits the player's own throw.

    Raised by the play engine / action pipeline when a real roll would happen
    and the player is the one who must throw it. The caller turns ``spec``
    into a pending-check payload; a later call carrying the thrown die
    (``seed_roll``) resolves the beat normally. See ``play/engine.act``.
    """

    def __init__(self, spec: dict[str, Any]) -> None:
        super().__init__(str(spec.get("label", "check")))
        self.spec = dict(spec)

# --- Difficulty bands (GDD §19-20) -------------------------------------------

#: Named DC bands. Trivial is not a DC: it resolves automatically, no roll.
DC_BANDS: dict[str, int] = {
    "Easy": 8,
    "Routine": 10,
    "Moderate": 13,
    "Difficult": 16,
    "Hard": 19,
    "Extreme": 23,
    "Legendary": 27,
}
TRIVIAL = "Trivial"

#: Margin at/above which a success becomes exceptional; at/below which a
#: failure becomes critical (nat 1 / nat 20 always apply regardless).
EXCEPTIONAL_MARGIN = 10
CRITICAL_MARGIN = -10
#: Margin window [0, COST_WINDOW] above the DC that yields Success With Cost.
COST_WINDOW = 2


class Outcome(str, Enum):
    CriticalFailure = "CriticalFailure"
    Failure = "Failure"
    SuccessWithCost = "SuccessWithCost"
    Success = "Success"
    Exceptional = "Exceptional"


class CheckRequest(BaseModel):
    """Structured request for a single ability/skill check.

    Contract shared with other streams: import these exact names from
    ``app.modules.rules.checks``.
    """

    campaign_id: str = "default"
    character_id: str | None = None
    skill: str = "General"
    attribute_mod: int = 0
    skill_mod: int = 0
    situational_mod: int = 0
    dc: int = 10
    difficulty: str = "Routine"
    hidden: bool = False
    # Social-check context (t_2e94122b); ignored for non-social checks.
    social: bool = False
    npc_resistant: bool = False
    outcome_matters: bool = False
    uncertain: bool = False
    manipulation_attempt: bool = False
    #: Quality of player role-play, -1.0 (poor) .. +1.0 (great). Shifts DC only.
    rp_quality: float = 0.0

    @property
    def total_mod(self) -> int:
        """Everything the die carries into the total (before the rolled face)."""
        return self.attribute_mod + self.skill_mod + self.situational_mod


class CheckResult(BaseModel):
    """Structured outcome handed to the narrator (t_7a355b22)."""

    request_snapshot: CheckRequest
    roll: int  # raw d20, 0 when trivial/auto (no die rolled)
    total: int
    dc: int
    margin: int
    outcome: Outcome
    trivial_auto: bool = False
    hidden: bool = False
    #: Surfaced in the UI only when meaningful: never for trivial or hidden.
    surfaced: bool = True
    #: Real complication for SuccessWithCost; None otherwise.
    cost: str | None = None

    model_config = {"use_enum_values": False}


def dc_for_band(band: str) -> int | None:
    """Return the DC for a named band, or None for Trivial (auto-success)."""
    if band == TRIVIAL:
        return None
    try:
        return DC_BANDS[band]
    except KeyError:
        raise ValueError(f"Unknown difficulty band: {band!r}") from None


def is_trivial(band: str) -> bool:
    return band == TRIVIAL


def _classify(roll: int, margin: int) -> Outcome:
    if roll == 1:
        return Outcome.CriticalFailure
    if roll == 20:
        return Outcome.Exceptional
    if margin <= CRITICAL_MARGIN:
        return Outcome.CriticalFailure
    if margin < 0:
        return Outcome.Failure
    if margin <= COST_WINDOW:
        return Outcome.SuccessWithCost
    if margin >= EXCEPTIONAL_MARGIN:
        return Outcome.Exceptional
    return Outcome.Success


def roll_check(request: CheckRequest, roll: int | None = None,
               rng: random.Random | None = None) -> CheckResult:
    """Resolve d20 + attribute + skill + situational vs DC.

    ``roll`` is injectable for tests; otherwise drawn from ``rng`` (or global).
    Trivial difficulty resolves automatically with no die roll.
    Hidden checks resolve normally but are never surfaced (t_4d8fddef).
    """
    if is_trivial(request.difficulty):
        total = request.dc  # auto success; no die involved
        return CheckResult(
            request_snapshot=request, roll=0, total=total, dc=request.dc,
            margin=0, outcome=Outcome.Success, trivial_auto=True,
            hidden=request.hidden, surfaced=False,
        )
    r = roll if roll is not None else (rng or random).randint(1, 20)
    if not 1 <= r <= 20:
        raise ValueError(f"d20 roll out of range: {r}")
    total = r + request.attribute_mod + request.skill_mod + request.situational_mod
    margin = total - request.dc
    outcome = _classify(r, margin)
    return CheckResult(
        request_snapshot=request, roll=r, total=total, dc=request.dc,
        margin=margin, outcome=outcome, trivial_auto=False,
        hidden=request.hidden,
        # Hidden checks are silent; trivial shows no dice (§19, §22).
        surfaced=not request.hidden,
        cost=complication_for(outcome, request.skill) if outcome == Outcome.SuccessWithCost else None,
    )


def complication_for(outcome: Outcome, skill: str) -> str | None:
    """A real (mechanical, not cosmetic) complication for Success With Cost."""
    if outcome != Outcome.SuccessWithCost:
        return None
    return (
        f"Success with cost ({skill}): the attempt works, but at a price — "
        "spend extra time, make noise, consume a resource, or alert someone nearby. "
        "The engine must apply one concrete setback."
    )


# --- Social check policy (t_2e94122b, GDD §66) --------------------------------

#: RP quality maps to a DC shift of at most ±2; it never replaces the roll.
MAX_RP_DC_SHIFT = 2

#: The best face a d20 shows short of the natural 20. A DC further than this
#: past the check's total modifier is reachable only by a critical — "Long odds".
NORMAL_REACH_FACE = 19


def is_long_odds(dc: int, total_mod: int) -> bool:
    """True when only a natural 20 passes (§6: the hail-mary window is visible).

    Criticals are unconditional, so a long shot is never impossible — the
    player is gambling, and the check prompt says so.
    """
    return int(dc) - int(total_mod) > NORMAL_REACH_FACE


def should_roll_social(request: CheckRequest) -> bool:
    """Roll only when the NPC is resistant, the outcome matters under real
    uncertainty, or the player attempts manipulation. Otherwise role-play
    the outcome without dice."""
    return bool(
        request.npc_resistant
        or request.manipulation_attempt
        or (request.outcome_matters and request.uncertain)
    )


def rp_dc_shift(rp_quality: float) -> int:
    """Convert RP quality [-1, 1] to a DC shift in [-2, +2].

    Great RP lowers the DC; poor RP raises it. It never replaces the skill.
    """
    q = max(-1.0, min(1.0, rp_quality))
    return -round(q * MAX_RP_DC_SHIFT)


def apply_social_policy(request: CheckRequest,
                        social: SocialContext | None = None) -> tuple[CheckRequest, bool]:
    """Apply the DC shifts a social check earns; return (adjusted, roll_needed).

    Two shifts ride on top of the base difficulty: the role-play shift, and —
    when the check is aimed at a known character — the personality adjustment
    (§6: approach + mood − relationship credit, with any vow's weight). Both
    only move the DC; the roll itself is never replaced.
    """
    shift = rp_dc_shift(request.rp_quality)
    if social is not None:
        shift += social_adjustment(social, request.dc).shift
    adjusted = request.model_copy(update={"dc": max(1, request.dc + shift)})
    return adjusted, should_roll_social(request)


ResultVisibility = Literal["shown", "silent"]
