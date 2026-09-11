"""Combat bounded context: state, turns, stamina, intents, environment,
injuries, modes, magic, rest, companions, engine, router.
"""
from app.modules.combat.companions import Companion, CompanionOrder
from app.modules.combat.engine import CombatEngine
from app.modules.combat.magic import APPROVED_TECHNIQUES, School, get_technique
from app.modules.combat.models import (
    Armour,
    Combatant,
    CombatState,
    Cover,
    EnvFeature,
    RangeBand,
    Weapon,
)
from app.modules.combat.modes import CampaignMode

__all__ = [
    "APPROVED_TECHNIQUES",
    "Armour",
    "CampaignMode",
    "CombatEngine",
    "CombatState",
    "Combatant",
    "Companion",
    "CompanionOrder",
    "Cover",
    "EnvFeature",
    "RangeBand",
    "School",
    "Weapon",
    "get_technique",
]
