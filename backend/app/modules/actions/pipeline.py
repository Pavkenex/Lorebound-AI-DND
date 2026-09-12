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
from app.modules.narrator.authority import AuthorityEngine, EngineProposal
from app.modules.narrator.prompts import PromptContext
from app.modules.narrator.prefs import ContentPrefs
from app.modules.narrator.schemas import Length, NarratorOutput
from app.modules.narrator.service import acknowledge, narrate
from app.modules.narrator.validator import (
    WorldSnapshot,
    log_report,
    repair_narration,
    validate_narration,
)
from app.modules.rules.checks import CheckResult, apply_social_policy, roll_check


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

    def orchestrate(self, action: ActionInput,
                    state: dict[str, Any] | None = None,
                    prefs: ContentPrefs | None = None) -> PipelineResult:
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
            if req.social:
                req, needed = apply_social_policy(req)
                if not needed:
                    continue  # RP resolves it; no roll (t_2e94122b).
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
        prompt_ctx = PromptContext(
            location=world.location or "Unknown",
            scene=f"PC acts: {intent.summary}",
            player_character={"name": state.get("pc_name", "the hero")},
            npcs=[{"name": n} for n in action.scene.npcs_present],
            world_facts=world.facts,
            recent_events=recent,
            player_action=text,
            mechanical_result=mech,
            length=action.length,
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
