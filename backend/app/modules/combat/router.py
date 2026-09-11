"""FastAPI surface for the combat bounded context."""
from __future__ import annotations

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel

from app.modules.combat.companions import (
    Companion,
    CompanionOrder,
    transfer_to_inventory,
)
from app.modules.combat.engine import CombatEngine
from app.modules.combat.magic import APPROVED_TECHNIQUES, UnknownTechniqueError
from app.modules.combat.models import Combatant, EnvFeature, RangeBand
from app.modules.combat.rest import long_rest, short_rest
from app.modules.combat.turns import ParsedTurn

router = APIRouter(prefix="/combat", tags=["combat"])
engine = CombatEngine()
companions: dict[str, Companion] = {}


class StartRequest(BaseModel):
    combatants: list[Combatant]
    campaign_id: str = "default"
    mode: str = "standard"
    env: list[EnvFeature] = []


class TurnRequest(BaseModel):
    actor_id: str
    text: str | None = None
    turn: ParsedTurn | None = None
    roll: int = 10


class IntentRequest(BaseModel):
    enemy_id: str
    kind: str = "attack"
    target_id: str | None = None


class CastRequest(BaseModel):
    caster_id: str
    technique_id: str
    target_id: str | None = None


class RestRequest(BaseModel):
    combatant_ids: list[str] = []
    safe: bool = True
    danger: int = 0


@router.post("/start")
def start_combat(req: StartRequest) -> dict:
    state = engine.start(req.combatants, campaign_id=req.campaign_id,
                         mode=req.mode, env=req.env)
    return state.to_snapshot()


@router.get("/{combat_id}")
def get_combat(combat_id: str) -> dict:
    try:
        return engine.get(combat_id).to_snapshot()
    except KeyError:
        raise HTTPException(404, "combat not found") from None


@router.post("/{combat_id}/turn")
def submit_turn(combat_id: str, req: TurnRequest) -> dict:
    try:
        state = engine.get(combat_id)
    except KeyError:
        raise HTTPException(404, "combat not found") from None
    result = engine.submit_turn(state, req.actor_id, text=req.text,
                                turn=req.turn, roll=req.roll)
    return result.model_dump()


@router.post("/{combat_id}/intent")
def declare_intent(combat_id: str, req: IntentRequest) -> dict:
    try:
        state = engine.get(combat_id)
        line = engine.set_intent(state, req.enemy_id, req.kind, req.target_id)
    except KeyError:
        raise HTTPException(404, "combat or combatant not found") from None
    return {"intent": line, "board": engine.intent_board(state)}


@router.get("/{combat_id}/intents")
def read_intents(combat_id: str) -> dict:
    try:
        state = engine.get(combat_id)
    except KeyError:
        raise HTTPException(404, "combat not found") from None
    return {"board": engine.intent_board(state)}


@router.post("/{combat_id}/cast")
def cast_spell(combat_id: str, req: CastRequest) -> dict:
    try:
        state = engine.get(combat_id)
    except KeyError:
        raise HTTPException(404, "combat not found") from None
    if req.technique_id not in APPROVED_TECHNIQUES:
        raise HTTPException(400, str(UnknownTechniqueError(req.technique_id)))
    result = engine.cast(state, req.caster_id, req.technique_id, req.target_id)
    return result.model_dump()


@router.get("/magic/techniques")
def list_techniques() -> dict:
    return {"techniques": [t.model_dump() for t in APPROVED_TECHNIQUES.values()]}


@router.post("/rest/short")
def do_short_rest(req: RestRequest) -> dict:
    party = [Combatant(id=cid, name=cid) for cid in req.combatant_ids]
    return short_rest(party).model_dump()


@router.post("/rest/long")
def do_long_rest(req: RestRequest) -> dict:
    party = [Combatant(id=cid, name=cid) for cid in req.combatant_ids]
    return long_rest(party, safe=req.safe, danger=req.danger).model_dump()


@router.post("/companions")
def recruit(companion: Companion) -> dict:
    companions[companion.id] = companion
    return companion.model_dump()


@router.post("/companions/{companion_id}/order")
def order_companion(companion_id: str, order: CompanionOrder) -> dict:
    companion = companions.get(companion_id)
    if companion is None:
        raise HTTPException(404, "companion not found")
    return companion.react(order).model_dump()


@router.post("/inventory/check")
def inventory_check(payload: dict) -> dict:
    """Demonstrates the companion guard for cross-module intake flows."""
    obj: object = companions.get(str(payload.get("companion_id", "")), object())
    try:
        transfer_to_inventory(obj, [])
    except TypeError as exc:
        raise HTTPException(400, str(exc)) from None
    return {"ok": True}


_RANGE_ORDER = [b.value for b in RangeBand]
