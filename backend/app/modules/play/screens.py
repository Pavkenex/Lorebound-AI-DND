"""Live screen payloads: character / skills / journal / map / inventory / companions.

Each builder takes the live :class:`PlayState` and returns the exact shape the
matching frontend screen already renders (its fixture contract), so the pages
go live without reshaping in the client.
"""
from __future__ import annotations

from app.content.ravenford import LOCATIONS, RUMOURS
from app.content.skills import SKILLS
from app.modules.exploration.leads import seed_caravan_graph
from app.modules.play.state import CLUES, PlayState
from app.modules.play.view import character_block
from app.modules.progression.skills import tier_for_xp

# Slice-world map layout on the 560x340 stage (match the fixture feel).
_MAP_POSITIONS: dict[str, tuple[int, int]] = {
    "ravenford": (250, 190),
    "lantern-inn": (140, 150),
    "market": (225, 115),
    "northern-road": (335, 105),
    "forest": (405, 140),
    "old-monastery": (465, 70),
    "watchtower": (425, 235),
}
_MAP_NOTES: dict[str, str] = {
    "ravenford": "Timber roofs, guild silver, and a notice board nobody likes reading.",
    "lantern-inn": "Marla's roadhouse — fire, rain, and every rumour in town.",
    "market": "Guild scales that pay over rate for silver, no questions asked.",
    "northern-road": "Where the travelers went, and the ruts turned.",
    "forest": "Lanterns between the trunks at night — too steady for will-o'-wisps.",
    "old-monastery": "The Quiet Order's hill — a shut door and a cold stair.",
    "watchtower": "The watch's rain-lashed tower; vanishing-traveler reports go nowhere.",
}
_MAP_DANGER: dict[str, int] = {"northern-road": 3, "forest": 2, "old-monastery": 2}

#: Journal graph layout on the 560x270 stage (deterministic, east-flowing thread).
_JOURNAL_LAYOUT: dict[str, tuple[int, int]] = {
    "missing-caravan": (90, 60),
    "destroyed-wagon": (280, 50),
    "silver-powder": (470, 75),
    "merchant-guild": (450, 185),
    "smugglers": (270, 215),
    "old-monastery": (95, 185),
}


def character_payload(state: PlayState) -> dict:
    return character_block(state)


def skills_payload(state: PlayState) -> dict:
    """The five slice skills with mastery XP earned in play."""
    by_key = {s.key: s for s in SKILLS}
    rows = []
    for key in by_key:
        skill = by_key[key]
        xp = int(state.skills.get(key, 0))
        tier = tier_for_xp(xp).value
        rows.append({
            "name": skill.name,
            "tier": tier,
            "xp": xp,
            "recent": state.skill_recent.get(key, []),
            "trainers": [],
            "practice": skill.example_uses[0] if skill.example_uses else "",
        })
    return {"list": rows, "notes": []}


def journal_payload(state: PlayState) -> dict:
    """Lead-graph reveal driven by the live mystery progress."""
    graph = seed_caravan_graph()
    revealed: dict[str, str] = {}
    if state.lead_stage != "unheard":
        revealed["missing-caravan"] = "confirmed"
    if "tracks" in state.clues:
        revealed["destroyed-wagon"] = "confirmed"
    if "ledger" in state.clues:
        revealed["silver-powder"] = "confirmed"
    if "ledger" in state.clues or "tracks" in state.clues:
        revealed["merchant-guild"] = "confirmed" if state.completed else "rumour"
    if "lanterns" in state.clues or state.solution_path:
        revealed["smugglers"] = "confirmed" if state.completed else "rumour"
        revealed["old-monastery"] = "confirmed" if state.completed else "new"

    nodes = []
    for node in graph.nodes.values():
        if node.id not in revealed:
            continue
        x, y = _JOURNAL_LAYOUT.get(node.id, (280, 135))
        nodes.append({"id": node.id, "label": node.label, "x": x, "y": y, "state": revealed[node.id]})
    kept = {n["id"] for n in nodes}
    edges = [[a, b] for a, b, _r in graph.edges if a in kept and b in kept]

    found = len(state.clues)
    if state.completed:
        detail = ("The thread runs its whole length now: the caravan, the guild's silver, "
                  "the tunnel under the monastery — and the travelers home. Nothing left "
                  "unwalked but the telling of it.")
    elif found:
        labels = ", ".join(CLUES[c].split("—")[0].strip() for c in state.clues)
        detail = f"Threads gathered ({found} of {len(CLUES)}): {labels}. Something holds them all."
    elif state.lead_stage != "unheard":
        detail = ("The thread starts here: two travelers, one road, and a town that would "
                  "rather talk about rain. Follow what they left behind.")
    else:
        detail = "A missing pair, a driverless wagon, and a town that would rather talk about rain."
    return {"nodes": nodes, "edges": edges, "detail": detail}


def map_payload(state: PlayState) -> dict:
    """Travel map: slice-world places known by play progress, roads, rumours, incidents."""
    known = set(state.visited) | {"ravenford", "lantern-inn"}
    if state.lead_stage != "unheard":
        known.add("old-monastery")
    if "lanterns" in state.clues or state.solution_path or state.completed:
        known.add("forest")
    if state.completed:
        known.add("old-monastery")

    places = []
    index: dict[str, int] = {}
    for loc in LOCATIONS:
        if loc.id not in known:
            continue
        index[loc.id] = len(places)
        x, y = _MAP_POSITIONS.get(loc.id, (280, 170))
        places.append({
            "name": loc.name,
            "x": x,
            "y": y,
            "known": True,
            "danger": _MAP_DANGER.get(loc.id, 0),
            "note": _MAP_NOTES.get(loc.id, ""),
        })
    roads: list[list[int]] = []
    seen: set[tuple[str, str]] = set()
    for loc in LOCATIONS:
        for other in loc.connects_to:
            pair = (loc.id, other) if loc.id < other else (other, loc.id)
            if pair in seen or loc.id not in index or other not in index:
                continue
            seen.add(pair)
            roads.append([index[loc.id], index[other]])
    incidents = (
        ["Resolved: the missing travelers walk home, and the guild has questions to answer."]
        if state.completed
        else ["Active: missing travelers on the North Road."]
    )
    return {"places": places, "roads": roads, "rumours": list(RUMOURS)[:3], "incidents": incidents}


def inventory_payload(state: PlayState) -> list[dict]:
    """Pack & purse: equipment + coin + story keepsakes."""
    def kind_of(name: str) -> str:
        lowered = name.lower()
        if any(w in lowered for w in ("sword", "bow", "knife", "blade")):
            return "Weapon"
        if any(w in lowered for w in ("cloak", "cord", "kit")):
            return "Gear"
        return "Keepsake"

    items = [{"name": item, "kind": kind_of(item), "note": ""} for item in state.pc.get("equipment", [])]
    items.append({
        "name": f"{state.silver} guilders",
        "kind": "Coin",
        "note": "enough for a week, barely" if state.silver < 20 else "a purse that sits heavier than it should",
    })
    if state.travelers_freed:
        items.append({
            "name": "A traveler's carved whistle",
            "kind": "Keepsake",
            "note": "given on the road home — you are owed a call, once you find the breath",
        })
    return items


def companions_payload(state: PlayState) -> list[dict]:  # noqa: ARG001 - shape parity
    """No companions are recruited in the vertical slice yet — honestly empty."""
    return []
