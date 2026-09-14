"""Mechanics resolver — Pass A (spec §5, §6). Pure code; no LLM.

Contract (R2 card):
- ``roll_expression`` parses ``NdM(+K|-K)(adv|dis)`` expressions, rolls via the
  injected RngSource, returns DiceResult (kept rolls, dropped rolls, total).
- ``resolve_check`` rolls d20 + skill modifier (from the skill's default table
  or an explicit override), applies the locked outcome bands from
  ARCHITECTURE.md ("Decisions"): nat-20 => CRITICAL (unconditional), nat-1 =>
  CRITICAL_FAILURE, margin 0-2 for non-crits => SUCCESS_AT_COST, below =>
  FAILURE, else SUCCESS. verdict_line is a one-line factual statement the
  narrator must honor.
- ``resolve_action`` maps an Intent to the mechanical work the turn implies
  (attack roll / skill check / flat check / nothing) and returns a
  MechanicalOutcome. It MAY read the store for eligibility (e.g., target
  present) and MAY attach suggested effect Deltas; it never commits anything.
- Determinism: everything takes an injected ``RngSource``; tests use seeded
  RNG. No global randomness.

Conventions this module pins (callers depend on them; documented because R7's
pipeline consumes these outcomes):

* **Expressions.** ``NdM``, ``NdM+K``, ``NdM-K`` (``N`` omitted means 1) with an
  optional advantage marker (``adv`` / ``advantage``) or disadvantage marker
  (``dis`` / ``disadvantage``), attached or as a separate word, in any
  position. Bounds: ``1 <= N <= 100``, ``2 <= M <= 1000``, ``|K| <= 1000``;
  anything else raises ``ValueError``. Advantage/disadvantage rolls the whole
  dice pool twice and keeps the higher (advantage) / lower (disadvantage)
  total; ties keep the FIRST pool (deterministic).
* **Checks.** d20 + modifier vs ``CheckRequest.dc``. nat-20 => ``CRITICAL``
  regardless of margin; nat-1 => ``CRITICAL_FAILURE`` regardless of margin;
  otherwise margin ``0..2`` => ``SUCCESS_AT_COST``, margin ``< 0`` =>
  ``FAILURE``, margin ``>= 3`` => ``SUCCESS``. ``advantage`` and
  ``disadvantage`` both set cancel out (plain single roll). Modifier source:
  explicit ``modifier=`` kwarg, else ``DEFAULT_SKILL_MODIFIERS[skill or
  attribute]``, else 0.
* **Actions.** ``resolve_action`` resolves at most ONE check: ``meta`` intents
  are always ``kind="none"``; an intent carrying a ``skill`` is a skill check;
  ``kind == "action"`` with a target and no skill is an attack;
  ``kind == "action"`` with neither is a flat check (modifier 0, DC
  ``DEFAULT_CHECK_DC``); dialogue/exploration without a skill is ``none``.
  A caller-supplied ``dc`` wins; otherwise the default DC applies and the
  outcome's notes say so.
* **Eligibility.** With a ``store`` supplied and a target that resolves to a
  known NPC (by ``npc:<id>``, ``<id>``, or exact name): a dead target
  (``alive == 0``) or a target provably elsewhere (both locations non-empty
  and different) resolves to ``kind="blocked"`` without rolling. A target that
  is not a known NPC just proceeds (it may be a prop); the note says so.
* **Effects.** Mechanics attach NO state deltas by default: a bare check
  implies no state change, and inventing one here would push un-validated lore
  into the pipeline. ``effect_rules`` accepts callables
  ``(Intent, MechanicalOutcome) -> list[Delta]`` for rules that genuinely
  imply state (R7 wires any it wants); their deltas still pass the Validator.
"""
from __future__ import annotations

import random
import re
from collections.abc import Callable, Sequence
from typing import Any, Protocol

from .models import (
    CheckRequest,
    CheckResult,
    Delta,
    DiceResult,
    Intent,
    MechanicalOutcome,
    OutcomeBand,
)

# Default skill -> attribute modifier table used when the store has no explicit
# value (R2 may extend; keep values in [-2..+5]).
DEFAULT_SKILL_MODIFIERS: dict[str, int] = {
    "athletics": 2, "stealth": 2, "persuasion": 2, "intimidation": 1,
    "insight": 2, "perception": 2, "investigation": 2, "survival": 1,
    "arcana": 1, "medicine": 1, "deception": 2, "lockpicking": 2,
    "attack": 3,
}

# Parsing bounds — guards against nonsense expressions.
MAX_DICE = 100
MIN_DIE_FACES = 2
MAX_DIE_FACES = 1000
MAX_MODIFIER = 1000

# Used by ``resolve_action`` when the caller supplies no explicit DC.
DEFAULT_CHECK_DC = 12
DEFAULT_ATTACK_DC = 12

_MODE_WORDS = {
    "adv": "adv", "adv.": "adv", "advantage": "adv",
    "dis": "dis", "dis.": "dis", "disadvantage": "dis",
}
_CORE_RE = re.compile(
    r"^(?P<count>\d*)d(?P<faces>\d+)(?P<mod>[+-]\d+)?(?P<mode>adv\.?|dis\.?)?$",
    re.IGNORECASE,
)
_NUMERIC_TARGET_RE = re.compile(r"^(?:npc[:_-]?)?(\d+)$", re.IGNORECASE)


class RngSource(Protocol):
    def randint(self, a: int, b: int) -> int:  # inclusive
        ...


class SeededRng:
    """Deterministic RNG adapter — the only concrete RNG the engine ships."""

    def __init__(self, seed: int | str | None = None) -> None:
        self._r = random.Random(seed)

    def randint(self, a: int, b: int) -> int:
        return self._r.randint(a, b)


def parse_expression(expr: str) -> tuple[int, int, int, str | None]:
    """Parse ``NdM(+K|-K)(adv|dis)`` -> ``(count, faces, modifier, mode)``.

    Public because callers (CLI/eval tooling) want to validate notation without
    rolling. Raises ``ValueError`` on anything malformed or out of bounds.
    """
    if not isinstance(expr, str) or not expr.strip():
        raise ValueError("empty dice expression")
    mode: str | None = None
    body_parts: list[str] = []
    for word in expr.strip().split():
        key = word.lower()
        if key in _MODE_WORDS:
            if mode is not None:
                raise ValueError(f"duplicate advantage/disadvantage marker in {expr!r}")
            mode = _MODE_WORDS[key]
        else:
            body_parts.append(word)
    if not body_parts:
        raise ValueError(f"malformed dice expression: {expr!r}")
    body = body_parts[0]
    for extra in body_parts[1:]:
        if extra in {"+", "-"} and not body.endswith(("+", "-")):
            body += extra
        elif extra[0] in "+-" and extra[1:].isdigit() and not body.endswith(("+", "-")):
            body += extra
        elif body.endswith(("+", "-")) and extra.isdigit():
            body += extra
        else:
            raise ValueError(f"malformed dice expression: {expr!r}")
    match = _CORE_RE.match(body)
    if match is None:
        raise ValueError(f"malformed dice expression: {expr!r}")
    count_text = match.group("count")
    count = int(count_text) if count_text else 1
    faces = int(match.group("faces"))
    modifier = int(match.group("mod")) if match.group("mod") else 0
    inline_mode = match.group("mode")
    if inline_mode:
        if mode is not None:
            raise ValueError(f"duplicate advantage/disadvantage marker in {expr!r}")
        mode = _MODE_WORDS[inline_mode.lower()]
    if not 1 <= count <= MAX_DICE:
        raise ValueError(f"dice count out of range 1..{MAX_DICE}: {expr!r}")
    if not MIN_DIE_FACES <= faces <= MAX_DIE_FACES:
        raise ValueError(f"die faces out of range {MIN_DIE_FACES}..{MAX_DIE_FACES}: {expr!r}")
    if abs(modifier) > MAX_MODIFIER:
        raise ValueError(f"modifier out of range ±{MAX_MODIFIER}: {expr!r}")
    return count, faces, modifier, mode


def canonical_expression(count: int, faces: int, modifier: int, mode: str | None) -> str:
    text = f"{count}d{faces}"
    if modifier:
        text += f"{modifier:+d}"
    if mode:
        text += f" {mode}"
    return text


def roll_expression(expr: str, rng: RngSource) -> DiceResult:
    """Roll a dice expression. See the module docstring for the grammar."""
    count, faces, modifier, mode = parse_expression(expr)
    first = [rng.randint(1, faces) for _ in range(count)]
    dropped: list[int] = []
    if mode is None:
        kept = first
    else:
        second = [rng.randint(1, faces) for _ in range(count)]
        first_total, second_total = sum(first), sum(second)
        if mode == "adv":
            keep_first = first_total >= second_total
        else:
            keep_first = first_total <= second_total
        kept, dropped = (first, second) if keep_first else (second, first)
    return DiceResult(
        expression=canonical_expression(count, faces, modifier, mode),
        rolls=list(kept),
        dropped=list(dropped),
        modifier=modifier,
        total=sum(kept) + modifier,
    )


def _skill_modifier(req: CheckRequest, override: int | None) -> int:
    if override is not None:
        return int(override)
    key = (req.skill or req.attribute or "").strip().lower()
    return DEFAULT_SKILL_MODIFIERS.get(key, 0)


def _check_label(req: CheckRequest) -> str:
    return (req.skill or req.attribute or "flat check").strip() or "flat check"


def _verdict_line(req: CheckRequest, roll: int, total: int, band: str, cancelled: bool) -> str:
    """One-line factual statement the narrator must honor (never multi-line)."""
    label = _check_label(req)
    margin = total - req.dc
    tail = " Advantage and disadvantage cancel." if cancelled else ""
    if band == OutcomeBand.CRITICAL.value:
        return (
            f"Natural 20 on the {label} check (dc {req.dc}): CRITICAL SUCCESS — "
            f"the action succeeds outright (no cost).{tail}"
        )
    if band == OutcomeBand.CRITICAL_FAILURE.value:
        return (
            f"Natural 1 on the {label} check (dc {req.dc}): CRITICAL FAILURE — "
            f"the action fails badly with an added complication.{tail}"
        )
    if band == OutcomeBand.SUCCESS_AT_COST.value:
        return (
            f"{label.capitalize()} check vs dc {req.dc}: SUCCESS AT A COST "
            f"({total} vs {req.dc}, margin {margin}) — it works, but a cost or "
            f"complication must be narrated.{tail}"
        )
    if band == OutcomeBand.FAILURE.value:
        return (
            f"{label.capitalize()} check vs dc {req.dc}: FAILURE "
            f"({total} vs {req.dc}, margin {margin}) — it does not succeed; fail "
            f"forward with a consequence, never narrate success.{tail}"
        )
    return (
        f"{label.capitalize()} check vs dc {req.dc}: SUCCESS "
        f"({total} vs {req.dc}, margin {margin}) — the action works as intended.{tail}"
    )


def resolve_check(req: CheckRequest, *, rng: RngSource, modifier: int | None = None) -> CheckResult:
    """Roll d20 + modifier against ``req.dc`` and apply the locked bands."""
    mod = _skill_modifier(req, modifier)
    advantage, disadvantage = bool(req.advantage), bool(req.disadvantage)
    cancelled = advantage and disadvantage
    if cancelled:
        advantage = disadvantage = False
    if advantage:
        first, second = rng.randint(1, 20), rng.randint(1, 20)
        roll = max(first, second)
    elif disadvantage:
        first, second = rng.randint(1, 20), rng.randint(1, 20)
        roll = min(first, second)
    else:
        roll = rng.randint(1, 20)
    total = roll + mod
    margin = total - req.dc
    if roll == 20:
        band = OutcomeBand.CRITICAL.value
    elif roll == 1:
        band = OutcomeBand.CRITICAL_FAILURE.value
    elif margin < 0:
        band = OutcomeBand.FAILURE.value
    elif margin <= 2:
        band = OutcomeBand.SUCCESS_AT_COST.value
    else:
        band = OutcomeBand.SUCCESS.value
    return CheckResult(
        request=req,
        roll=roll,
        modifier=mod,
        total=total,
        band=band,
        verdict_line=_verdict_line(req, roll, total, band, cancelled),
    )


def _target_id(target: str) -> int | None:
    """``"npc:3"`` / ``"npc_3"`` / ``"3"`` -> 3; names -> None."""
    match = _NUMERIC_TARGET_RE.match(str(target).strip())
    return int(match.group(1)) if match else None


def lookup_npc(store: Any, target: str) -> dict | None:
    """Resolve a delta/intent target to an ``npcs`` row (id form or exact name)."""
    if store is None or not target:
        return None
    text = str(target).strip()
    if not text:
        return None
    npc_id = _target_id(text)
    if npc_id is not None:
        return store.find_one("npcs", {"id": npc_id})
    return store.find_one("npcs", {"name": text})


def _player_location(store: Any) -> str:
    if store is None:
        return ""
    row = store.find_one("characters", order_by="id")
    return str(row.get("location_id", "") or "") if row else ""


def _eligibility_block(store: Any, target: str, notes: list[str]) -> str | None:
    """Reason the target cannot be interacted with, or None (proceed)."""
    if store is None or not target:
        return None
    row = lookup_npc(store, target)
    if row is None:
        notes.append(f"target {target!r} is not a known NPC; no eligibility gate applied")
        return None
    if not row.get("alive", 1):
        return f"target {target!r} is dead"
    npc_location = str(row.get("location_id", "") or "")
    player_location = _player_location(store)
    if npc_location and player_location and npc_location != player_location:
        return f"target {target!r} is not present (at {npc_location!r}, player at {player_location!r})"
    notes.append(f"target {target!r} is present and alive")
    return None


def resolve_action(
    intent: Intent,
    *,
    rng: RngSource,
    store: Any = None,
    turn: int = 0,
    dc: int | None = None,
    effect_rules: Sequence[Callable[[Intent, MechanicalOutcome], list[Delta]]] | None = None,
) -> MechanicalOutcome:
    """Resolve whatever mechanics ``intent`` implies. ``store`` is optional so
    the resolver stays usable (and testable) without a DB.

    Returns a MechanicalOutcome whose ``kind`` is one of ``none`` | ``check`` |
    ``attack`` | ``blocked``; mechanics never commit anything.
    """
    kind = (getattr(intent, "kind", "") or "").strip().lower()
    text = getattr(intent, "text", "") or ""
    skill = (getattr(intent, "skill", None) or "").strip()
    target = (getattr(intent, "target", None) or "").strip()
    notes: list[str] = []

    if kind == "meta":
        notes.append("meta intent: no dice are rolled")
        return MechanicalOutcome(
            turn=turn,
            kind="none",
            label="meta",
            check=None,
            effects=[],
            verdict_line="No mechanics resolve this meta request; narration may proceed freely.",
            notes=notes,
        )

    check_kind: str
    if skill:
        check_kind, label = "check", f"{skill} check"
    elif kind == "action" and target:
        check_kind, label = "attack", f"attack on {target}"
    elif kind == "action":
        check_kind, label = "check", "flat check"
    else:
        notes.append(f"{kind or 'unknown'} intent with no skill: no check is implied")
        return MechanicalOutcome(
            turn=turn,
            kind="none",
            label=kind or "free narration",
            check=None,
            effects=[],
            verdict_line=f"No mechanics resolve this {kind or 'free'} turn; narration may proceed freely.",
            notes=notes,
        )

    if target:
        block = _eligibility_block(store, target, notes)
        if block:
            notes.append(f"action blocked: {block}")
            return MechanicalOutcome(
                turn=turn,
                kind="blocked",
                label=label,
                check=None,
                effects=[],
                verdict_line=(
                    f"No roll is made: {block}. The action cannot proceed as declared; "
                    f"narrate the obstacle, not a success or failure of the attempt."
                ),
                notes=notes,
            )

    if dc is None:
        dc = DEFAULT_ATTACK_DC if check_kind == "attack" else DEFAULT_CHECK_DC
        notes.append(f"no explicit dc supplied; using the default dc {dc}")

    req = CheckRequest(skill=skill, dc=dc, why=text, attribute="")
    result = resolve_check(req, rng=rng)
    outcome = MechanicalOutcome(
        turn=turn,
        kind=check_kind,
        label=label,
        check=result,
        effects=[],
        verdict_line=result.verdict_line,
        notes=notes,
    )
    for rule in effect_rules or ():
        extra = rule(intent, outcome)
        if extra:
            outcome.effects.extend(extra)
    return outcome
