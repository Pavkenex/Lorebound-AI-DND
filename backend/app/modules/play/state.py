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
from app.modules.memory.npc_memory import display_name
from app.modules.npc.mood import (
    DEFAULT_MOOD,
    MOOD_DECAY_PER_HOUR,
    MOOD_SETTLED,
    baseline_mood,
    clamp_intensity,
    mood_word,
)
from app.modules.story.scenes import PROLOGUE, SceneDirector

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

def reopen_prologue_opening(state: PlayState) -> bool:
    """Hand the prologue's opening back to the narrator (§intro, P14).

    The chronicle's opening is the model's like every other story surface: the
    seed writes no prose, and ``POST /campaigns/{id}/opening`` narrates the
    arrival from the player's own sheet. A campaign is seeded before character
    creation commits, so the moment a built sheet lands this drops whatever
    opening was written for the sheet before it — it is the chronicle's first
    narration, if it exists at all — and marks the opening pending again, so
    the next call writes the arrival for the character who actually walks in.
    """
    if getattr(state, "location", "") != PROLOGUE:
        return False
    feed = list(getattr(state, "feed", []) or [])
    while feed and feed[0].get("kind") == "narration":
        feed.pop(0)
    state.feed = feed
    state.opening_pending = True
    return True


#: Relationship meter bounds (design §3): -100 (Hostile) .. +100 (Bonded).
ATTITUDE_MIN = -100
ATTITUDE_MAX = 100

#: Band ladder: (band word, low, high) — the range behind each word.
ATTITUDE_BANDS: tuple[tuple[str, int, int], ...] = (
    ("Hostile", -100, -60),
    ("Wary", -59, -20),
    ("Neutral", -19, 19),
    ("Warm", 20, 59),
    ("Bonded", 60, 100),
)


def clamp_attitude(value: int) -> int:
    """Keep a relationship value inside -100..+100."""
    return max(ATTITUDE_MIN, min(ATTITUDE_MAX, int(value)))


def attitude_band(value: int) -> str:
    """Band word for a relationship value: Hostile .. Bonded."""
    v = clamp_attitude(value)
    for band, low, high in ATTITUDE_BANDS:
        if low <= v <= high:
            return band
    return "Neutral"  # unreachable: the band ladder covers the whole range


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

    #: Relationship meter per NPC slug (design §3): -100..+100, default Neutral.
    attitudes: dict[str, int] = field(default_factory=dict)
    #: Last reason behind each meter move (mirrored to npc_relationships.note).
    attitude_reasons: dict[str, str] = field(default_factory=dict)

    #: Live emotional state per NPC slug (design §4): {mood, intensity 0..1}.
    #: Decays toward each character's baseline as the clock advances; gated
    #: words are surfaced per the campaign's content settings, never here.
    moods: dict[str, dict[str, Any]] = field(default_factory=dict)

    # -- sheet-adjacent economy -------------------------------------------
    silver: int = 8  # guilders in the pack

    # -- scene flow (design §7) -------------------------------------------
    #: The scene the player stands in — a map location's root scene id, or a
    #: micro-scene nested under it ("lantern-inn:upstairs-room"). Scenes are
    #: finer-grained than the map: the map tracks travel, scenes the moment.
    scene: str = ""
    #: Remembered scene state by id (see modules/story/scenes.py): re-entering
    #: a scene restores its beats/progress/state, so a resolved scene never
    #: re-runs its opening.
    scenes: dict[str, dict[str, Any]] = field(default_factory=dict)

    # -- bookkeeping -------------------------------------------------------
    visits: int = 1
    actions_taken: int = 0
    feed_seq: int = 0
    feed: list[dict[str, Any]] = field(default_factory=list)
    #: The chronicle's opening has not been narrated yet (P14): the seed writes
    #: no prose, and the opening is the one story surface with no beat to hang
    #: on. ``POST /campaigns/{id}/opening`` writes it from the player's sheet.
    opening_pending: bool = True

    #: Rolling saga digest (§25 continuity): a short deterministic recap of
    #: the whole tale so far, refreshed at checkpoint beats; rides every
    #: narrator prompt as [Story so far]. Model-free and campaign-agnostic
    #: (see modules/story/saga.py).
    saga: str = ""
    #: actions_taken at the last digest refresh (fallback cadence).
    saga_at: int = 0

    #: Inspiration (table applause): earned on story-driving beats, spent
    #: for advantage on one surfaced check. Capped at INSPIRATION_MAX.
    inspiration: int = 0
    #: A spent point waiting on its check — the next surfaced resolution
    #: throws twice and keeps the higher.
    inspired: bool = False

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
        if "opening_pending" not in data:
            # A save from before the opening was the model's (P14): its
            # chronicle already carries written narration, so it counts as
            # opened. Written history is never re-narrated.
            data["opening_pending"] = not any(
                e.get("kind") == "narration" for e in (data.get("feed") or [])
            )
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

    # ------------------------------------------------- relationship meter
    def attitude_for(self, npc: str) -> int:
        """Current relationship value for an NPC (default 0 — Neutral)."""
        return clamp_attitude(self.attitudes.get(npc, 0))

    def attitude_band_for(self, npc: str) -> str:
        """Band word for an NPC's current relationship value."""
        return attitude_band(self.attitude_for(npc))

    def adjust_attitude(self, npc: str, delta: int, reason: str = "") -> int:
        """Move one NPC's relationship meter; returns the new value.

        Engine-authoritative (design §3): beats call this, never the model.
        Clamped to -100..+100. The move is echoed to the chronicle as a system
        line so the player feels the meter move; the line reports the delta
        actually applied, so a capped meter stays honest and a zero-effect
        move writes no line. ``reason`` becomes the last cause, mirrored to
        ``npc_relationships.note`` on store.
        """
        if not npc:
            return 0
        before = self.attitude_for(npc)
        after = clamp_attitude(before + int(delta))
        self.attitudes[npc] = after
        if reason:
            self.attitude_reasons[npc] = str(reason)
        applied = after - before
        if applied:
            sign = "+" if applied > 0 else "\u2212"
            self.append_feed(
                "system",
                text=f"❖ {display_name(npc)} {sign}{abs(applied)} — {attitude_band(after)} ({after})",
            )
        return after

    # ---------------------------------------------------------------- mood
    def mood_of(self, npc: str) -> dict[str, Any]:
        """Current mood for an NPC slug: ``{mood, intensity}``.

        A character with no live mood reads as their baseline (Neutral by
        default) at zero intensity — never an invented word.
        """
        if not npc:
            return {"mood": DEFAULT_MOOD, "intensity": 0.0}
        entry = self.moods.get(npc)
        if not entry:
            return {"mood": baseline_mood(npc), "intensity": 0.0}
        return {
            "mood": str(entry.get("mood", baseline_mood(npc))),
            "intensity": clamp_intensity(entry.get("intensity", 0.0)),
        }

    def set_mood(self, npc: str, mood: str, intensity: float = 0.6) -> dict[str, Any]:
        """Set one NPC's live mood (engine-authoritative, design §4).

        The word is validated against the curated vocabulary (an unknown word
        raises) and the intensity clamped to 0..1; an intensity at 0 settles
        the character straight back to their baseline. Content gating is the
        *engine's* decision before calling this (:meth:`ActEngine._mood`); the
        view and the narrator prompt re-gate defensively on surfacing, so one
        settings flip re-gates every surface at once.
        """
        if not npc:
            return {"mood": DEFAULT_MOOD, "intensity": 0.0}
        word = mood_word(mood)
        level = round(clamp_intensity(intensity), 4)
        if level <= 0:
            word, level = baseline_mood(npc), 0.0
        self.moods[npc] = {"mood": word, "intensity": level}
        return dict(self.moods[npc])

    def _decay_moods(self, minutes: int) -> None:
        """Fade live moods toward each character's baseline (design §4).

        Linear per-hour decay; once an intensity falls past
        :data:`MOOD_SETTLED` the character has settled and the word snaps
        back to the baseline.
        """
        if minutes <= 0 or not self.moods:
            return
        hours = minutes / 60.0
        for slug, entry in self.moods.items():
            try:
                level = float(entry.get("intensity", 0.0))
            except (TypeError, ValueError):
                level = 0.0
            level -= MOOD_DECAY_PER_HOUR * hours
            if level <= MOOD_SETTLED:
                entry["mood"] = baseline_mood(slug)
                entry["intensity"] = 0.0
            else:
                entry["intensity"] = round(level, 4)

    def advance_minutes(self, minutes: int) -> None:
        total = self.hour * 60 + self.minute + max(0, minutes)
        extra_days, rem = divmod(total, 24 * 60)
        self.day += extra_days
        self.hour, self.minute = divmod(rem, 60)
        # The clock is what fades a mood: no time passed, nothing decays.
        self._decay_moods(max(0, minutes))

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
    """Fresh campaign state: the prologue on the road, clock set, no prose.

    The chronicle opens *outside* Ravenford (§intro): the arrival plays first,
    and entering the inn is the first true transition — its opening (and
    Marla's welcome) belongs to that beat, so it never replays. The mystery
    lead is *not* auto-discovered: the player earns "rumored" by asking Marla
    (or combing the notice board) — only the hook is in the air.

    The arrival itself is written by the model, on demand (P14): the seed marks
    ``opening_pending`` and writes nothing — no authored prose stands in for
    the narrator, not even for the first line of the tale.
    """
    state = PlayState()
    state.location = PROLOGUE
    state.visit_location(PROLOGUE)
    SceneDirector(state).seed()
    return state
