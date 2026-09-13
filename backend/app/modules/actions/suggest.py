"""Suggested-action buttons: contextual, always alongside free text (t_2aa58729)."""
from __future__ import annotations

from typing import Any

from pydantic import BaseModel


class SceneContext(BaseModel):
    npcs_present: list[str] = []
    #: NPC first-name -> the strongest memories they keep about the player.
    npc_memories: dict[str, list[str]] = {}
    #: NPC first-name -> live mood {"mood": word, "intensity": 0..1} (slice 3).
    npc_moods: dict[str, dict[str, Any]] = {}
    exits: list[str] = []
    items_visible: list[str] = []
    rumors_available: bool = False
    in_combat: bool = False
    can_rest: bool = True


class SuggestedAction(BaseModel):
    label: str
    command: str
    kind: str = "button"
    #: Free-text input is always available alongside buttons.
    free_text_enabled: bool = True


def generate_suggestions(scene: SceneContext, max_buttons: int = 6) -> list[SuggestedAction]:
    """Build contextual buttons from scene state (GDD §24, §71)."""
    out: list[SuggestedAction] = []
    if scene.in_combat:
        out += [
            SuggestedAction(label="⚔️ Attack", command="I attack the nearest foe!"),
            SuggestedAction(label="🛡️ Defend", command="I raise my guard and defend."),
            SuggestedAction(label="🏃 Retreat", command="I fall back toward the exit."),
        ]
        return out[:max_buttons]
    if scene.npcs_present:
        first = scene.npcs_present[0]
        out.append(SuggestedAction(label=f"💬 Talk to {first}", command=f"I greet {first} and ask what brings them here."))
        out.append(SuggestedAction(label="🎲 Attempt Persuasion", command=f"I try to persuade {first}."))
    out.append(SuggestedAction(label="🔍 Inspect the area", command="I carefully inspect the area."))
    if scene.items_visible:
        out.append(SuggestedAction(label=f"✋ Examine {scene.items_visible[0]}",
                                   command=f"I examine the {scene.items_visible[0]} closely."))
    else:
        out.append(SuggestedAction(label="🌙 Sneak", command="I move quietly and stay out of sight."))
    if scene.rumors_available:
        out.append(SuggestedAction(label="🍺 Ask about rumors", command="I ask around about any rumors or news."))
    if scene.exits:
        out.append(SuggestedAction(label=f"🚪 Leave ({scene.exits[0]})", command=f"I head to {scene.exits[0]}."))
    if scene.can_rest:
        out.append(SuggestedAction(label="🔥 Rest", command="I take a short rest."))
    return out[:max_buttons]
