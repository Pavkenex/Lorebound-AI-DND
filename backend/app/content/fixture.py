"""Prototype fixture: Aric / Lantern Inn / Marla (GDD §119, §116).

Smallest environment that proves the architecture: one character, one
location, one NPC with persistent memory, five wired skills, one story
lead, and seven scripted flows (talk / inspect / steal / fight /
discover-lead / leave / return) where Marla remembers prior visits.
"""

from __future__ import annotations

from dataclasses import dataclass, field

from app.content.skills import SKILL_KEYS

# ---------------------------------------------------------------------------
# Data
# ---------------------------------------------------------------------------

ARIC = {
    "id": "aric",
    "name": "Aric",
    "title": "Rain-soaked traveler",
    "hp": 12,
    "max_hp": 12,
    "skills": {key: 1 for key in SKILL_KEYS},
    "inventory": ["traveler's cloak", "short sword", "3 silver rings"],
    "silver": 8,
}

LANTERN_INN = {
    "id": "lantern-inn",
    "name": "Lantern Inn",
    "description": (
        "A low-beamed roadhouse on the edge of Ravenford. Rain hammers the "
        "shutters; a peat fire hisses in the hearth. Marla, the innkeeper, "
        "polishes tankards and watches every guest."
    ),
    "interactables": {
        "common-room": "Tables, hearth, and a notice board with a plea about missing travelers.",
        "marlas-ledger": "The inn's guest ledger, kept behind the bar.",
        "storeroom-strongbox": "A locked strongbox in the pantry storeroom.",
        "drunk-mercenary": "Borin, a drunk mercenary spoiling for a fight by the fire.",
    },
}

MARLA = {
    "id": "marla",
    "name": "Marla",
    "role": "Innkeeper of the Lantern Inn",
    "description": (
        "Broad-shouldered, sharp-eyed, and fair to paying guests. "
        "She forgets nothing said under her roof."
    ),
}

MISSING_TRAVELERS_LEAD = {
    "id": "missing-travelers",
    "title": "Missing Travelers",
    "summary": (
        "Two travelers bound for the Old Monastery never arrived. "
        "Marla last saw them heading up the Northern Road in the rain."
    ),
    "stages": ["unheard", "rumored", "accepted", "investigating", "solved"],
}

#: The seven scripted acceptance flows for t_5f738da1.
SCRIPTED_FLOWS: tuple[str, ...] = (
    "talk",
    "inspect",
    "steal",
    "fight",
    "discover-lead",
    "leave",
    "return",
)


@dataclass
class NarrativeResult:
    """One beat of authored narration plus its world-state effects."""

    text: str
    events: list[str] = field(default_factory=list)
    state_changes: dict[str, str] = field(default_factory=dict)


# ---------------------------------------------------------------------------
# Fixture world (tiny state machine with NPC memory)
# ---------------------------------------------------------------------------


#: Authored return greetings — rotated per visit so repeat visits never repeat
#: (PLAYTEST_REPORT condition 1). Every variant references what Marla remembers.
_RETURN_GREETINGS: tuple[str, ...] = (
    "Welcome back, Aric. Marla looks up from the bar — she remembers {recalled}.",
    ("The door lets you in and Marla is already watching it — she remembers "
     "{recalled}, and does not pretend otherwise."),
    ("The Lantern's fire has hardly found you before Marla sets a cup on the bar. "
     "She remembers {recalled}; nothing said under this roof is ever quite forgotten here."),
)


class FixtureWorld:
    """Playable Aric / Lantern Inn / Marla fixture with Marla's memory.

    Memory model: every flow appends tags (e.g. ``"fought"``) to
    :attr:`marla_memory`. :meth:`return_to_inn` renders a greeting that
    references what actually happened, proving persistence.
    """

    def __init__(self) -> None:
        self.location: str = "lantern-inn"
        self.lead_stage: str = "unheard"
        self.marla_memory: list[str] = []
        self.stolen: list[str] = []
        self.fought: bool = False
        self.visits: int = 1

    # -- helpers ---------------------------------------------------------
    def _remember(self, tag: str) -> None:
        if tag not in self.marla_memory:
            self.marla_memory.append(tag)

    # -- flows -----------------------------------------------------------
    def talk_to_marla(self, topic: str = "travelers") -> NarrativeResult:
        self._remember(f"talked:{topic}")
        return NarrativeResult(
            text=(
                f'You ask Marla about {topic}. She leans on the bar. '
                '"Two guests bound for the monastery walked into that rain '
                'three nights back and never came down again. Nobody else '
                'seems to care."'
            ),
            events=["marla:talk"],
            state_changes={"marla_memory": f"talked:{topic}"},
        )

    def inspect_room(self, target: str = "common-room") -> NarrativeResult:
        detail = LANTERN_INN["interactables"].get(target, "Nothing of note.")
        self._remember(f"inspected:{target}")
        return NarrativeResult(
            text=f"You inspect the {target}. {detail}",
            events=["room:inspected"],
            state_changes={"inspected": target},
        )

    def steal(self, target: str = "storeroom-strongbox") -> NarrativeResult:
        self.stolen.append(target)
        self._remember(f"stole:{target}")
        return NarrativeResult(
            text=(
                f"You slip into the storeroom and crack the {target}. "
                "A small pouch of silver changes hands. Marla's eyes narrow "
                "when you return — she counts her stock nightly."
            ),
            events=["item:stolen"],
            state_changes={"stolen": target, "marla_memory": f"stole:{target}"},
        )

    def start_fight(self, target: str = "drunk-mercenary") -> NarrativeResult:
        self.fought = True
        self._remember(f"fought:{target}")
        return NarrativeResult(
            text=(
                f"Borin the {target.replace('-', ' ')} swings first. Tankards fly, "
                "the fire hisses, and you put him down with the flat of your "
                "blade. Marla hauls him to the porch and remembers exactly "
                "who started it."
            ),
            events=["combat:resolved"],
            state_changes={"fought": "true", "marla_memory": f"fought:{target}"},
        )

    def discover_lead(self) -> NarrativeResult:
        self.lead_stage = "rumored"
        self._remember("shared:lead")
        return NarrativeResult(
            text=(
                "Lead discovered — MISSING TRAVELERS: two travelers bound for "
                "the Old Monastery vanished on the Northern Road. Marla marks "
                "their names in her ledger and asks you to look into it."
            ),
            events=["lead:discovered"],
            state_changes={"lead": "missing-travelers", "stage": "rumored"},
        )

    def leave_inn(self) -> NarrativeResult:
        self.location = "northern-road"
        return NarrativeResult(
            text=(
                "You shoulder your cloak and step out into the rain, "
                "the Lantern's light shrinking behind you on the Northern Road."
            ),
            events=["travel:left-inn"],
            state_changes={"location": "northern-road"},
        )

    def return_to_inn(self) -> NarrativeResult:
        self.location = "lantern-inn"
        self.visits += 1
        memories: list[str] = []
        for tag in self.marla_memory:
            if tag.startswith("stole:"):
                memories.append("her missing silver")
            elif tag.startswith("fought:"):
                memories.append("the brawl you started")
            elif tag.startswith("talked:"):
                memories.append(f"your questions about {tag.split(':', 1)[1]}")
            elif tag == "shared:lead":
                memories.append("the missing travelers you promised to find")
            elif tag.startswith("inspected:"):
                memories.append(f"your poking around the {tag.split(':', 1)[1]}")
        recalled = (
            "; ".join(memories) if memories else "a quiet first visit"
        )
        template = _RETURN_GREETINGS[(self.visits - 2) % len(_RETURN_GREETINGS)]
        return NarrativeResult(
            text=template.format(recalled=recalled),
            events=["travel:returned"],
            state_changes={"location": "lantern-inn", "visits": str(self.visits)},
        )

    # -- snapshot ----------------------------------------------------------
    def snapshot(self) -> dict:
        return {
            "location": self.location,
            "lead_stage": self.lead_stage,
            "marla_memory": list(self.marla_memory),
            "stolen": list(self.stolen),
            "fought": self.fought,
            "visits": self.visits,
        }


def new_fixture_world() -> FixtureWorld:
    """Build a fresh fixture world (one per playthrough / test)."""
    return FixtureWorld()
