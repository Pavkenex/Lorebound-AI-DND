"""Live play state for the vertical slice (per campaign).

This is the *authoritative* gameplay state the engine owns — never the model.
It seeds from the authored slice fiction (Lantern Inn / Marla / missing
travelers) and the live protagonist sheet, and it is what ``GET /state``
renders and ``POST /act`` mutates (through engine-side effects only).

Design notes
- One :class:`PlayState` per campaign, serialized to ``PlayStateRow``.
- ``feed`` is the player-visible chronicle tail in the frontend's FeedEvent
  shape (kind: narration | dialogue | dice | lead | system).
- Lead stages follow the authored slice: unheard -> rumored -> accepted ->
  investigating -> solved.
"""
from __future__ import annotations

import json
from dataclasses import asdict, dataclass, field
from typing import Any

from app.content.skills import SKILL_KEYS

#: Player-visible feed is a tail; older entries fall out of view, not the DB history.
FEED_CAP = 60

#: Cap on structured NPC memories in the save; the weakest (lowest salience,
#: then oldest) fall out first.
NPC_MEMORY_CAP = 200

#: Lead stage order for the slice mystery (index = progress).
LEAD_STAGES: tuple[str, ...] = ("unheard", "rumored", "accepted", "investigating", "solved")

#: Clue registry: id -> description shown in journal/screens when found.
CLUES: dict[str, str] = {
    "ledger": "Marla's ledger — two guests signed toward the monastery, never signed out.",
    "tracks": "Cart tracks at the crossroads — wheels cutting toward the forest, not the road.",
    "lanterns": "Lanterns moving through the trees after dark, timed to the monastery bells.",
}

#: The five solution paths (GDD §116): id -> display name. Each is gated by one
#: slice skill; unlocking any one opens the confrontation.
SOLUTIONS: dict[str, str] = {
    "talk-it-out": "The Honest Plea",
    "lean-on-them": "The Hard Stare",
    "follow-the-clues": "The Ledger Trail",
    "shadow-them": "The Night Watch",
    "blades-out": "The Road Ambush",
}

#: Authored live protagonist default sheet (the chronicle's lead, as authored
#: across the UI). Character creation (later stream) replaces this per campaign.
DEFAULT_PC: dict[str, Any] = {
    "name": "Kaelis Thorn",
    "epithet": "the Lantern-Bearer",
    "portraitHue": 36,
    "background": (
        "Raised by the lamplighters of Ravenford after the caravan fire took her "
        "parents, Kaelis learned to read roads by smell and strangers by their boots. "
        "She carries her mother's unlit lantern — a vow, not a tool."
    ),
    "level": 4,
    "xp": 62,
    "attributes": {"Might": 12, "Finesse": 15, "Wits": 14, "Resolve": 13, "Presence": 11},
    "hp": {"cur": 32, "max": 32},
    "stamina": {"cur": 12, "max": 12},
    "resolve": {"cur": 6, "max": 6},
    "conditions": [],
    "traits": ["Night-eyed", "Soft-footed", "Keeps every promise twice"],
    "drives": ["Find the missing caravan", "Light the monastery beacon again"],
    "relationships": [
        {"name": "Marla Voss", "note": "Innkeeper. Owes Kaelis a debt she will not name."},
        {"name": "Brother Anselm", "note": "Monastery archivist. Trades secrets for lamp oil."},
    ],
    "equipment": ["Alder shortbow", "Lantern (unlit)", "Silvered knife", "Climber's cord"],
    "achievements": [],
}

#: Opening beat shown when a campaign's chronicle is first unrolled.
OPENING_BEAT: str = (
    "Rain needles the shutters of the Lantern Inn. The hearth throws long shadows "
    "across Marla's notice board, where one parchment hangs newer than the rest — "
    "a plea about travelers who never came back down the Northern Road."
)


def default_pc_sheet() -> dict[str, Any]:
    """Deep-ish copy of the default protagonist sheet."""
    return json.loads(json.dumps(DEFAULT_PC))


@dataclass
class PlayState:
    """Authoritative per-campaign slice state (engine-owned)."""

    version: int = 1
    pc: dict[str, Any] = field(default_factory=default_pc_sheet)

    # -- world position ----------------------------------------------------
    location: str = "lantern-inn"  # lantern-inn | northern-road | market | old-monastery | cellar
    day: int = 1
    hour: int = 19
    minute: int = 0

    # -- mystery -----------------------------------------------------------
    lead_stage: str = "unheard"
    clues: list[str] = field(default_factory=list)
    solution_path: str | None = None
    travelers_freed: bool = False
    completed: bool = False

    # -- NPC memory / world flags -----------------------------------------
    marla_memory: list[str] = field(default_factory=list)
    #: Structured per-NPC memories about the player (mirrored to npc_memories
    #: on store). Entries: {npc, text, kind, sentiment, salience, day, hour};
    #: ``npc`` is the slug from modules/memory/npc_memory.py.
    npc_memory_log: list[dict[str, Any]] = field(default_factory=list)
    borin_down: bool = False
    sella_trust: int = 0

    # -- sheet-adjacent economy -------------------------------------------
    silver: int = 8  # guilders in the pack

    # -- bookkeeping -------------------------------------------------------
    visits: int = 1
    actions_taken: int = 0
    feed_seq: int = 0
    feed: list[dict[str, Any]] = field(default_factory=list)

    # -- progression (five slice skills) ----------------------------------
    skills: dict[str, int] = field(default_factory=lambda: {k: 0 for k in SKILL_KEYS})
    skill_recent: dict[str, list[str]] = field(default_factory=dict)

    #: Locations the player has actually stood in (map/journal reveal).
    visited: list[str] = field(default_factory=lambda: ["lantern-inn"])

    #: In-flight 6-stage character creation draft: {"stage": int, "data": {...}}.
    creation: dict[str, Any] = field(default_factory=dict)

    # ------------------------------------------------------------------ api
    def to_json(self) -> str:
        return json.dumps(asdict(self), ensure_ascii=False)

    @classmethod
    def from_json(cls, raw: str) -> PlayState:
        try:
            data = json.loads(raw)
        except (TypeError, ValueError):
            return cls()
        if not isinstance(data, dict):
            return cls()
        known = {f for f in cls.__dataclass_fields__}
        return cls(**{k: v for k, v in data.items() if k in known})

    # ------------------------------------------------------------- helpers
    def note(self, tag: str) -> None:
        """Append a Marla-memory tag exactly once."""
        if tag not in self.marla_memory:
            self.marla_memory.append(tag)

    def remember(
        self,
        npc: str,
        text: str,
        *,
        kind: str = "",
        sentiment: int = 0,
        salience: int = 2,
    ) -> bool:
        """Record one structured memory an NPC keeps about the player.

        Deduped on exact (npc, text): a repeated beat adds nothing twice.
        Capped at :data:`NPC_MEMORY_CAP`; overflow drops the weakest entries
        first (lowest salience, then oldest).
        """
        text = text.strip()
        if not npc or not text:
            return False
        for memory in self.npc_memory_log:
            if memory.get("npc") == npc and memory.get("text") == text:
                return False
        self.npc_memory_log.append(
            {
                "npc": npc,
                "text": text,
                "kind": kind,
                "sentiment": int(sentiment),
                "salience": int(salience),
                "day": self.day,
                "hour": self.hour,
            }
        )
        overflow = len(self.npc_memory_log) - NPC_MEMORY_CAP
        if overflow > 0:
            weakest = sorted(
                range(len(self.npc_memory_log)),
                key=lambda i: (self.npc_memory_log[i]["salience"], i),
            )
            for idx in sorted(weakest[:overflow], reverse=True):
                del self.npc_memory_log[idx]
        return True

    def memories_for(self, npc: str, limit: int = 3) -> list[dict[str, Any]]:
        """An NPC's strongest memories about the player: salience desc, newest first."""
        found = [(i, m) for i, m in enumerate(self.npc_memory_log) if m.get("npc") == npc]
        found.sort(key=lambda pair: (-int(pair[1].get("salience", 0)), -pair[0]))
        return [m for _, m in found[: max(0, limit)]]

    def advance_minutes(self, minutes: int) -> None:
        total = self.hour * 60 + self.minute + max(0, minutes)
        extra_days, rem = divmod(total, 24 * 60)
        self.day += extra_days
        self.hour, self.minute = divmod(rem, 60)

    def set_lead_stage(self, stage: str) -> bool:
        """Advance the lead stage monotonically. Returns True if it changed."""
        if stage not in LEAD_STAGES:
            raise ValueError(f"unknown lead stage {stage!r}")
        if LEAD_STAGES.index(stage) <= LEAD_STAGES.index(self.lead_stage):
            return False
        self.lead_stage = stage
        return True

    def find_clue(self, clue_id: str) -> bool:
        """Record a clue. Returns True if newly found."""
        if clue_id not in CLUES:
            raise ValueError(f"unknown clue {clue_id!r}")
        if clue_id in self.clues:
            return False
        self.clues.append(clue_id)
        return True

    def unlock_solution(self, solution_id: str) -> bool:
        """Record the route that opens the confrontation. Returns True if new."""
        if solution_id not in SOLUTIONS:
            raise ValueError(f"unknown solution {solution_id!r}")
        if self.solution_path is not None:
            return False
        self.solution_path = solution_id
        return True

    def visit_location(self, location_id: str) -> None:
        if location_id not in self.visited:
            self.visited.append(location_id)

    def award_skill(self, skill: str, amount: int, note: str = "") -> None:
        """Add mastery XP for a skill used in anger; keep the last notes."""
        self.skills[skill] = self.skills.get(skill, 0) + max(0, amount)
        if note:
            recent = self.skill_recent.setdefault(skill, [])
            recent.append(f"{note} +{amount}")
            self.skill_recent[skill] = recent[-3:]

    def append_feed(self, kind: str, **payload: Any) -> dict[str, Any]:
        """Append one player-visible feed event; keeps only the tail."""
        self.feed_seq += 1
        event: dict[str, Any] = {"id": f"live-{self.feed_seq}", "kind": kind, **payload}
        self.feed.append(event)
        if len(self.feed) > FEED_CAP:
            self.feed = self.feed[-FEED_CAP:]
        return event


def seeded_state() -> PlayState:
    """Fresh campaign state: opening pose at the Lantern Inn, clock set, opening beat.

    The mystery lead is *not* auto-discovered: the player earns "rumored" by
    asking Marla (or combing the notice board) — only the hook is in the air.
    """
    state = PlayState()
    state.append_feed("narration", text=OPENING_BEAT)
    state.append_feed(
        "dialogue",
        speaker="Marla Voss",
        text=(
            "Come in from the rain, then — the fire's warm and the road's bad. "
            "Sit where I can see you, stranger; questions come cheaper than silver here."
        ),
    )
    return state
