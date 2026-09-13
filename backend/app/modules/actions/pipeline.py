"""10-step narrator pipeline orchestration (t_5cfaf67f, GDD §79).

Steps: 1 input -> 2 intent -> 3 mechanics -> 4 rules -> 5 state ->
6 memory (retrieved-only) -> 7 narrator -> 8 prose+suggestions ->
9 validator -> 10 player payload.

The AI is never the database: the orchestrator owns state transitions; the
model only receives structured results and returns prose/proposals, which
the AuthorityEngine reviews before anything is applied.
"""
from __future__ import annotations

import random
from typing import Any

from pydantic import BaseModel, Field

from app.core.events import EventKind, GameEvent
from app.modules.actions.interpreter import Intent, parse, sanitize_for_narrator, to_check_requests
from app.modules.actions.suggest import SceneContext, generate_suggestions
from app.modules.ai.metering import MeterRegistry
from app.modules.ai.providers import Provider
from app.modules.memory.npc_memory import named_npc, npc_slug
from app.modules.narrator.authority import AuthorityEngine, EngineProposal
from app.modules.narrator.prefs import ContentPrefs
from app.modules.narrator.prompts import PromptContext
from app.modules.narrator.schemas import Length, NarratorOutput
from app.modules.narrator.service import acknowledge, narrate
from app.modules.narrator.validator import (
    WorldSnapshot,
    log_report,
    repair_narration,
    validate_narration,
)
from app.modules.npc.personality import SocialContext, approach_for_skill, social_adjustment
from app.modules.rules.checks import (
    CheckResult,
    CheckSuspension,
    apply_social_policy,
    is_long_odds,
    is_trivial,
    roll_check,
    rp_dc_shift,
)


def _signed(value: int) -> str:
    return f"+{value}" if value > 0 else f"−{abs(value)}"


def scene_line(summary: str, state: dict[str, Any]) -> str:
    """The narrator's [Scene] line: where the moment happens, its goal (§7/§8).

    Scene flow rides the same prompt block the location does: a micro-scene
    ("the upstairs room") reads as the scene while the map keeps the inn, and
    the scene's goal tells the model what this moment is *for*.
    """
    label = str(state.get("scene_label") or "").strip()
    goal = str(state.get("scene_goal") or "").strip()
    phase = str(state.get("scene_state") or "").strip()
    parts: list[str] = []
    if label:
        parts.append(f"{label} ({phase})" if phase else label)
    parts.append(f"PC acts: {summary}")
    if goal:
        parts.append(f"scene goal: {goal}")
    return " — ".join(parts)


class ActionInput(BaseModel):
    campaign_id: str = "default"
    character_id: str | None = None
    text: str
    length: str = Length.STANDARD.value
    scene: SceneContext = Field(default_factory=SceneContext)
    seed_roll: int | None = None  # test hook: deterministic d20


class PipelineResult(BaseModel):
    ack: dict[str, Any]
    intent: Intent
    checks: list[CheckResult] = Field(default_factory=list)
    narration: NarratorOutput
    validator_action: str = "accept"
    events: list[GameEvent] = Field(default_factory=list)
    meter: dict[str, Any] = Field(default_factory=dict)
    state: dict[str, Any] = Field(default_factory=dict)


class Pipeline:
    """Owns state transitions end to end. Construct per campaign/session."""

    def __init__(self, provider: Provider | None = None,
                 meter: MeterRegistry | None = None,
                 authority: AuthorityEngine | None = None,
                 rng: random.Random | None = None) -> None:
        self.provider = provider
        self.meter = meter or MeterRegistry()
        self.authority = authority or AuthorityEngine()
        self.rng = rng or random.Random()

    def _social_context(self, req, action: ActionInput, text: str) -> SocialContext | None:
        """The character a social check is aimed at, or None (§6).

        Names are matched through the shared alias table, and only characters
        actually in the room can be aimed at — "I persuade the empty room to
        be quiet" is aimed at nobody, and stays a role-play beat with no die.
        """
        scene = action.scene
        slug = named_npc(text, list(scene.npcs_present))
        if not slug:
            return None
        key = next((n for n in scene.npcs_present if npc_slug(n) == slug), slug)
        mood = scene.npc_moods.get(key) or {}
        return SocialContext(
            slug=slug,
            approach=approach_for_skill(req.skill, text),
            mood=str(mood.get("mood") or ""),
            mood_intensity=float(mood.get("intensity") or 0.0),
            attitude=int(scene.npc_attitudes.get(key, 0) or 0),
        )

    def orchestrate(self, action: ActionInput,
                    state: dict[str, Any] | None = None,
                    prefs: ContentPrefs | None = None,
                    suspend_on_check: bool = False) -> PipelineResult:
        state = dict(state or {})
        campaign_id = action.campaign_id
        events: list[GameEvent] = []
        meter = self.meter.for_campaign(campaign_id)
        meter.record_action()

        # 1. Input.
        text = action.text or ""
        # 2. Intent model (local deterministic; player text never a world fact).
        intent = parse(text)
        events.append(GameEvent(kind=EventKind.PLAYER_ACTION, campaign_id=campaign_id,
                                payload={"text": text, **sanitize_for_narrator(intent)}))

        # 3-4. Required mechanics + rules engine.
        results: list[CheckResult] = []
        for req in to_check_requests(intent, campaign_id, action.character_id):
            base_dc = req.dc
            why = ""
            if req.social:
                social_ctx = self._social_context(req, action, text)
                if social_ctx is not None:
                    # Aimed at a character who is actually in the room: the
                    # attempt is contested, so it rolls (§6) at a DC moved by
                    # personality (§5), the live mood (§4) and the meter (§3).
                    req = req.model_copy(update={"npc_resistant": True})
                parts = list(social_adjustment(social_ctx, base_dc).parts) if social_ctx else []
                rp_shift = rp_dc_shift(req.rp_quality)
                if rp_shift:
                    parts.append(f"role-play ({_signed(rp_shift)})")
                why = " · ".join(parts)
                req, needed = apply_social_policy(req, social=social_ctx)
                if not needed:
                    continue  # RP resolves it; no roll (t_2e94122b).
            if (suspend_on_check and action.seed_roll is None
                    and not req.hidden and not is_trivial(req.difficulty)):
                # The player must throw this one: stop before the roll and let
                # the caller surface a pending check (two-phase /act).
                raise CheckSuspension({
                    "label": f"{req.skill} check",
                    "skill": req.skill,
                    "attribute": None,
                    "attribute_mod": req.attribute_mod,
                    "skill_mod": req.skill_mod,
                    "total_mod": req.attribute_mod + req.skill_mod,
                    "dc": req.dc,
                    "dc_base": base_dc,
                    "dc_why": why or None,
                    "long_odds": is_long_odds(req.dc, req.attribute_mod + req.skill_mod),
                    "difficulty": req.difficulty,
                })
            res = roll_check(req, roll=action.seed_roll, rng=self.rng)
            results.append(res)
            if res.surfaced:  # hidden/trivial stay silent.
                events.append(GameEvent(kind=EventKind.CHECK_RESOLVED, campaign_id=campaign_id,
                                        payload={"skill": req.skill, "roll": res.roll,
                                                 "total": res.total, "dc": res.dc,
                                                 "outcome": res.outcome.value
                                                 if hasattr(res.outcome, "value") else res.outcome,
                                                 "hidden": res.hidden}))

        # 5. World state update lives in the engine; proposals reviewed below.
        # 6. Memory retrieval (retrieved-only slices; never full transcript).
        recent = [{"kind": e.kind.value if hasattr(e.kind, "value") else e.kind,
                   "payload": e.payload} for e in events[-8:]]
        world = WorldSnapshot(inventory=list(state.get("inventory", [])),
                              alive=dict(state.get("npcs_alive", {})),
                              location=str(state.get("location", "")),
                              known_locations=list(state.get("known_locations", [])),
                              facts=list(state.get("facts", [])))

        # 7-8. Narrator (exactly one primary call) + prose + suggestions.
        mech = {"checks": [r.model_dump() for r in results],
                "world_fact_attempt": intent.world_fact_attempt,
                "asserted_claims": intent.asserted_claims}

        def _npc_ctx(name: str) -> dict[str, Any]:
            """One present NPC for the prompt: memories + live mood + disposition."""
            entry: dict[str, Any] = {
                "name": name,
                "remembers": list(action.scene.npc_memories.get(name, [])),
            }
            mood = action.scene.npc_moods.get(name) or {}
            word = str(mood.get("mood") or "")
            level = float(mood.get("intensity") or 0.0)
            if word and level > 0:
                entry["mood"] = word
                entry["mood_intensity"] = level
            disposition = str(action.scene.npc_dispositions.get(name) or "").strip()
            if disposition:
                entry["disposition"] = disposition
            return entry

        prompt_ctx = PromptContext(
            location=world.location or "Unknown",
            scene=scene_line(intent.summary, state),
            player_character={"name": state.get("pc_name", "the hero")},
            npcs=[_npc_ctx(n) for n in action.scene.npcs_present],
            world_facts=world.facts,
            recent_events=recent,
            player_action=text,
            mechanical_result=mech,
            length=action.length,
            # Continuity (§25): the last beats the player has already read ride
            # the prompt, so the narrator continues the tale instead of
            # re-deriving the scene from scratch every turn.
            chronicle=list(state.get("chronicle") or []),
            # Saga digest (#4): the shape of the whole tale so far — act one
            # still in mind at act ten.
            saga=str(state.get("saga") or ""),
        )
        suggestions = [s.model_dump() for s in generate_suggestions(action.scene)]
        output, _bundle = narrate(prompt_ctx, provider=self.provider,
                                  meter=self.meter, campaign_id=campaign_id,
                                  suggestions=[{"label": s["label"], "command": s["command"]}
                                               for s in suggestions],
                                  prefs=prefs)

        # If the player tried to author facts, the narration must not honor them.
        if intent.world_fact_attempt:
            output.narration += (" Your words alone conjure nothing that was not already here.")

        # 9. Output validator (+ authority review of any proposed events).
        report = validate_narration(output.narration, world)
        if not report.ok:
            if report.action.value == "regenerate":
                # One bounded retry, then fall back to repaired text.
                output.narration = repair_narration(output.narration, report) or \
                    "The attempt falters; the scene remains as it was."
            else:
                output.narration = repair_narration(output.narration, report)
            ev = log_report(report, campaign_id)
            if ev is not None:
                events.append(ev)
        for raw in output.proposed_events:
            try:
                prop = EngineProposal(**raw) if isinstance(raw, dict) else None
            except (ValueError, TypeError):
                prop = None
            if prop is None:
                events.append(GameEvent(
                    kind=EventKind.WORLD_EVENT_OCCURRED, campaign_id=campaign_id,
                    payload={"proposal": str(raw)[:120], "approved": False,
                             "discard_reason": "malformed proposal"}))
                continue
            decision = self.authority.review([prop], state, campaign_id)[0]
            if decision.approved:
                state = self.authority.apply(decision, state)

        # 10. Player payload.
        return PipelineResult(ack=acknowledge(intent.summary), intent=intent,
                              checks=results, narration=output,
                              validator_action=report.action.value,
                              events=events, meter=self.meter.report(campaign_id),
                              state=state)
