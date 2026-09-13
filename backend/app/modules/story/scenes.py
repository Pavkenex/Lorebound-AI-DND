"""Scene director (docs/SYSTEMS_DESIGN.md §7): the moment-to-moment scene machine.

A scene is finer-grained than the map: "the upstairs room" is a scene even
while the map still reads Lantern Inn, and sub-scenes arise from the fiction
(an invitation, an agreement, player initiative) before resolving back to
their parent.

This module owns the bookkeeping — which scene the player stands in, how long
they have lingered, what progress was made, what is still possible — while the
engine owns every line of prose (the same split as the memory/meter mirrors:
state here, prose there).

State contract:
- ``PlayState.scenes`` remembers each scene by id and ``PlayState.scene`` holds
  the current one; both ride the state JSON, so a save keeps the scene the
  player actually stood in and a return visit restores its remembered state
  (never a fresh opening).
- A scene entry is ``{id, label, location, parent, goal, beats,
  last_progress, state, actions}``: ``beats`` counts the resolved actions
  taken here, ``last_progress`` is the ``beats`` value progress last landed on
  (so ``beats - last_progress`` is the live idle streak), ``parent`` is the
  scene this one resolves back to (empty for the map's own root scenes), and
  ``actions`` counts action fingerprints for the anti-loop replies.
- The anti-loop guard never blocks a transition: :meth:`SceneDirector.enter`
  always moves, whatever the lingering counters say — an invitation is a
  transition trigger, and lingering in a scene is allowed (user ruling, §7).
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:  # pragma: no cover - typing only; the state module owns the class
    from app.modules.play.state import PlayState

#: Scene lifecycle (§7): the scene the player stands in is active; a scene
#: whose goal was met (or whose errand ended) is resolved; a sub-scene walked
#: out of mid-flow waits in transitioning until the player picks it back up.
ACTIVE = "active"
TRANSITIONING = "transitioning"
RESOLVED = "resolved"
SCENE_STATES: tuple[str, ...] = (ACTIVE, TRANSITIONING, RESOLVED)

#: Beats without progress before a scene's goal reads exhausted (§7, trigger b).
EXHAUSTED_AFTER = 4
#: Times one action may be repeated here before replies start diminishing.
REPEAT_DIMINISH_AFTER = 3
#: Consecutive beats without progress before replies start diminishing.
IDLE_DIMINISH_AFTER = 4

#: The slice's micro-scene: Marla's room above the inn's common room (§7).
UPSTAIRS = "lantern-inn:upstairs-room"
UPSTAIRS_LABEL = "The upstairs room"
UPSTAIRS_GOAL = "Hear what Marla will not say at the bar"

#: The prologue (§intro): a fresh chronicle opens on the road above Ravenford,
#: one rainy dusk before the tavern — the arrival first, then the room.
PROLOGUE = "road-to-ravenford"
PROLOGUE_LABEL = "The road to Ravenford"
PROLOGUE_GOAL = "Come in out of the rain"

#: Root scenes: one per map location the slice plays in, keyed by location id.
ROOT_SCENES: dict[str, dict[str, str]] = {
    PROLOGUE: {
        "label": PROLOGUE_LABEL,
        "goal": PROLOGUE_GOAL,
    },
    "lantern-inn": {
        "label": "The Lantern Inn — common room",
        "goal": "Take the measure of the room",
    },
    "northern-road": {"label": "The Northern Road", "goal": "Read the road before it hides the rest"},
    "market": {"label": "Ravenford Market", "goal": "Get the guild's business out of a ledger"},
    "old-monastery": {"label": "The Old Monastery", "goal": "Find the way into the undercroft"},
    "cellar": {"label": "Beneath the Old Monastery", "goal": "Get the travelers out"},
}

#: Goal hints per lead stage: the hint follows the story that is live (§7c).
GOALS_BY_STAGE: dict[str, dict[str, str]] = {
    "lantern-inn": {
        "unheard": "Take the measure of the room",
        "rumored": "Ask Marla what she needs from you",
        "accepted": "Look into the two who never came back",
        "investigating": "Follow the threads you hold to the undercroft",
        "solved": "The tale is told — the fire still wants company",
    },
    "northern-road": {
        "unheard": "Read the road before it hides the rest",
        "rumored": "Read the road before it hides the rest",
        "accepted": "Read the road before it hides the rest",
        "investigating": "Read the road: the carts turn north of the oak",
        "solved": "The road is only road again",
    },
    "market": {
        "unheard": "Take the market's temperature",
        "rumored": "Take the market's temperature",
        "accepted": "Find who pays over rate for silver",
        "investigating": "Get the guild's business out of a ledger",
        "solved": "The scales balance again",
    },
    "old-monastery": {
        "unheard": "Walk the monastery path",
        "rumored": "Walk the monastery path",
        "accepted": "Ask the porter what the bells keep silent about",
        "investigating": "Find the way into the undercroft",
        "solved": "The bells can ring their own hours now",
    },
}

#: Chapter closed (§7c: a story-beat boundary resolves the scene's goal).
EPILOGUE_GOAL = "The chapter is closed — the road is yours"

#: Root scene ids are the location ids themselves; a sub-scene id nests under
#: its parent ("lantern-inn:upstairs-room").
_SUB_SCENE_SEP = ":"


def root_scene_id(location: str) -> str:
    """The map location's own scene id (the root of any sub-scene stack)."""
    return str(location or "lantern-inn")


def root_scene(location: str) -> Scene:
    """A fresh root scene for a map location."""
    spec = ROOT_SCENES.get(location, {})
    return Scene(
        id=root_scene_id(location),
        label=spec.get("label", location.replace("-", " ").capitalize()),
        location=str(location or "lantern-inn"),
        goal=spec.get("goal", ""),
    )


def goal_for(location: str, lead_stage: str, completed: bool = False) -> str:
    """The goal hint for a scene: the story's shape at this lead stage (§7)."""
    if completed:
        return EPILOGUE_GOAL
    stages = GOALS_BY_STAGE.get(location)
    if stages:
        return stages.get(lead_stage, next(iter(stages.values())))
    return ROOT_SCENES.get(location, {}).get("goal", "")


def invitation_open(state: PlayState) -> bool:
    """Marla's invitation to go up stands once the story has been told (§7d).

    Before that the stair is still hers: the fiction answers with a refusal,
    not a wall — asking her about the road opens it.
    """
    return getattr(state, "lead_stage", "unheard") != "unheard"


def possible_moves(state: PlayState) -> list[dict[str, str]]:
    """What is still possible from here — the anti-loop's pointer list (§7).

    Short, authored, state-aware: a diminishing reply names these instead of
    re-narrating the room, and the same entries become suggestion buttons so
    "what is still possible" is one tap away.
    """
    stage = str(getattr(state, "lead_stage", "unheard"))
    scene = str(getattr(state, "scene", "") or "")
    out: list[dict[str, str]] = []
    if scene == UPSTAIRS:
        out.append({"label": "Head back down to the bar",
                    "command": "I go back down to the bar"})
        if stage != "solved":
            out.append({"label": "Ask her what she brought you up here for",
                        "command": "I ask Marla what she wanted to say"})
        return out
    if scene and scene != root_scene_id(getattr(state, "location", "")):
        out.append({"label": "Back to the room you came from",
                    "command": "I go back the way I came"})
    location = str(getattr(state, "location", ""))
    if location == PROLOGUE:
        out += [
            {"label": "Look over the town below",
             "command": "I look over the town below"},
            {"label": "Head down to the Lantern",
             "command": "I head down to the inn and step inside"},
            {"label": "Listen to the night",
             "command": "I listen to the rain and the river"},
        ]
    elif location == "lantern-inn":
        if stage == "unheard":
            out += [
                {"label": "Ask Marla about the road",
                 "command": "I ask Marla about the travelers"},
                {"label": "Read the notice board", "command": "I read the notice board"},
            ]
        else:
            out += [
                {"label": "Search Marla's ledger", "command": "I examine the guest ledger"},
                {"label": "Check the cellar door", "command": "I look at the cellar door"},
            ]
        if invitation_open(state):
            out.append({"label": "Take the stairs with Marla",
                        "command": "I follow Marla upstairs"})
        out.append({"label": "Ask Borin about the carts",
                    "command": "I ask Borin about the carts"})
    elif location == "northern-road":
        out += [
            {"label": "Read the wagon ruts", "command": "I study the wagon ruts at the crossroads"},
            {"label": "Watch the treeline for lanterns", "command": "I keep watch for lanterns in the trees"},
        ]
    elif location == "market":
        out += [
            {"label": "Press Sella for the ledger", "command": "I persuade Sella the factor to talk"},
            {"label": "Buy a word from Tomm", "command": "I offer Tomm five guilders for the truth"},
        ]
    elif location == "old-monastery":
        out += [
            {"label": "Try the undercroft door", "command": "I confront what waits below"},
            {"label": "Ask the porter", "command": "I talk to Brother Anselm at the porter's door"},
        ]
    if not out:
        out.append({"label": "Look around", "command": "I look around where I am"})
    return out[:4]


@dataclass
class Scene:
    """One scene's remembered state (design §7)."""

    id: str
    label: str
    location: str
    parent: str = ""
    goal: str = ""
    beats: int = 0
    last_progress: int = 0
    state: str = ACTIVE
    #: Action fingerprint -> times taken here (the anti-loop's repeat counter).
    actions: dict[str, int] = field(default_factory=dict)

    # ------------------------------------------------------------------ api
    def to_dict(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "label": self.label,
            "location": self.location,
            "parent": self.parent,
            "goal": self.goal,
            "beats": int(self.beats),
            "last_progress": int(self.last_progress),
            "state": self.state if self.state in SCENE_STATES else ACTIVE,
            "actions": {str(k): int(v) for k, v in self.actions.items()},
        }

    @classmethod
    def from_dict(cls, raw: dict[str, Any]) -> Scene:
        """Tolerant read (a half-written or older entry never breaks a load)."""
        scene = cls(
            id=str(raw.get("id", "")),
            label=str(raw.get("label", "")),
            location=str(raw.get("location", "")),
            parent=str(raw.get("parent", "")),
            goal=str(raw.get("goal", "")),
            beats=int(raw.get("beats", 0) or 0),
            last_progress=int(raw.get("last_progress", 0) or 0),
            state=str(raw.get("state", ACTIVE)),
        )
        actions = raw.get("actions")
        if isinstance(actions, dict):
            scene.actions = {str(k): int(v) for k, v in actions.items() if isinstance(v, (int, float))}
        return scene

    # --------------------------------------------------------------- derived
    @property
    def idle(self) -> int:
        """Beats since the scene last made progress (its lingering streak)."""
        return max(0, int(self.beats) - int(self.last_progress))

    @property
    def exhausted(self) -> bool:
        """The scene's goal is clearly exhausted (§7, trigger b)."""
        return self.idle >= EXHAUSTED_AFTER

    def repeats(self, key: str) -> int:
        return int(self.actions.get(str(key), 0))


@dataclass
class Move:
    """What one scene transition did — the engine reads it to pick prose."""

    from_id: str = ""
    to_id: str = ""
    first_visit: bool = False
    restored: bool = False  # a remembered scene came back
    closed: str = ""  # the sub-scene that ended on the way out
    closed_resolved: bool = False

    @property
    def changed(self) -> bool:
        return bool(self.to_id) and self.from_id != self.to_id


class SceneDirector:
    """Bookkeeping over one campaign's scene state (state in, facts out)."""

    def __init__(self, state: PlayState) -> None:
        self.state = state

    # ------------------------------------------------------------ registry
    def get(self, scene_id: str) -> Scene | None:
        raw = (getattr(self.state, "scenes", None) or {}).get(str(scene_id))
        if not isinstance(raw, dict):
            return None
        return Scene.from_dict(raw)

    def save(self, scene: Scene) -> Scene:
        scenes = getattr(self.state, "scenes", None)
        if scenes is None:  # a bare state without the field (older save)
            self.state.scenes = {}
            scenes = self.state.scenes
        scenes[scene.id] = scene.to_dict()
        return scene

    def current(self) -> Scene:
        """The scene the player stands in (read-only; bootstraps in memory)."""
        sid = str(getattr(self.state, "scene", "") or root_scene_id(self.state.location))
        found = self.get(sid)
        if found is not None:
            return found
        fresh = root_scene(self.state.location)
        fresh.goal = goal_for(self.state.location, self.state.lead_stage, self.state.completed)
        return fresh

    def ensure(self) -> Scene:
        """Bootstrap the map location's root scene on first use (idempotent)."""
        sid = str(getattr(self.state, "scene", "") or root_scene_id(self.state.location))
        found = self.get(sid)
        if found is not None:
            return found
        fresh = root_scene(self.state.location)
        fresh.goal = goal_for(self.state.location, self.state.lead_stage, self.state.completed)
        self.save(fresh)
        self.state.scene = sid
        return fresh

    def seed(self) -> Scene:
        """Register the opening scene of a fresh campaign.

        The opening narration is already in the feed, so the root scene starts
        where the player is: standing in it, nothing yet resolved.
        """
        scene = root_scene(self.state.location)
        scene.goal = goal_for(self.state.location, self.state.lead_stage, self.state.completed)
        self.save(scene)
        self.state.scene = scene.id
        return scene

    # ------------------------------------------------------------ lifecycle
    def note_beat(self, *, key: str = "", progress: bool = False) -> Scene:
        """Count one resolved action in the current scene (§7 bookkeeping).

        ``progress`` is the engine's progress detection (a clue found, a lead
        advanced, any state change): it re-marks ``last_progress``, which is
        what the anti-loop measures lingering against.
        """
        scene = self.ensure()
        scene.beats += 1
        if progress:
            scene.last_progress = scene.beats
        if key:
            scene.actions[str(key)] = scene.repeats(key) + 1
        return self.save(scene)

    def enter(
        self,
        scene_id: str,
        *,
        label: str = "",
        location: str = "",
        parent: str = "",
        goal: str = "",
    ) -> Move:
        """Move the player into a scene — a transition that never gets blocked.

        A remembered scene comes back with its beats/progress/state (a
        ``transitioning`` scene is picked back up as active; a ``resolved`` one
        stays resolved, so its opening never re-runs). A sub-scene left behind
        closes: resolved when it had made progress, transitioning otherwise.
        """
        target = str(scene_id)
        from_scene = self.ensure()
        existing = self.get(target)
        first = existing is None
        if existing is None:
            spec = ROOT_SCENES.get(target, {})
            existing = Scene(
                id=target,
                label=label or spec.get("label") or target.replace("-", " ").replace(":", " — ").capitalize(),
                location=location or self.state.location,
                parent=parent,
                goal=goal or goal_for(location or self.state.location, self.state.lead_stage, self.state.completed),
            )
            self.save(existing)
        else:
            if existing.state == TRANSITIONING:
                existing.state = ACTIVE  # the thread the player walked away from
            existing.goal = goal or goal_for(
                existing.location or self.state.location, self.state.lead_stage, self.state.completed
            )
            self.save(existing)
        closed, closed_resolved = self._close(from_scene, target)
        self.state.scene = target
        return Move(
            from_id=from_scene.id,
            to_id=target,
            first_visit=first,
            restored=not first,
            closed=closed,
            closed_resolved=closed_resolved,
        )

    def _close(self, scene: Scene, target: str) -> tuple[str, bool]:
        """Settle the scene the player is walking out of (§7).

        Sub-scenes resolve back to their parent: a sub-scene whose goal was met
        is resolved (the aftermath is what a return visit finds), one walked
        out of unfinished waits in ``transitioning``. Root scenes stay the
        map's own places — they stay open.
        """
        if scene.id == target:
            return "", False
        if scene.parent:
            scene.state = RESOLVED if scene.last_progress > 0 else TRANSITIONING
            self.save(scene)
            return scene.id, scene.state == RESOLVED
        if scene.state == ACTIVE and getattr(self.state, "completed", False):
            scene.state = RESOLVED
            self.save(scene)
        return "", False

    def resolve_current(self) -> bool:
        """Mark the player's scene resolved — its goal was met. Returns change."""
        scene = self.current()
        if scene.state == RESOLVED:
            return False
        scene.state = RESOLVED
        self.save(scene)
        return True

    def ended(self, scene_id: str) -> bool:
        """True when a remembered scene is resolved (a return finds aftermath)."""
        scene = self.get(scene_id)
        return scene is not None and scene.state == RESOLVED

    def story_boundary(self) -> Scene:
        """A story beat landed (lead advance / chapter close): refresh the goal.

        Trigger (c) of §7. The boundary moves the *aim* of the scene; it never
        yanks the player anywhere — lingering stays allowed and the goal hint
        simply follows the story that is now live.
        """
        scene = self.current()
        scene.goal = goal_for(self.state.location, self.state.lead_stage, self.state.completed)
        if self.state.completed and not scene.parent:
            scene.state = RESOLVED
        return self.save(scene)

    # -------------------------------------------------------------- anti-loop
    def diminishing(self, key: str) -> bool:
        """True when the anti-loop guard should shorten this reply (§7).

        A repeated action or a run of idle beats in one scene gets a
        diminishing response that points at what is still possible. The guard
        only shapes prose — it never blocks a transition, and beats that made
        progress are never touched.
        """
        scene = self.current()
        return (
            scene.repeats(key) >= REPEAT_DIMINISH_AFTER
            or scene.idle >= IDLE_DIMINISH_AFTER
        )

    def scene_block(self) -> dict[str, Any]:
        """The player-visible read of the current scene (GET /state)."""
        scene = self.current()
        return {
            "id": scene.id,
            "label": scene.label,
            "parent": scene.parent,
            "goal": scene.goal,
            "state": scene.state,
            "beats": scene.beats,
            "idle": scene.idle,
        }
