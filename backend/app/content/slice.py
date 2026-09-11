"""Vertical-slice assembly (GDD §116): 30–60 minutes of playable mystery.

One PC (Aric), one tavern (Lantern Inn), three NPCs, one mystery (Missing
Travelers, solvable several ways), one combat encounter, five usable skills,
plus inventory-screen and story-lead-screen data and a paced session outline.
"""

from __future__ import annotations

from dataclasses import dataclass, field

from app.content.fixture import ARIC, LANTERN_INN, MARLA, MISSING_TRAVELERS_LEAD
from app.content.skills import SKILL_KEYS

SLICE_NPCS: tuple[dict, ...] = (
    dict(MARLA, memory="persistent: remembers talk/steal/fight/lead across visits"),
    {
        "id": "borin",
        "name": "Borin",
        "role": "Drunk mercenary (combat encounter trigger)",
        "description": "Spoiling for a fight by the fire; backs down if beaten or cowed.",
    },
    {
        "id": "sella",
        "name": "Sella Voss",
        "role": "Guild silver factor (social solution route)",
        "description": "Polite, evasive, rich — cracks under pressure or a good price.",
    },
)

MYSTERY: dict = {
    "id": "missing-travelers",
    "title": "Missing Travelers",
    "brief": (
        "Two travelers bound for the Old Monastery vanished on the Northern Road. "
        "Find them — Marla's ledger, the road mud, and three rumours point the way."
    ),
    "solutions": [
        {
            "id": "talk-it-out",
            "name": "The Honest Plea",
            "skills": ["persuasion"],
            "path": "Befriend Marla and Sella; Sella confesses the pen location.",
        },
        {
            "id": "lean-on-them",
            "name": "The Hard Stare",
            "skills": ["intimidation"],
            "path": "Cow Borin, then pressure Sella into naming the carriers.",
        },
        {
            "id": "follow-the-clues",
            "name": "The Ledger Trail",
            "skills": ["investigation"],
            "path": "Match ledger entries to mud and tunnel tracks; find the cellar pen.",
        },
        {
            "id": "shadow-them",
            "name": "The Night Watch",
            "skills": ["stealth"],
            "path": "Tail the lantern-bearers through the forest to the tunnel mouth.",
        },
        {
            "id": "blades-out",
            "name": "The Road Ambush",
            "skills": ["swordsmanship"],
            "path": "Survive the Northern Road ambush and make a carrier talk.",
        },
    ],
}

COMBAT_ENCOUNTER: dict = {
    "id": "road-ambush",
    "name": "Northern Road Ambush",
    "trigger": "Travel the Northern Road after dark while investigating.",
    "foes": ["Fenn (smuggler carrier)", "Ossia (caravan guard)"],
    "win": "A carrier surrenders and reveals the cellar pen.",
    "lose": "You wake at the Lantern Inn, tended by Marla — who remembers.",
    "nonviolent_outs": ["persuasion", "intimidation", "stealth"],
}

INVENTORY_SCREEN: dict = {
    "title": "Pack",
    "owner": "aric",
    "silver": ARIC["silver"],
    "items": [
        {"id": "cloak", "name": "Traveler's cloak", "use": "Keeps off the worst rain."},
        {"id": "short-sword", "name": "Short sword", "use": "Swordsmanship checks."},
        {"id": "silver-rings", "name": "3 silver rings", "use": "Bribes and bargaining."},
    ],
    "skills": list(SKILL_KEYS),
}

LEADS_SCREEN: dict = {
    "title": "Story Leads",
    "leads": [
        dict(
            MISSING_TRAVELERS_LEAD,
            stage="rumored",
            clues_found=[],
            clues_total=3,
        )
    ],
}


@dataclass(frozen=True)
class SessionBeat:
    id: str
    title: str
    minutes: int
    text: str


SESSION_BEATS: tuple[SessionBeat, ...] = (
    SessionBeat("arrival", "Arrival in the rain", 5,
                "Opening scene: crest the rise, see Ravenford, reach the Lantern Inn."),
    SessionBeat("inn", "The Lantern Inn", 10,
                "Meet Marla, Borin, and Sella. Hear the three rumours. Discover the lead."),
    SessionBeat("investigate", "Following threads", 15,
                "Inspect rooms, search the market, question the watch — gather clues."),
    SessionBeat("ambush", "Northern Road Ambush", 10,
                "Combat encounter (winnable, avoidable, survivable)."),
    SessionBeat("cellar", "The monastery cellar", 10,
                "Confront the truth behind the lights and the silver; free the travelers."),
    SessionBeat("return", "Debts and dawn", 5,
                "Return to Marla, who remembers everything. Lead resolved."),
)

#: 30–60 minute budget check: sum of beat minutes.
SESSION_MINUTES: int = sum(b.minutes for b in SESSION_BEATS)


@dataclass
class VerticalSlice:
    pc: dict = field(default_factory=lambda: dict(ARIC))
    tavern: dict = field(default_factory=lambda: dict(LANTERN_INN))
    npcs: tuple[dict, ...] = SLICE_NPCS
    mystery: dict = field(default_factory=lambda: dict(MYSTERY))
    combat: dict = field(default_factory=lambda: dict(COMBAT_ENCOUNTER))
    skills: tuple[str, ...] = SKILL_KEYS
    inventory_screen: dict = field(default_factory=lambda: dict(INVENTORY_SCREEN))
    leads_screen: dict = field(default_factory=lambda: dict(LEADS_SCREEN))
    session_beats: tuple[SessionBeat, ...] = SESSION_BEATS


def get_slice() -> VerticalSlice:
    """Assemble the vertical slice from fixture + Ravenford content."""
    return VerticalSlice()
