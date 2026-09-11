"""Turn structure: Movement / Primary / Minor / Reaction.

A turn may be declared with traditional slot actions OR as free text; both fill
the same slots. Free text that names an existing environment object is always
classified as an environment interaction (never rewritten to Attack).
"""
from __future__ import annotations

from enum import Enum

from pydantic import BaseModel

from app.modules.combat.models import CombatState


class ActionSlot(str, Enum):
    MOVEMENT = "movement"
    PRIMARY = "primary"
    MINOR = "minor"
    REACTION = "reaction"


class ActionKind(str, Enum):
    ATTACK_LIGHT = "attack_light"
    ATTACK_HEAVY = "attack_heavy"
    MOVE = "move"
    SPRINT = "sprint"
    TECHNIQUE = "technique"
    BLOCK = "block"
    ENV_INTERACT = "env_interact"
    MINOR = "minor"
    REACTION = "reaction"
    PASS = "pass"


class SlotAction(BaseModel):
    slot: ActionSlot
    kind: ActionKind
    target_id: str | None = None
    text: str = ""
    technique_id: str | None = None


class ParsedTurn(BaseModel):
    raw_text: str = ""
    movement: SlotAction | None = None
    primary: SlotAction | None = None
    minor: SlotAction | None = None
    reaction: SlotAction | None = None
    uses_free_text: bool = False

    def primary_kind(self) -> str | None:
        return self.primary.kind.value if self.primary else None


_ATTACK_WORDS = ("attack", "strike", "slash", "stab", "shoot", "fire", "hit", "swing",
                   "smash", "crush", "cleave", "impale")
_HEAVY_WORDS = ("heavy", "power", "brutal", "reckless", "all-out", "all out")
_ENV_VERBS = ("cut", "drop", "topple", "shove", "light", "throw", "smash", "break",
              "pull", "climb", "hide", "topple", "swing from", "push")
_MOVE_WORDS = ("move", "advance", "retreat", "rush", "charge", "close", "fall back", "reposition")
_SPRINT_WORDS = ("sprint", "dash", "run")
_OBJECT_NOUNS = ("rope", "chandelier", "barrel", "chain", "beam", "door", "table",
                 "bridge", "brazier", "tapestry", "pillar", "statue", "cart",
                 "window", "balcony", "rafters", "powder", "chandelier")
_BLOCK_WORDS = ("block", "parry", "deflect", "shield")


def classify_free_text(state: CombatState, actor_id: str, text: str) -> ParsedTurn:
    """Classify free text into turn slots. Env matches take absolute priority."""
    lowered = text.lower()
    turn = ParsedTurn(raw_text=text, uses_free_text=True)

    # 1. Environment interaction wins over everything (anti-rewrite rule).
    env_feature = state.find_env(text)
    if env_feature is not None:
        target_id = _named_combatant(state, actor_id, text, fallback=False)
        turn.primary = SlotAction(
            slot=ActionSlot.PRIMARY, kind=ActionKind.ENV_INTERACT,
            text=text, target_id=target_id or env_feature.id,
        )
        if any(w in lowered for w in _MOVE_WORDS):
            turn.movement = SlotAction(slot=ActionSlot.MOVEMENT, kind=ActionKind.MOVE, text=text)
        return turn

    # 2. Named person + violent verb aimed at them: an attack, not improv.
    named = _named_combatant(state, actor_id, text, fallback=False)
    if named is not None and any(w in lowered for w in _ATTACK_WORDS):
        kind = ActionKind.ATTACK_HEAVY if any(w in lowered for w in _HEAVY_WORDS) else ActionKind.ATTACK_LIGHT
        turn.primary = SlotAction(
            slot=ActionSlot.PRIMARY, kind=kind, target_id=named, text=text)
    elif any(w in lowered for w in _ATTACK_WORDS):
        # 3. Attack words with no name: nearest living foe — unless the words
        # are aimed at an object (which may simply not exist here), in which
        # case fail open through the environment resolver.
        if any(n in lowered for n in _OBJECT_NOUNS):
            turn.primary = SlotAction(
                slot=ActionSlot.PRIMARY, kind=ActionKind.ENV_INTERACT, text=text)
        else:
            kind = ActionKind.ATTACK_HEAVY if any(w in lowered for w in _HEAVY_WORDS) else ActionKind.ATTACK_LIGHT
            turn.primary = SlotAction(
                slot=ActionSlot.PRIMARY, kind=kind,
                target_id=_named_combatant(state, actor_id, text), text=text,
            )
    elif "spell" in lowered or "cast" in lowered or "technique" in lowered:
        turn.primary = SlotAction(
            slot=ActionSlot.PRIMARY, kind=ActionKind.TECHNIQUE, text=text,
            target_id=_named_combatant(state, actor_id, text),
        )
    elif any(w in lowered for w in _ENV_VERBS):
        # 4. Looks like object improv but nothing matches: route to the
        # environment resolver so it fails open (env_missing) instead of
        # being silently rewritten into an Attack.
        turn.primary = SlotAction(
            slot=ActionSlot.PRIMARY, kind=ActionKind.ENV_INTERACT, text=text)

    # 3. Movement riders.
    if any(w in lowered for w in _SPRINT_WORDS):
        turn.movement = SlotAction(slot=ActionSlot.MOVEMENT, kind=ActionKind.SPRINT, text=text)
    elif any(w in lowered for w in _MOVE_WORDS) and turn.movement is None:
        turn.movement = SlotAction(slot=ActionSlot.MOVEMENT, kind=ActionKind.MOVE, text=text)

    # 4. Block as reaction.
    if any(w in lowered for w in _BLOCK_WORDS):
        turn.reaction = SlotAction(slot=ActionSlot.REACTION, kind=ActionKind.BLOCK, text=text)

    if turn.primary is None and turn.movement is None and turn.reaction is None:
        # Genuinely unclassifiable: keep the player's words as a minor/improv
        # action rather than inventing an Attack.
        turn.minor = SlotAction(slot=ActionSlot.MINOR, kind=ActionKind.MINOR, text=text)
    return turn


def _named_combatant(state: CombatState, actor_id: str, text: str,
                     *, fallback: bool = True) -> str | None:
    lowered = text.lower()
    # Prefer living opponents whose name appears in the text.
    for c in state.combatants.values():
        if c.id != actor_id and c.name.lower() in lowered:
            return c.id
    if not fallback:
        return None
    living = state.living_enemies_of(actor_id)
    if living and any(w in lowered for w in _ATTACK_WORDS):
        return living[0].id
    return None


def traditional_turn(
    *,
    movement: SlotAction | None = None,
    primary: SlotAction | None = None,
    minor: SlotAction | None = None,
    reaction: SlotAction | None = None,
) -> ParsedTurn:
    """Build a turn from explicit slot picks (no free text)."""
    for action, slot in ((movement, ActionSlot.MOVEMENT), (primary, ActionSlot.PRIMARY),
                         (minor, ActionSlot.MINOR), (reaction, ActionSlot.REACTION)):
        if action is not None and action.slot != slot:
            raise ValueError(f"action {action.kind} does not belong in slot {slot}")
    return ParsedTurn(movement=movement, primary=primary, minor=minor,
                      reaction=reaction, uses_free_text=False)


def validate_turn(turn: ParsedTurn) -> list[str]:
    """Return human-readable problems; empty means the turn is legal."""
    problems: list[str] = []
    primaries = [a for a in (turn.primary,) if a is not None]
    if len(primaries) > 1:
        problems.append("only one primary action per turn")
    return problems
