"""Combat engine: resolves turns, attacks, env interaction, casting, intents.

Deterministic by default (fixed roll of 10) so scenes and tests agree; pass an
explicit roll to force a hit or a miss. Every resolution emits GameEvents.
"""
from __future__ import annotations

from pydantic import BaseModel, Field

try:  # Shared contract owned by the rules workstream.
    from app.modules.rules.checks import (
        CheckRequest,
        CheckResult,
        Outcome,
        roll_check,
    )
except ImportError:  # pragma: no cover - fallback when rules is unavailable

    class Outcome(str):  # type: ignore[no-redef]
        CriticalFailure = "CriticalFailure"
        Failure = "Failure"
        SuccessWithCost = "SuccessWithCost"
        Success = "Success"
        Exceptional = "Exceptional"

    class CheckRequest(BaseModel):  # type: ignore[no-redef]
        dc: int = 10
        attribute_mod: int = 0
        skill_mod: int = 0
        situational_mod: int = 0

    class CheckResult(BaseModel):  # type: ignore[no-redef]
        outcome: str = "Success"
        margin: int = 0

    def roll_check(request: CheckRequest, roll: int | None = None, **_: object) -> CheckResult:  # type: ignore[no-redef]
        r = roll if roll is not None else 10
        total = r + request.attribute_mod + request.skill_mod + request.situational_mod
        return CheckResult(outcome="Success" if total >= request.dc else "Failure",
                           margin=total - request.dc)


_HIT_OUTCOMES = {Outcome.Success, Outcome.SuccessWithCost, Outcome.Exceptional,
                 "Success", "SuccessWithCost", "Exceptional"}

from app.core.events import EventKind, GameEvent, event_log
from app.modules.combat import injuries as inj
from app.modules.combat import stamina as st
from app.modules.combat.magic import UnknownTechniqueError, get_technique
from app.modules.combat.models import (
    Combatant,
    CombatState,
    EnemyIntent,
    EnvFeature,
    RangeBand,
)
from app.modules.combat.modes import resolve_zero_hp
from app.modules.combat.records import CombatRecord  # noqa: F401 (contract import)
from app.modules.combat.turns import ActionKind, ParsedTurn, classify_free_text


class TurnResult(BaseModel):
    summary: str
    resolved_kind: str = "pass"
    damage_dealt: dict[str, int] = Field(default_factory=dict)
    injury_inflicted: dict[str, str] = Field(default_factory=dict)
    weakened: bool = False
    problems: list[str] = Field(default_factory=list)
    events: list[GameEvent] = Field(default_factory=list)


class CombatEngine:
    def __init__(self) -> None:
        self.states: dict[str, CombatState] = {}

    # -- lifecycle ------------------------------------------------------
    def start(
        self,
        combatants: list[Combatant],
        *,
        campaign_id: str = "default",
        mode: str = "standard",
        env: list[EnvFeature] | None = None,
        ranges: dict[tuple[str, str], RangeBand] | None = None,
    ) -> CombatState:
        state = CombatState(campaign_id=campaign_id, mode=mode, env=env or [])
        for c in combatants:
            state.combatants[c.id] = c
        for (a, b), band in (ranges or {}).items():
            state.set_range(a, b, band)
        self.states[state.id] = state
        self._emit(state, EventKind.PLAYER_ACTION, None,
                   {"action": "combat_started", "mode": mode})
        return state

    def get(self, combat_id: str) -> CombatState:
        return self.states[combat_id]

    # -- turns ----------------------------------------------------------
    def submit_turn(
        self,
        state: CombatState,
        actor_id: str,
        *,
        text: str | None = None,
        turn: ParsedTurn | None = None,
        roll: int = 10,
    ) -> TurnResult:
        actor = state.combatants[actor_id]
        if not actor.can_act:
            return TurnResult(summary=f"{actor.name} cannot act ({actor.status}).",
                              resolved_kind="pass")
        parsed = turn if turn is not None else classify_free_text(state, actor_id, text or "")
        result = TurnResult(summary="", resolved_kind="pass")

        if parsed.movement is not None:
            self._resolve_movement(state, actor, parsed.movement.kind.value, result)

        if parsed.primary is not None:
            kind = parsed.primary.kind.value
            if kind in (ActionKind.ATTACK_LIGHT.value, ActionKind.ATTACK_HEAVY.value):
                self._resolve_attack(state, actor, parsed.primary.target_id,
                                     heavy=kind == ActionKind.ATTACK_HEAVY.value,
                                     roll=roll, result=result)
            elif kind == ActionKind.ENV_INTERACT.value:
                self._resolve_env(state, actor, parsed.primary.text, result)
            elif kind == ActionKind.TECHNIQUE.value:
                tid = parsed.primary.technique_id
                if tid is None:
                    result.summary += (f"{actor.name} gathers power — but no approved "
                                       "technique was named. ")
                    result.resolved_kind = "technique_unnamed"
                else:
                    self._resolve_cast(state, actor.id, tid,
                                       parsed.primary.target_id, result, roll=roll)
            elif kind == ActionKind.MOVE.value:
                self._resolve_movement(state, actor, kind, result)

        if parsed.reaction is not None and parsed.reaction.kind == ActionKind.BLOCK:
            st.spend_flat(actor, st.BASE_COSTS[ActionKind.BLOCK.value])
            if "blocking" not in actor.conditions:
                actor.conditions.append("blocking")
            result.summary += f"{actor.name} raises a guard. "

        if not result.summary:
            result.summary = f"{actor.name} holds position."
        self._emit(state, EventKind.PLAYER_ACTION, actor_id,
                   {"action": "turn", "text": parsed.raw_text,
                    "resolved": result.resolved_kind}, result)
        self._check_over(state, result)
        return result

    # -- movement -------------------------------------------------------
    def _resolve_movement(self, state: CombatState, actor: Combatant,
                          kind: str, result: TurnResult) -> None:
        foes = state.living_enemies_of(actor.id)
        if not foes:
            return
        steps = 2 if kind == ActionKind.SPRINT.value else 1
        if kind == ActionKind.SPRINT.value:
            st.spend_flat(actor, st.BASE_COSTS[ActionKind.SPRINT.value])
        # Close toward the nearest foe.
        target = min(foes, key=lambda c: state.get_range(actor.id, c.id).order())
        band = state.close_distance(actor.id, target.id, steps)
        actor.position = f"closing on {target.name}"
        result.summary += f"{actor.name} moves toward {target.name} ({band.value}). "

    # -- attacks --------------------------------------------------------
    def _resolve_attack(self, state: CombatState, actor: Combatant,
                        target_id: str | None, *, heavy: bool,
                        roll: int, result: TurnResult) -> None:
        kind = ActionKind.ATTACK_HEAVY.value if heavy else ActionKind.ATTACK_LIGHT.value
        spend = st.spend_stamina(actor, kind)
        result.weakened = spend.weakened
        if target_id is None or target_id not in state.combatants:
            result.summary += f"{actor.name} lashes out at nothing. "
            result.resolved_kind = "attack_no_target"
            return
        target = state.combatants[target_id]
        band = state.get_range(actor.id, target.id)
        ranged = actor.weapon.ranged

        if not ranged and band != RangeBand.ENGAGED:
            result.summary += (f"{actor.name} cannot reach {target.name} "
                               f"({band.value}) — too far for melee. ")
            result.resolved_kind = "attack_out_of_range"
            return
        if ranged and band == RangeBand.DISTANT:
            result.summary += f"{target.name} is beyond {actor.name}'s range. "
            result.resolved_kind = "attack_out_of_range"
            return
        if ranged and band == RangeBand.ENGAGED:
            result.summary += f"{actor.name} fires point-blank at {target.name}. "

        dc = 8 if not ranged else 8 + target.cover.ranged_bonus()
        check = roll_check(
            CheckRequest(situational_mod=inj.attack_penalty(actor, heavy=heavy), dc=dc),
            roll=roll,
        )
        if check.outcome not in _HIT_OUTCOMES:
            result.summary += f"{actor.name} misses {target.name}. "
            result.resolved_kind = "attack_miss"
            return

        damage = actor.weapon.damage + (4 if heavy else 0)
        damage += inj.attack_penalty(actor, heavy=heavy)
        if "blocking" in target.conditions:
            damage = max(0, damage // 2)
            target.conditions.remove("blocking")
            result.summary += f"{target.name} blocks much of it. "
        damage = max(1, damage - target.armour.reduction)
        if spend.weakened:
            damage = max(1, damage // 2)
            result.summary += f"{actor.name} is exhausted — the blow lands weakly. "
        self._apply_damage(state, actor.name, target, damage, result,
                           heavy=heavy, source="attack")
        result.resolved_kind = "attack_hit" if not heavy else "attack_heavy_hit"

    def _apply_damage(self, state: CombatState, source_name: str, target: Combatant,
                      damage: int, result: TurnResult, *, heavy: bool,
                      source: str, critical: bool = False) -> None:
        if target.status == "dead":
            return
        target.hp -= damage
        result.damage_dealt[target.id] = result.damage_dealt.get(target.id, 0) + damage
        result.summary += f"{source_name} {source} hits {target.name} for {damage}. "
        if inj.should_inflict(damage, target, critical=critical):
            kind = inj.pick_injury(damage, target.max_hp)
            inj.inflict(target, kind)
            result.injury_inflicted[target.id] = kind
            result.summary += f"{target.name} suffers {kind}! "
        if target.hp <= 0:
            status = resolve_zero_hp(target, state.mode)
            if status == "dead":
                result.summary += f"{target.name} dies. "
            else:
                result.summary += f"{target.name} is {status}. "

    # -- environment ----------------------------------------------------
    def _resolve_env(self, state: CombatState, actor: Combatant,
                     text: str, result: TurnResult) -> None:
        st.spend_flat(actor, st.BASE_COSTS[ActionKind.ENV_INTERACT.value])
        feature = state.find_env(text)
        if feature is None:
            # The object is not here: say so. Never rewrite into an Attack.
            result.summary += (f"{actor.name} reaches for something that isn't there "
                               f"({text.strip()!r} finds no purchase). ")
            result.resolved_kind = "env_missing"
            self._emit(state, EventKind.PLAYER_ACTION, actor.id,
                       {"action": "env_interact", "success": False, "text": text}, result)
            return
        feature.broken = True
        targets = [state.combatants[cid] for cid in feature.linked_targets
                   if cid in state.combatants and state.combatants[cid].alive]
        named = self._named_foe(state, actor.id, text)
        if named is not None and all(t.id != named.id for t in targets):
            targets.append(named)
        if not targets:
            result.summary += (f"{actor.name} {self._env_verb(text)} the {feature.name} — "
                               "it crashes down, but no one is under it. ")
            result.resolved_kind = "env_no_effect"
        else:
            for t in targets:
                # Falling architecture ignores cover.
                self._apply_damage(state, f"{actor.name}'s {feature.name}",
                                   t, max(1, feature.effect_damage), result,
                                   heavy=False, source="environment", critical=False)
            result.resolved_kind = "env_interact"
        self._emit(state, EventKind.PLAYER_ACTION, actor.id,
                   {"action": "env_interact", "success": True,
                    "feature": feature.name,
                    "targets": [t.id for t in targets]}, result)

    @staticmethod
    def _named_foe(state: CombatState, actor_id: str, text: str):
        lowered = text.lower()
        for c in state.living_enemies_of(actor_id):
            if c.name.lower() in lowered:
                return c
        return None

    @staticmethod
    def _env_verb(text: str) -> str:
        lowered = text.lower()
        for verb in ("cut", "drop", "topple", "shove", "light", "throw", "smash"):
            if verb in lowered:
                if verb == "cut":
                    return "cuts the rope of"
                if verb == "drop":
                    return "drops"
                return f"{verb}s"
        return "uses"

    # -- casting --------------------------------------------------------
    def _resolve_cast(self, state: CombatState, caster_id: str, technique_id: str,
                      target_id: str | None, result: TurnResult, *, roll: int = 10) -> None:
        caster = state.combatants[caster_id]
        try:
            technique = get_technique(technique_id)
        except UnknownTechniqueError as exc:
            result.summary += str(exc) + " "
            result.resolved_kind = "technique_unknown"
            return
        st.spend_flat(caster, technique.stamina_cost)
        if technique.healing and not technique.damage:
            ally = state.combatants.get(target_id or "") or caster
            gain = min(ally.max_hp - ally.hp, technique.healing)
            ally.hp += gain
            result.summary += (f"{caster.name} casts {technique.name} on {ally.name} "
                               f"(+{gain} HP). ")
            result.resolved_kind = "cast_heal"
        else:
            if target_id is None or target_id not in state.combatants:
                result.summary += f"{caster.name}'s {technique.name} finds no target. "
                result.resolved_kind = "cast_no_target"
                return
            target = state.combatants[target_id]
            self._apply_damage(state, f"{caster.name}'s {technique.name}",
                               target, technique.damage, result,
                               heavy=False, source="spell")
            result.resolved_kind = "cast_hit"
        self._emit(state, EventKind.PLAYER_ACTION, caster_id,
                   {"action": "cast", "technique": technique.id}, result)

    def cast(self, state: CombatState, caster_id: str, technique_id: str,
             target_id: str | None = None) -> TurnResult:
        result = TurnResult(summary="")
        self._resolve_cast(state, caster_id, technique_id, target_id, result)
        if not result.summary:
            result.summary = "Nothing happens."
        self._check_over(state, result)
        return result

    # -- intents --------------------------------------------------------
    def set_intent(self, state: CombatState, enemy_id: str, kind: str,
                   target_id: str | None = None) -> str:
        enemy = state.combatants[enemy_id]
        enemy.intent = EnemyIntent(enemy_id=enemy_id, kind=kind, target_id=target_id)
        return self.intent_line(state, enemy_id)

    def intent_line(self, state: CombatState, enemy_id: str) -> str:
        enemy = state.combatants[enemy_id]
        if enemy.intent is None:
            return f"{enemy.name} — waiting"
        target_name = None
        if enemy.intent.target_id and enemy.intent.target_id in state.combatants:
            target_name = state.combatants[enemy.intent.target_id].name
        return enemy.intent.describe(enemy.name, target_name)

    def intent_board(self, state: CombatState) -> list[str]:
        return [self.intent_line(state, c.id) for c in state.combatants.values()
                if c.is_enemy and c.alive]

    # -- rounds ---------------------------------------------------------
    def end_round(self, state: CombatState) -> None:
        for c in state.combatants.values():
            if c.status == "dead":
                continue
            st.regenerate(c)
            lost = inj.bleed_tick(c)
            if lost and c.hp <= 0:
                resolve_zero_hp(c, state.mode)
        state.round_no += 1

    # -- helpers --------------------------------------------------------
    def _check_over(self, state: CombatState, result: TurnResult) -> None:
        sides = {c.is_enemy for c in state.combatants.values() if c.alive}
        if len(sides) <= 1:
            state.over = True
            self._emit(state, EventKind.COMBAT_RESOLVED, None,
                       {"rounds": state.round_no}, result)

    def _emit(self, state: CombatState, kind: EventKind, actor_id: str | None,
              payload: dict, result: TurnResult | None = None) -> GameEvent:
        event = GameEvent(kind=kind, campaign_id=state.campaign_id,
                          actor_id=actor_id, payload=dict(payload))
        event_log.append(event)
        if result is not None:
            result.events.append(event)
        return event

    def snapshot(self, state: CombatState) -> dict:
        return state.to_snapshot()
