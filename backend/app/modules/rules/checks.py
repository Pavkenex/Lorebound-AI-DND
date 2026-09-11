"""Check engine: d20 + modifiers vs DC, difficulty bands, hidden checks.

Tasks: t_4d8fddef (resolution + bands + hidden), t_7a355b22 (5 outcomes),
t_2e94122b (social check policy).
"""
from __future__ import annotations

import random
from enum import Enum
from typing import Literal

from pydantic import BaseModel

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


def apply_social_policy(request: CheckRequest) -> tuple[CheckRequest, bool]:
    """Apply RP DC shift; return (adjusted_request, roll_needed)."""
    adjusted = request.model_copy(update={"dc": request.dc + rp_dc_shift(request.rp_quality)})
    return adjusted, should_roll_social(request)


ResultVisibility = Literal["shown", "silent"]
