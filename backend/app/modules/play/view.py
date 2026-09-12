"""GET /state payload: the live game-state view the adventure screen renders.

Shape mirrors the frontend's gameState contract
({location, time, npcs[], leads[], interactables[], party[], feed[]}) plus a
live ``character`` block and a ``completed`` flag for the slice epilogue.
"""
from __future__ import annotations

from app.modules.play.state import PlayState

LOCATION_NAMES: dict[str, str] = {
    "lantern-inn": "The Lantern Inn, Ravenford",
    "northern-road": "The Northern Road",
    "market": "Ravenford Market",
    "old-monastery": "The Old Monastery",
    "cellar": "Beneath the Old Monastery",
}

INTERACTABLES: dict[str, list[str]] = {
    "lantern-inn": ["hearth", "notice board", "marla's ledger", "cellar door", "Marla", "borin"],
    "northern-road": ["wagon ruts", "woodline", "distant lights"],
    "market": ["silver stall", "peddler row", "Sella"],
    "old-monastery": ["porter's door", "cellar stairs", "bell tower"],
    "cellar": ["the pen", "travelers", "lantern racks"],
}


def location_name(state: PlayState) -> str:
    return LOCATION_NAMES.get(state.location, state.location)


def time_label(state: PlayState) -> str:
    return f"Day {state.day} · {state.hour:02d}:{state.minute:02d} · rain"


def _inn_npcs(state: PlayState) -> list[dict]:
    npcs = [{"name": "Marla Voss", "note": "wiping a cup, watching the door"}]
    if state.borin_down:
        npcs.append({"name": "Borin", "note": "sleeping it off on the porch"})
    else:
        npcs.append({"name": "Borin", "note": "nursing a grudge by the fire"})
    return npcs


def npcs_present(state: PlayState) -> list[dict]:
    """NPCs at the current location with a short note for the UI."""
    if state.location == "lantern-inn":
        return _inn_npcs(state)
    if state.location == "market":
        return [
            {"name": "Sella Voss", "note": "guild silver factor, watching the scales"},
            {"name": "Tomm Ash", "note": "peddler, visibly nervous"},
        ]
    if state.location == "old-monastery":
        return [{"name": "Brother Anselm", "note": "porter, frightened of the cellar stairs"}]
    return []


def npc_names(state: PlayState) -> list[str]:
    """First names only — the pipeline scene context wants short handles."""
    return [n["name"].split()[0] for n in npcs_present(state)]


def lead_titles(state: PlayState) -> list[str]:
    if state.lead_stage == "unheard":
        return []
    titles = ["Missing Travelers"]
    if state.lead_stage in ("investigating", "solved") or "lanterns" in state.clues:
        titles.append("The Monastery Lights")
    return titles


def interactables(state: PlayState) -> list[str]:
    items = list(INTERACTABLES.get(state.location, []))
    if state.location == "lantern-inn" and state.borin_down:
        items = [i for i in items if i != "borin"]
    return items


def party(state: PlayState) -> list[dict]:
    pc = state.pc
    hp = pc.get("hp", {})
    return [{"name": pc.get("name", "the hero").split()[0], "hp": f"{hp.get('cur', 0)}/{hp.get('max', 0)}"}]


def character_block(state: PlayState) -> dict:
    """The adventure/character screens' live character sheet."""
    return dict(state.pc)


def game_state_payload(state: PlayState) -> dict:
    return {
        "location": location_name(state),
        "time": time_label(state),
        "npcs": npcs_present(state),
        "leads": lead_titles(state),
        "interactables": interactables(state),
        "party": party(state),
        "feed": list(state.feed),
        "character": character_block(state),
        "completed": state.completed,
        "lead_stage": state.lead_stage,
        "clues": list(state.clues),
    }
