"""Live act engine: player text -> engine-resolved beat -> narration + effects.

Invariants (GDD): the model is never the database; player text is an attempt
only. Beats below are engine-authored (slice content); anything unrecognised
falls through to the 10-step Pipeline (interpreter -> checks -> narrator) where
the stub provider keeps the game playable with zero models.

The engine owns: state transitions, d20 checks (rules engine), feed events,
lead/clue progression, Marla's memory. The provider only writes prose.
"""
from __future__ import annotations

import hashlib
import re
from dataclasses import dataclass, field
from typing import Any

from app.modules.actions.pipeline import ActionInput, Pipeline
from app.modules.actions.suggest import SceneContext
from app.modules.ai.metering import MeterRegistry
from app.modules.ai.providers import Provider
from app.modules.memory.npc_memory import npc_slug
from app.modules.narrator.prefs import ContentPrefs
from app.modules.play.session import PlaySession
from app.modules.play.state import CLUES, SOLUTIONS
from app.modules.play.view import npc_names
from app.modules.rules.checks import (
    CheckRequest,
    CheckResult,
    CheckSuspension,
    Outcome,
    dc_for_band,
    roll_check,
)

# ---------------------------------------------------------------------------
# Skill mapping: slice skills -> PC attribute + trained bonus
# ---------------------------------------------------------------------------

SKILL_ATTRIBUTE: dict[str, str] = {
    "persuasion": "Presence",
    "intimidation": "Might",
    "investigation": "Wits",
    "stealth": "Finesse",
    "perception": "Wits",
    "swordsmanship": "Might",
}

#: Trained competence in the five slice skills (the PC's trained set).
TRAINED_BONUS = 2

#: How many of an NPC's memories about the player reach the narrator prompt.
NARRATOR_MEMORY_LIMIT = 3

SUCCESS_OUTCOMES = {Outcome.Success, Outcome.SuccessWithCost, Outcome.Exceptional}

# ---------------------------------------------------------------------------
# Authored beat prose (engine-owned; <= 600 chars each, playtest discipline)
# ---------------------------------------------------------------------------

TALK_FIRST = (
    "You lean on the bar and ask about the road. Marla wipes the same cup twice "
    "before she answers — a habit of hers when a thing is worth saying, and the "
    "rain keeps drumming the shutters while she weighs you."
)
TALK_LINE_FIRST = (
    "Two guests bound for the monastery walked into that rain three nights back "
    "and never came down again. The watch says travelers wander off. I say "
    "travelers don't leave their packs."
)
TALK_ACCEPT_NARRATION = (
    "Marla studies you a moment longer, then sets the cup down with a decision. "
    "The inn goes quiet around the two of you, in the way rooms do when a promise "
    "is about to be made."
)
TALK_ACCEPT_LINE = (
    "Find out what happened to them. Bring me word — or better. Keep your tab "
    "open here and I'll keep your lamp lit, Aric's luck to you."
)
TALK_REPEAT = (
    "Marla gives you the same hard look and the same thin patience: the road is "
    "still swallowing travelers, and every night she counts her beds twice. She "
    "nods at the ledger under the bar — whatever you need to know, it will "
    "answer to paper sooner than to words."
)
TALK_INVESTIGATING = (
    "Marla tops the cup without being asked. \"Word travels ahead of you,\" she "
    "says. \"Half the market has seen you asking after carts and lanterns.\" She "
    "does not ask you to stop — which, from Marla, is closer to a request to "
    "finish."
)
TALK_SOLVED = (
    "Marla meets your eye across the room and for once the cup stays still. She "
    "pours two — one for you, one set opposite, for the tale that freed her "
    "ledger of ghosts. The inn will hear it all by morning."
)

BOARD_FIRST = (
    "The newest parchment on the notice board is a plea in three hands: two "
    "travelers bound for the Old Monastery are missing on the Northern Road. "
    "Someone has underscored the word 'again'. The rain has got at the edges, "
    "but the names are clear enough."
)
BOARD_REPEAT = (
    "The plea still hangs there, rain-curled at the corners — two names, one "
    "road, no answers. Somebody has since pinned a charcoal sketch of the "
    "missing wagon below it."
)

LEDGER_NARRATION = (
    "Behind the bar the guest ledger lies open where Marla left it — she counts "
    "her stock nightly, and tonight it has been counted twice. You turn the "
    "pages before the damp can."
)
LEDGER_SUCCESS = (
    "There: two signatures for the Old Monastery road, three nights apart — and "
    "no line drawn through either. They never signed out. In the margin, one "
    "small hand has pencilled the same word twice: 'again'."
)
LEDGER_FAIL = (
    "The damp has won the last few pages — the ink runs together into dark "
    "smears. You can make out boots, a guild mark, nothing certain. Come at it "
    "fresh before Marla notices where you're reading."
)

STRONGBOX_SUCCESS = (
    "The storeroom latch gives with a practiced nudge. Inside the strongbox: a "
    "pouch of guilders, heavier than an innkeeper's till has any right to be in "
    "one night. Marla will count her stock tonight — she always does — but the "
    "road eats coin as fast as it eats travelers."
)
STRONGBOX_COST = (
    "You have the pouch when a floorboard speaks under your heel — one loud "
    "note in the rain-soft house. Marla says nothing as you ease the door shut, "
    "but something in the way she doesn't look up says the ledger will remember "
    "this even if the watch can't prove it."
)
STRONGBOX_FAIL = (
    "The latch holds fast to its frame, and the second try costs you a scrape "
    "across the knuckles. You gather yourself in the dark storeroom and decide "
    "the strongbox can wait for better trade."
)
STRONGBOX_CAUGHT = (
    "The strongbox isn't locked — it's a decoy, and decoys ring. The little bell "
    "inside jumps once and Marla is in the doorway before your hand clears the "
    "lid. She looks at you the way a ledger looks at a debt, and says nothing "
    "at all. That silence will cost more than any fine."
)

FIGHT_WIN = (
    "Borin swings first, the way he always does, and that is the last choice "
    "the fight lets him make. Tankards scatter, the fire hisses, and you put "
    "him down flat beside the hearth. Marla hauls him none too gently to the "
    "porch and remembers — in writing and otherwise — exactly who started it "
    "and who finished it."
)
FIGHT_COST = (
    "You win, but it is a close, ugly thing: a chair dies, a ridge of the "
    "hearth rakes your ribs, and Borin goes down wearing his own apology in a "
    "split lip. Marla's look could stop the rain; the porch claims him for the "
    "night."
)
FIGHT_LOSE = (
    "Borin has the strength of ten drunk men and the reach of none of them — "
    "but tonight is not your night. He gets inside your arm, the room tilts, "
    "and Marla's voice cuts the brawl apart before it becomes a hanging matter. "
    "You come up with a split brow and an education."
)
FIGHT_CRIT = (
    "He catches you flush with the flat of a tankard and the floor introduces "
    "itself. Marla breaks it apart with a cudgel from under the bar and a voice "
    "you have never heard her use. You wake on the settle with your ribs in "
    "bandages and Borin snoring on the porch — and every soul in the inn now "
    "knows your business."
)

LEAVE_NARRATION = (
    "You shoulder your cloak, and the door lets in the whole wet breath of the "
    "night at once. Behind you the Lantern's light shrinks to a coin, to a "
    "needle, and the Northern Road takes you the way it takes every traveler — "
    "promising nothing but the walking."
)
RETURN_NARRATION = (
    "The Lantern Inn gathers you back with its firelight and the smell of wet "
    "peat. The door finds its latch, the rain settles back to a drum, and Marla "
    "looks up from the bar in the small silence that always follows an arrival."
)
REST_NARRATION = (
    "You take the room Marla pretends not to save for lamplighters, bar the "
    "door, and let the rain do the arguing for once. Sleep comes the way it "
    "does when a road is waiting: shallow, quick, and already half-dreaming "
    "about the morning."
)
MARKET_NARRATION = (
    "The market is a bad morning in a good coat: canvas snapping, gutters "
    "roaring, and guild silver changing hands where the stalls stay dry. A "
    "factor in grey gloves watches the price of silver climb, and does not "
    "bother to look away when you catch her at it."
)
MONASTERY_NARRATION = (
    "The Old Monastery stands above the treeline like a promise someone broke "
    "a long time ago. Rain floods the cart ruts on the path; the bells are "
    "silent in the towers but a lantern burns low at the porter's door."
)

# --- investigation, solutions, resolution (the arc) ------------------------

TRACKS_SUCCESS = (
    "You kneel where the mud still holds its shape. Two wheel-ruts leave the "
    "monastery road at the lightning-bent oak — and they cut away toward the "
    "deep forest, where no map of Ravenford admits a road at all. Guild-narrow "
    "wheels, double-laden by the look of the rills: whatever came back "
    "driverless, something heavier followed it in."
)
TRACKS_FAIL = (
    "The rain has combed the last day's ruts to soup. Carts came and went — "
    "that much the mud will swear to — but which way the last one turned, it "
    "keeps to itself. Come at it fresh; the weather will hold long enough."
)
TRACKS_HINT = (
    "The ruts are knee-deep and dumb. Without a story to follow they are only "
    "mud — ask under the Lantern's roof what everyone else is trying not to "
    "notice, and the road will start to make sense."
)
LANTERNS_SUCCESS = (
    "You fold into the hollow of the woodline and wait the way a lamplighter's "
    "daughter waits. Near the second bell, lights come swinging down through "
    "the trees — three lanterns, one route, carried low and slow with weight. "
    "They pass near enough to smell of tallow and wet wool, and they take the "
    "wide path east where the ridge folds: the mouth of the old supply tunnel."
)
LANTERNS_FAIL = (
    "You wait through one bell and two, until the cold owns the hollow and the "
    "rain finds a way past your collar. Lanterns or fireflies, the trees keep "
    "their business to themselves tonight."
)
LANTERNS_HINT = (
    "The tree line is only dark, and dark is not yet evidence. Learn first "
    "what the road has been swallowing, then watch what the night carries — "
    "the two will start to rhyme."
)
SELLA_PERSUADE_SUCCESS = (
    "Sella listens with the weariness of a woman who has heard every plea ever "
    "made across that stall — and it is not the plea that lands. It is Marla's "
    "name. She closes the ledger and looks up the road a long moment. \"The "
    "guild rents the old monastery cellar — the one the order calls sealed. If "
    "your two went up that road, they are under the hill. Go before the bells "
    "ring again, and you never heard it here.\""
)
SELLA_PERSUADE_FAIL = (
    "\"Guild business, love,\" she says, not unkindly, and slides the ledger "
    "out from under your eyes. The kindness is real; so is the ledger."
)
SELLA_INTIMIDATE_SUCCESS = (
    "Something in your stillness reaches her after all. Sella's own stillness "
    "breaks first — she turns a page she is not reading. \"The cellar under "
    "the monastery. The old one. Guild rent, guild carriers — and your "
    "travelers still breathing, because the guild wants the road worried "
    "about, not closed. That is all you get.\""
)
SELLA_INTIMIDATE_FAIL = (
    "You are three words into the hard voice when two grey-gloved men find "
    "reasons to stand behind her shoulders. Sella smiles the whole way. "
    "Nothing is said that a magistrate could use."
)
AMBUSH_WIN = (
    "They come up the road late with hooded lanterns and a hand-cart — Fenn "
    "in front, a caravan guard twice your width behind. The fight is short and "
    "muddy and ends with the guard sitting in the ditch reconsidering his "
    "career. Fenn talks, because Fenn always talks: a cellar under the old "
    "monastery, guild rent, and two travelers kept alive because a closed road "
    "earns the guild more than an empty one. The tunnel mouth, he says, is "
    "east where the ridge folds."
)
AMBUSH_LOSE = (
    "The road is bad at this hour and worse at ambushes; you learn that with "
    "your ribs. You wake at the Lantern Inn with linen bound tight and a "
    "headache in your teeth. Marla says nothing about it, which is the loudest "
    "thing she has ever said to you. The road keeps its carriers — for now."
)
CONFRONT_READY = (
    "The tunnel mouth breathes cold and lamp oil. You go in low along the "
    "wall, past stacked silver that nobody in Ravenford will ever admit to — "
    "and there they are: the two travelers, rope-burned and hollow-eyed and "
    "alive. You cut them loose before the bells ring again, and the hill lets "
    "all three of you out into the rain."
)
EPILOGUE = (
    "By dawn it is over Ravenford: the guild's sealed cellar, the rent ledger, "
    "the two names the notice board had begun to forget. Sergeant Dain takes "
    "Fenn's statement twice. Marla sets two extra cups on the bar and does not "
    "charge for them. The travelers walk the north road with the rain finally "
    "behind them — and the chronicle closes this chapter grieving for nothing."
)
CONFRONT_HINT = (
    "You stand at the monastery's undercroft door and feel the cold through "
    "your boots. Something below is worth the guild's money and the order's "
    "silence — but you hold threads, not a way in. Someone knows it: a factor "
    "with a ledger, a mercenary with a grudge, or the road itself. Finish one "
    "of those threads first."
)
BORIN_TALK_NARRATION = (
    "Borin does not look up from his cup, but for Borin the not-looking is "
    "practically confidence — the way of a man deciding how much of a secret "
    "he can afford to be careless with."
)
BORIN_TALK_LINE = (
    "Carts. Turn north of the oak, nights, since Midwinter. Ask the woman with "
    "the scales why the guild pays over rate for silver — then ask her where "
    "the carts come back from. You did not hear it here."
)
CONFRONT_QUIET = (
    "The undercroft is quiet now — a cold stair, a smell of lamp oil and rain. "
    "Whatever the guild kept down here, the light and the law have both found "
    "it. You do not need to go down again."
)


@dataclass
class BeatOutcome:
    ack: str
    narration: str
    kind: str = "scene_transition"  # checkpoint kind; None -> persist only
    mechanics: dict[str, Any] | None = None
    dialogue: list[dict[str, str]] = field(default_factory=list)
    new_leads: list[str] = field(default_factory=list)
    suggestions: list[dict[str, str]] = field(default_factory=list)


class PendingCheckStale(Exception):
    """The board moved between calling a check and throwing the die."""


# ---------------------------------------------------------------------------
# Routing
# ---------------------------------------------------------------------------

_TALK_V = r"\b(talk|speak|ask|question|chat|greet|approach|tell)\b"
_INSPECT_V = r"\b(inspect|examine|search|study|look|read|check|peer|glance|notice|watch)\b"
_STEAL_V = r"\b(steal|rob|pick the lock|picklock|lift|break into|force the|raid)\b"
_FIGHT_V = r"\b(attack|fight|hit|punch|strike|stab|swing|challenge|draw (my |the )?(sword|blade|knife))\b"
_REST_V = r"\b(rest|sleep|bead|bed|nap|camp|bandage|tend|recover)\b"
_LEAVE_V = r"\b(leave|step out|go out|head out|exit|outside|depart|walk out|head north|take the road|northern road|north road)\b"
_RETURN_V = r"\b(return|go back|come back|back to|enter)\b"

_MARLA = re.compile(r"\bmarla\b|\binnkeeper\b", re.IGNORECASE)
_INN = re.compile(r"\binn\b|\blantern\b", re.IGNORECASE)
_BORIN = re.compile(r"\bborin\b|\bmercenary\b|\bdrunk\b", re.IGNORECASE)
_BOX = re.compile(r"strongbox|strong box|lockbox|storeroom|pantry|till|till box", re.IGNORECASE)
_BOARD = re.compile(r"notice|board|plea|posting|parchment", re.IGNORECASE)
_LEDGER = re.compile(r"ledger|guest ?book|books", re.IGNORECASE)
_CELLAR = re.compile(r"cellar|trapdoor|cellars", re.IGNORECASE)
_MARKET = re.compile(r"\bmarket\b|\bstalls?\b|\bshops?\b|sella", re.IGNORECASE)
_MONASTERY = re.compile(r"monastery|chapel|anselm|beacon|monks?", re.IGNORECASE)

# --- arc routing (solutions + resolution) -----------------------------------
_SELLA = re.compile(r"\bsella\b|\bfactor\b|\bscales\b", re.IGNORECASE)
_PERSUADE_RE = re.compile(r"\b(persuade|convince|charm|plead|plea|reason with|talk down|coax)\b", re.IGNORECASE)
_INTIMIDATE_RE = re.compile(r"\b(intimidate|threaten|pressure|lean on|scare|strong-arm|cow)\b", re.IGNORECASE)
_CONFRONT_RE = re.compile(
    r"\b(confront|enter the cellar|into the cellar|cellar stairs|go down|descend|"
    r"open the cellar|the passage|down into the dark|enter the tunnel|into the tunnel)\b",
    re.IGNORECASE,
)
_AMBUSH_RE = re.compile(
    r"\b(ambush|waylay|lie in wait|wait in ambush|strike first)\b|\b(attack|stop|rob)\b.*\b(carriers?|cart|wagons?)\b",
    re.IGNORECASE,
)
_TRACKS_RE = re.compile(r"\b(tracks?|ruts?|wheel|wheels?|mud)\b", re.IGNORECASE)
_LIGHTS_RE = re.compile(r"\b(lanterns?|lights?|bells?)\b", re.IGNORECASE)
_WATCH_RE = re.compile(r"\b(watch|wait|follow|tail|shadow|observe|trail|stake out|keep watch)\b", re.IGNORECASE)


def route(text: str) -> str:
    """Classify player text to a beat name (or ``pipeline`` fallback)."""
    t = text.strip()
    # Target-specific rules first: naming the ledger/board/cellar wins over the
    # generic "mentions Marla" rules ("I search Marla's ledger" is an inspection).
    if _BOX.search(t) and re.search(_STEAL_V, t, re.IGNORECASE):
        return "steal"
    if _BORIN.search(t) and re.search(_FIGHT_V, t, re.IGNORECASE):
        return "fight"
    if _PERSUADE_RE.search(t) and (_SELLA.search(t) or _BORIN.search(t)):
        return "persuade"
    if _INTIMIDATE_RE.search(t) and (_SELLA.search(t) or _BORIN.search(t)):
        return "intimidate"
    if _AMBUSH_RE.search(t):
        return "ambush"
    if _CONFRONT_RE.search(t):
        return "confront"
    if _TRACKS_RE.search(t) and re.search(_INSPECT_V + r"|\b(follow|study)\b", t, re.IGNORECASE):
        return "clue_tracks"
    if _LIGHTS_RE.search(t) and _WATCH_RE.search(t):
        return "clue_lanterns"
    if _BOARD.search(t) and (re.search(_INSPECT_V, t, re.IGNORECASE) or not re.search(_TALK_V, t, re.IGNORECASE)):
        return "inspect_board"
    if _LEDGER.search(t) and (re.search(_INSPECT_V, t, re.IGNORECASE) or not re.search(_TALK_V, t, re.IGNORECASE)):
        return "inspect_ledger"
    if _CELLAR.search(t) and re.search(_INSPECT_V, t, re.IGNORECASE):
        return "inspect_cellar"
    if _BORIN.search(t) and re.search(_TALK_V, t, re.IGNORECASE):
        return "talk_borin"
    if _MARLA.search(t) and re.search(_TALK_V, t, re.IGNORECASE):
        return "talk"
    if _MARLA.search(t) and re.search(_INSPECT_V, t, re.IGNORECASE):
        return "talk"
    if re.search(r"\b(ask|question|talk|speak)\b.*\b(wagon|travelers?|guests?|missing)\b", t, re.IGNORECASE):
        return "talk"
    if re.search(_REST_V, t, re.IGNORECASE):
        return "rest"
    if _MARKET.search(t) and re.search(r"\b(go|walk|head|travel|visit|leave|enter|toward|towards|to)\b", t, re.IGNORECASE):
        return "travel_market"
    if _MONASTERY.search(t) and re.search(r"\b(go|walk|head|travel|visit|leave|enter|toward|towards|to)\b", t, re.IGNORECASE):
        return "travel_monastery"
    if _INN.search(t) and re.search(_RETURN_V, t, re.IGNORECASE):
        return "return"
    if re.search(_LEAVE_V, t, re.IGNORECASE):
        return "leave"
    if re.search(_INSPECT_V, t, re.IGNORECASE):
        return "inspect"
    return "pipeline"


# ---------------------------------------------------------------------------
# Engine
# ---------------------------------------------------------------------------


class ActEngine:
    """Resolves one player action against the live session (state in, prose out)."""

    def __init__(
        self,
        session: PlaySession,
        *,
        provider: Provider | None = None,
        meter: MeterRegistry | None = None,
        prefs: ContentPrefs | None = None,
    ) -> None:
        self.session = session
        self.provider = provider
        self.meter = meter or MeterRegistry()
        self.prefs = prefs
        self._seed_roll: int | None = None
        self._suspend_on_check = False
        self._act_token: str | None = None

    # -- helpers ------------------------------------------------------------
    @property
    def state(self):
        return self.session.state

    def _modifiers(self, skill: str) -> tuple[int, int]:
        attr = SKILL_ATTRIBUTE.get(skill, "Wits")
        value = int(self.state.pc.get("attributes", {}).get(attr, 10))
        return (value - 10) // 2, TRAINED_BONUS

    def _check_spec(self, skill: str, attr_mod: int, skill_mod: int,
                    dc: int, difficulty: str, label: str) -> dict[str, Any]:
        """Player-facing description of a check, for the pending-throw prompt."""
        return {
            "label": label,
            "skill": skill.title(),
            "attribute": SKILL_ATTRIBUTE.get(skill, "Wits"),
            "attribute_mod": attr_mod,
            "skill_mod": skill_mod,
            "total_mod": attr_mod + skill_mod,
            "dc": dc,
            "difficulty": difficulty,
        }

    def _check(self, skill: str, difficulty: str, label: str) -> tuple[dict[str, Any], CheckResult]:
        """Roll one d20 check for a beat; returns (mechanics contract, result).

        With ``suspend_on_check`` (and no seeded die) the roll is deferred —
        the beat raises :class:`CheckSuspension` so the player throws the die;
        the second call carries the thrown face as ``seed_roll``.
        """
        attr_mod, skill_mod = self._modifiers(skill)
        dc = dc_for_band(difficulty) or 10
        if self._suspend_on_check and self._seed_roll is None:
            raise CheckSuspension(self._check_spec(skill, attr_mod, skill_mod, dc, difficulty, label))
        request = CheckRequest(
            campaign_id=self.session.campaign_id,
            skill=skill.title(),
            attribute_mod=attr_mod,
            skill_mod=skill_mod,
            dc=dc,
            difficulty=difficulty,
        )
        result = roll_check(request, roll=self._seed_roll)
        self._seed_roll = None  # a seed only steers the action's first check
        if result.outcome in SUCCESS_OUTCOMES:
            self.state.award_skill(
                skill,
                30 if result.outcome == Outcome.Exceptional else 20,
                label.split("—")[0].strip(),
            )
        mech = {
            "label": label,
            "roll": f"d20{attr_mod + skill_mod:+d}" if (attr_mod + skill_mod) else "d20",
            "total": result.total,
            "detail": result.cost,
            "d20": result.roll,
            "outcome": result.outcome.value,
            "dc": result.dc,
            "skill": skill.title(),
            "attribute": SKILL_ATTRIBUTE.get(skill, "Wits"),
        }
        return mech, result

    @staticmethod
    def _succeeded(result: CheckResult) -> bool:
        return result.outcome in SUCCESS_OUTCOMES

    def _feed(self, kind: str, **payload: Any) -> None:
        self.state.append_feed(kind, **payload)

    def _remember(
        self,
        npc: str,
        text: str,
        *,
        delta: int = 0,
        reason: str = "",
        kind: str = "",
        sentiment: int = 0,
        salience: int = 2,
    ) -> bool:
        """Record one NPC memory and, when it is new, move their meter.

        The meter move is tied to the memory write (design §2+§3): the same
        interaction sites do both, a repeated beat that writes no new memory
        moves nothing, and the engine — never the model — owns the value.
        """
        if not self.state.remember(npc, text, kind=kind, sentiment=sentiment, salience=salience):
            return False
        if delta:
            self.state.adjust_attitude(npc, delta, reason or text)
        return True

    def _narrate_event(self, text: str) -> None:
        self._feed("narration", text=text)

    def _discover_lead(self, outcome: BeatOutcome) -> None:
        if self.state.set_lead_stage("rumored"):
            outcome.new_leads.append("Missing Travelers")
            self._feed("lead", lead="Missing Travelers — two travelers vanished on the Northern Road")

    def _advance_to(self, stage: str) -> bool:
        return self.state.set_lead_stage(stage)

    # -- pending throws -------------------------------------------------------
    def _pending_token(self, text: str) -> str:
        """Fingerprint of (campaign, action text, board) — the throw's anchor."""
        material = f"{self.session.campaign_id}|{text}|{self.state.to_json()}"
        return hashlib.sha256(material.encode("utf-8")).hexdigest()[:20]

    def _pending_payload(self, spec: dict[str, Any]) -> dict[str, Any]:
        """The contract for a check that waits on the player's own throw."""
        return {
            "ack": "The bones are called for — one throw, and the tale moves.",
            "mechanics": None,
            "pending_check": {**spec, "token": self._act_token},
            "narration": None,
            "dialogue": [],
            "newLeads": [],
            "suggestions": [],
            "system": [],
        }

    # -- public API ----------------------------------------------------------
    def act(
        self,
        text: str,
        seed_roll: int | None = None,
        *,
        suspend_on_check: bool = False,
        pending_token: str | None = None,
    ) -> tuple[dict[str, Any], str | None]:
        """Resolve one action. Returns (frontend-contract response, checkpoint kind).

        Two-phase throws: with ``suspend_on_check`` the engine stops before the
        action's first surfaced check and returns a ``pending_check`` payload
        instead of rolling. The player throws the die; the caller calls again
        with that face as ``seed_roll`` and the payload's ``pending_token`` to
        resolve the beat. A stale token means the board moved — nothing lands.
        """
        self._seed_roll = seed_roll if seed_roll is not None else None
        self._suspend_on_check = suspend_on_check
        t = (text or "").strip()
        self.state.actions_taken += 1
        self._feed("system", text=f"❧ {t}")
        # Fingerprint the moment the check was called from: the board must be
        # untouched between the call and the throw for the die to land home.
        self._act_token = self._pending_token(t)
        if pending_token is not None and pending_token != self._act_token:
            raise PendingCheckStale("the board has moved since the check was called")

        beat = route(t)
        handler = getattr(self, f"_beat_{beat}")
        seq_before = self.state.feed_seq
        try:
            if beat == "pipeline":
                outcome, checkpoint = handler(t)
            else:
                outcome = handler(t)
                checkpoint = outcome.kind
        except CheckSuspension as suspension:
            # Nothing persists on a pending throw: no checkpoint, no feed write.
            return self._pending_payload(suspension.spec), None

        # System lines the beat itself wrote (clues found, routes opened, notes)
        # are echoed in the response so the live feed shows them immediately.
        system_lines = [
            e["text"]
            for e in self.state.feed
            if e.get("kind") == "system"
            and int(str(e.get("id", "live-0")).split("-")[-1] or 0) > seq_before
        ]

        response = {
            "ack": outcome.ack,
            "mechanics": outcome.mechanics,
            "narration": outcome.narration,
            "dialogue": outcome.dialogue,
            "newLeads": outcome.new_leads,
            "suggestions": outcome.suggestions,
            "system": system_lines,
        }
        # Mirror the response into the chronicle feed so a page reload
        # reconstructs exactly what the player saw live.
        if outcome.mechanics:
            m = outcome.mechanics
            self._feed("dice", roll={
                "label": m["label"], "dice": m["roll"], "total": m["total"],
                "detail": m.get("detail"), "d20": m.get("d20"), "outcome": m.get("outcome"),
                "dc": m.get("dc"),
            })
        self._narrate_event(outcome.narration)
        for d in outcome.dialogue:
            self._feed("dialogue", speaker=d["speaker"], text=d["line"])
        return response, checkpoint

    # -- beats ---------------------------------------------------------------
    def _beat_talk(self, text: str) -> BeatOutcome:
        st = self.state
        st.note("talked:travelers")
        st.advance_minutes(5)
        out = BeatOutcome(ack="Marla sets the cup down.", narration=TALK_REPEAT, kind="talk")
        if st.lead_stage == "unheard":
            out.narration = TALK_FIRST
            out.dialogue = [{"speaker": "Marla Voss", "line": TALK_LINE_FIRST}]
            self._discover_lead(out)
            self._remember(
                "marla", "asked about the travelers who never came back",
                kind="conversation", delta=+5,
                reason="asked about the travelers who never came back",
            )
        elif st.lead_stage == "rumored":
            self._advance_to("accepted")
            out.narration = TALK_ACCEPT_NARRATION
            out.dialogue = [{"speaker": "Marla Voss", "line": TALK_ACCEPT_LINE}]
            self._remember(
                "marla", "promised to look into the missing travelers",
                kind="promise", sentiment=1, salience=3, delta=+10,
                reason="promised to look into the missing travelers",
            )
        elif st.lead_stage == "investigating":
            out.narration = TALK_INVESTIGATING
        elif st.lead_stage == "solved":
            out.narration = TALK_SOLVED
        return out

    def _beat_inspect_board(self, text: str) -> BeatOutcome:
        st = self.state
        st.note("inspected:notice-board")
        st.advance_minutes(5)
        first = st.lead_stage == "unheard"
        out = BeatOutcome(
            ack="You read the notice board.",
            narration=BOARD_FIRST if first else BOARD_REPEAT,
            kind="inspect",
        )
        self._discover_lead(out)
        return out

    def _beat_inspect_ledger(self, text: str) -> BeatOutcome:
        st = self.state
        st.note("inspected:marlas-ledger")
        st.advance_minutes(10)
        mech, result = self._check("investigation", "Moderate", "Investigation — Marla's guest ledger")
        out = BeatOutcome(
            ack="You turn the ledger pages.", narration=LEDGER_NARRATION, kind="inspect", mechanics=mech
        )
        self._remember(
            "marla", "went through her guest ledger page by page",
            kind="curiosity", delta=-5, reason="went through her guest ledger page by page",
        )
        if self._succeeded(result):
            if st.find_clue("ledger"):
                self._feed("system", text="❧ Clue found — the ledger's unsigned guests.")
            self._discover_lead(out)
            self._advance_to("investigating")
            out.narration = LEDGER_NARRATION + " " + LEDGER_SUCCESS
        else:
            out.narration = LEDGER_NARRATION + " " + LEDGER_FAIL
            if result.outcome == Outcome.SuccessWithCost:
                out.narration = LEDGER_NARRATION + " " + LEDGER_SUCCESS
        return out

    def _beat_inspect_cellar(self, text: str) -> BeatOutcome:
        st = self.state
        st.note("inspected:cellar-door")
        st.advance_minutes(5)
        if st.lead_stage in ("accepted", "investigating") and not st.travelers_freed:
            self._remember(
                "marla", "eyed the barred cellar door more than once",
                kind="suspicion", sentiment=-1, salience=2, delta=-5,
                reason="eyed the barred cellar door more than once",
            )
        if st.travelers_freed:
            body = (
                "The cellar door stands open now, hooked back against the wall. Cold "
                "air climbs the steps from the inn's deep stores — barrels, salt, and "
                "the shape of the story that used to live down here."
            )
        elif st.lead_stage in ("accepted", "investigating"):
            body = (
                "The cellar door is barred as always — but the bar has been lifted "
                "recently: the dust on its bracket is disturbed, and a thin woman's "
                "scarf is caught on the latch. Behind the inn's own stores, the "
                "passage keeps going down. You would not want to be found opening "
                "this without a reason Marla would accept."
            )
        else:
            body = (
                "The cellar door is shut fast, barred and padlocked, the way it has "
                "been since you were a child small enough to be scared of it. One "
                "step past the stores, and the Lantern becomes a warren. That is "
                "what the old gripes say, anyway."
            )
        return BeatOutcome(ack="You consider the cellar door.", narration=body, kind="inspect")

    def _beat_inspect(self, text: str) -> BeatOutcome:
        st = self.state
        target = "the room"
        for word in ("hearth", "fire", "window", "bar", "tables", "tankards", "bottles", "door"):
            if word in text.lower():
                target = word
                break
        st.note(f"inspected:{target}")
        st.advance_minutes(5)
        body = (
            f"You give the {target} the traveler's once-over — useful habits, nothing "
            "the room is ready to surrender yet. The rain keeps its own counsel "
            "outside; inside, only the fire and Marla's patience move."
        )
        return BeatOutcome(ack="You look closer.", narration=body, kind="inspect")

    def _beat_steal(self, text: str) -> BeatOutcome:
        st = self.state
        st.advance_minutes(15)
        mech, result = self._check("stealth", "Moderate", "Stealth — the storeroom strongbox")
        if self._succeeded(result):
            st.silver += 14
            st.note("stole:storeroom-strongbox")
            narration = STRONGBOX_SUCCESS
            if result.outcome == Outcome.SuccessWithCost:
                st.note("saw:sneaking")
                narration = STRONGBOX_COST
                self._remember(
                    "marla", "was robbed at the storeroom — and glimpsed who did it",
                    kind="theft", sentiment=-2, salience=4, delta=-30,
                    reason="robbed the storeroom strongbox — and was glimpsed doing it",
                )
            else:
                self._remember(
                    "marla", "was robbed — the storeroom strongbox came up light",
                    kind="theft", sentiment=-2, salience=4, delta=-25,
                    reason="robbed the storeroom strongbox",
                )
        elif result.outcome == Outcome.CriticalFailure:
            st.note("saw:sneaking")
            narration = STRONGBOX_CAUGHT
            self._remember(
                "marla", "caught them red-handed at the storeroom strongbox",
                kind="theft", sentiment=-2, salience=5, delta=-35,
                reason="caught red-handed at the storeroom strongbox",
            )
        else:
            st.note("heard:noise")
            narration = STRONGBOX_FAIL
            self._remember(
                "marla", "heard a suspicious clatter by the storeroom",
                kind="suspicion", sentiment=-1, salience=2, delta=-5,
                reason="made a suspicious clatter by the storeroom",
            )
        return BeatOutcome(ack="Your hand finds the storeroom latch.", narration=narration, kind="steal", mechanics=mech)

    def _beat_fight(self, text: str) -> BeatOutcome:
        st = self.state
        st.advance_minutes(10)
        mech, result = self._check("swordsmanship", "Moderate", "Swordsmanship — Borin the drunk")
        pc = st.pc
        hp = pc.get("hp", {"cur": 10, "max": 10})
        if result.outcome == Outcome.Exceptional:
            st.borin_down = True
            st.note("fought:drunk-mercenary")
            hp["cur"] = max(1, hp["cur"] - 1)
            narration = FIGHT_WIN
        elif self._succeeded(result):
            st.borin_down = True
            st.note("fought:drunk-mercenary")
            hp["cur"] = max(1, hp["cur"] - (6 if result.outcome == Outcome.SuccessWithCost else 3))
            narration = FIGHT_COST if result.outcome == Outcome.SuccessWithCost else FIGHT_WIN
        elif result.outcome == Outcome.CriticalFailure:
            st.note("fought:drunk-mercenary")
            hp["cur"] = max(1, hp["cur"] - 9)
            if "Bruised" not in pc.setdefault("conditions", []):
                pc["conditions"].append("Bruised")
            narration = FIGHT_CRIT
        else:
            st.note("fought:drunk-mercenary")
            hp["cur"] = max(1, hp["cur"] - 6)
            narration = FIGHT_LOSE
        pc["hp"] = hp
        self._remember(
            "marla",
            "started a brawl by her hearth" + (" — and won it" if st.borin_down else ""),
            kind="violence", sentiment=-1, salience=3, delta=-15,
            reason="started a brawl by her hearth",
        )
        if st.borin_down:
            self._remember(
                "borin", "was knocked down in a brawl by the fire",
                kind="violence", sentiment=-1, salience=2, delta=-15,
                reason="was knocked down in a brawl by the fire",
            )
        else:
            self._remember(
                "borin", "got the better of them in the brawl by the fire",
                kind="violence", sentiment=-1, salience=2, delta=-5,
                reason="brawled with them by the fire",
            )
        return BeatOutcome(ack="The hearthlight swings as the fight starts.", narration=narration, kind="fight", mechanics=mech)

    def _beat_leave(self, text: str) -> BeatOutcome:
        st = self.state
        if st.location == "northern-road":
            return BeatOutcome(
                ack="You are already on the road.",
                narration="The Northern Road holds you in its long grey corridor of rain. "
                          "Behind, the Lantern's light; ahead, the dark that keeps its books badly.",
                kind="travel",
            )
        st.location = "northern-road"
        st.visit_location("northern-road")
        st.advance_minutes(10)
        return BeatOutcome(ack="You step out into the rain.", narration=LEAVE_NARRATION, kind="travel")

    def _beat_return(self, text: str) -> BeatOutcome:
        st = self.state
        st.location = "lantern-inn"
        st.visit_location("lantern-inn")
        st.visits += 1
        st.advance_minutes(10)
        return BeatOutcome(
            ack="The door gives way to firelight.",
            narration=RETURN_NARRATION,
            kind="travel",
            dialogue=[{"speaker": "Marla Voss", "line": self._marla_greeting()}],
        )

    def _beat_rest(self, text: str) -> BeatOutcome:
        st = self.state
        st.advance_minutes(360)
        pc = st.pc
        for key in ("hp", "stamina"):
            bar = pc.get(key, {})
            bar["cur"] = bar.get("max", bar.get("cur", 10))
            pc[key] = bar
        if "Bruised" in pc.get("conditions", []) and pc["hp"]["cur"] >= pc["hp"]["max"]:
            pc["conditions"].remove("Bruised")
        return BeatOutcome(ack="You take your rest.", narration=REST_NARRATION, kind="rest")

    def _beat_travel_market(self, text: str) -> BeatOutcome:
        st = self.state
        st.location = "market"
        st.visit_location("market")
        st.advance_minutes(15)
        return BeatOutcome(ack="You take the market road.", narration=MARKET_NARRATION, kind="travel")

    def _beat_travel_monastery(self, text: str) -> BeatOutcome:
        st = self.state
        st.location = "old-monastery"
        st.visit_location("old-monastery")
        st.advance_minutes(25)
        return BeatOutcome(ack="You climb the monastery path.", narration=MONASTERY_NARRATION, kind="travel")

    # -- arc beats: clues, solutions, resolution -----------------------------
    def _unlock(self, solution_id: str) -> bool:
        if self.state.unlock_solution(solution_id):
            self._feed("system", text=f"❧ The way in — {SOLUTIONS[solution_id]}")
            return True
        return False

    def _clue(self, clue_id: str) -> bool:
        if self.state.find_clue(clue_id):
            self._feed("system", text=f"❧ Clue found — {CLUES[clue_id]}")
            return True
        return False

    def _maybe_open_ledger_trail(self) -> bool:
        """Two or more clues together spell the route (investigation solution)."""
        if len(self.state.clues) >= 2 and self.state.solution_path is None:
            return self._unlock("follow-the-clues")
        return False

    def _beat_clue_tracks(self, text: str) -> BeatOutcome:
        st = self.state
        st.note("inspected:road-ruts")
        st.advance_minutes(10)
        if st.location != "northern-road":
            return BeatOutcome(
                ack="You look for tracks.",
                narration="The ruts worth reading are out on the Northern Road; here, the mud "
                          "has better manners.",
                kind="inspect",
            )
        if st.lead_stage == "unheard":
            return BeatOutcome(ack="The mud is dumb.", narration=TRACKS_HINT, kind="inspect")
        mech, result = self._check("perception", "Moderate", "Perception — the wagon ruts")
        if self._succeeded(result):
            self._clue("tracks")
            self._advance_to("investigating")
            self._maybe_open_ledger_trail()
            return BeatOutcome(ack="You read the road.", narration=TRACKS_SUCCESS, kind="inspect", mechanics=mech)
        return BeatOutcome(ack="The rain owns the road.", narration=TRACKS_FAIL, kind="inspect", mechanics=mech)

    def _beat_clue_lanterns(self, text: str) -> BeatOutcome:
        st = self.state
        st.advance_minutes(30)
        if st.location not in ("northern-road", "old-monastery"):
            return BeatOutcome(
                ack="You find a vantage.",
                narration="The lights over the treeline are watched from the road or the "
                          "monastery path; from here, every lantern is just a lantern.",
                kind="inspect",
            )
        if st.lead_stage == "unheard":
            return BeatOutcome(ack="The dark stays dark.", narration=LANTERNS_HINT, kind="inspect")
        following = bool(re.search(r"\b(follow|tail|shadow)\b", text, re.IGNORECASE))
        skill = "stealth" if following else "perception"
        label = "Stealth — tailing the lantern-bearers" if following else "Perception — watching the treeline"
        mech, result = self._check(skill, "Moderate", label)
        if self._succeeded(result):
            self._clue("lanterns")
            self._advance_to("investigating")
            if following:
                self._unlock("shadow-them")
            self._maybe_open_ledger_trail()
            return BeatOutcome(ack="You keep still and count lanterns.", narration=LANTERNS_SUCCESS, kind="inspect", mechanics=mech)
        return BeatOutcome(ack="The cold wins.", narration=LANTERNS_FAIL, kind="inspect", mechanics=mech)

    def _beat_persuade(self, text: str) -> BeatOutcome:
        st = self.state
        st.advance_minutes(10)
        if not _SELLA.search(text):
            return self._beat_talk_borin(text)
        if st.location != "market":
            return BeatOutcome(
                ack="You straighten your cuffs.",
                narration="Sella Voss keeps to the guild's stall in the market — you would "
                          "have to go to her, and be ready to be seen doing it.",
                kind="talk",
            )
        if st.lead_stage == "unheard":
            return BeatOutcome(
                ack="You would not know what to ask.",
                narration="You do not yet know enough to make anyone nervous — learn what "
                          "the road has been swallowing first.",
                kind="talk",
            )
        mech, result = self._check("persuasion", "Difficult", "Persuasion — Sella Voss")
        out = BeatOutcome(ack="You make your case quietly.", narration=SELLA_PERSUADE_FAIL, kind="talk", mechanics=mech)
        if self._succeeded(result):
            self._advance_to("investigating")
            self._unlock("talk-it-out")
            out.narration = SELLA_PERSUADE_SUCCESS
        return out

    def _beat_intimidate(self, text: str) -> BeatOutcome:
        st = self.state
        st.advance_minutes(10)
        if _SELLA.search(text):
            if st.location != "market":
                return BeatOutcome(
                    ack="You set your jaw.",
                    narration="You will have to go to the market for that. Making a guild "
                              "factor nervous in her own stall is a thing done in person.",
                    kind="talk",
                )
            if st.lead_stage == "unheard":
                return BeatOutcome(
                    ack="You would not know where to press.",
                    narration="Threats without a question behind them are just noise. Learn "
                              "the story first — then choose who sweats.",
                    kind="talk",
                )
            mech, result = self._check("intimidation", "Difficult", "Intimidation — Sella Voss")
            out = BeatOutcome(ack="You lean into the space between you.", narration=SELLA_INTIMIDATE_FAIL, kind="talk", mechanics=mech)
            if self._succeeded(result):
                self._advance_to("investigating")
                self._unlock("lean-on-them")
                out.narration = SELLA_INTIMIDATE_SUCCESS
            return out

        # Borin: he can be cowed (slice NPC: "backs down if beaten or cowed").
        if st.location != "lantern-inn":
            return BeatOutcome(ack="Borin is not here.", narration="Wherever Borin is drinking tonight, it is not here.", kind="talk")
        if st.borin_down:
            return BeatOutcome(
                ack="Borin avoids your eye.",
                narration="Borin is in no hurry to be reacquainted. He mutters something "
                          "about carts turning north of the oak, and finds his cup suddenly "
                          "fascinating.",
                kind="talk",
            )
        mech, result = self._check("intimidation", "Moderate", "Intimidation — Borin")
        if self._succeeded(result):
            st.borin_down = True
            st.note("cowed:borin")
            self._remember(
                "borin", "was frightened into talking about the carts",
                kind="intimidation", sentiment=-2, salience=3, delta=-20,
                reason="was frightened into talking about the carts",
            )
            return BeatOutcome(
                ack="You lean in close.",
                narration="You do not put a hand on him — you do not need to. You lean in "
                          "close enough that the fire's crackle cannot cover your voice, and "
                          "the mercenary who has fought for worse pay than this decides he "
                          "has somewhere else to be. He mutters one thing on his way out.",
                kind="talk",
                mechanics=mech,
                dialogue=[{"speaker": "Borin", "line": "Carts. North of the oak. That is all you get from me."}],
            )
        return BeatOutcome(
            ack="You test the room.",
            narration="Borin has been intimidated by professionals, and you are not, tonight, "
                      "one of them. He grins into his cup and stays exactly where he is.",
            kind="talk",
            mechanics=mech,
        )

    def _beat_ambush(self, text: str) -> BeatOutcome:
        st = self.state
        st.advance_minutes(20)
        if st.location != "northern-road":
            return BeatOutcome(
                ack="You pick your ground.",
                narration="Ambushes happen on the road, and you are not on the road. The "
                          "carriers come and go on the Northern Road after dark.",
                kind="inspect",
            )
        if st.lead_stage != "investigating":
            return BeatOutcome(
                ack="The road is just road.",
                narration="You could wait out here, but you do not yet know what is worth "
                          "lying in wait for. Follow the threads first — the north road is "
                          "where they will end up crossing.",
                kind="inspect",
            )
        mech, result = self._check("swordsmanship", "Difficult", "Swordsmanship — the road carriers")
        if self._succeeded(result):
            st.note("fought:road-carriers")
            self._unlock("blades-out")
            return BeatOutcome(ack="Lanterns, then shouting.", narration=AMBUSH_WIN, kind="fight", mechanics=mech)
        pc = st.pc
        hp = pc.get("hp", {"cur": 10, "max": 10})
        hp["cur"] = max(1, hp["cur"] - (9 if result.outcome == Outcome.CriticalFailure else 6))
        pc["hp"] = hp
        st.note("fought:road-carriers")
        st.location = "lantern-inn"
        return BeatOutcome(ack="The mud gets its say.", narration=AMBUSH_LOSE, kind="fight", mechanics=mech)

    def _beat_talk_borin(self, text: str) -> BeatOutcome:
        st = self.state
        st.note("talked:borin")
        first_ask = st.remember("borin", "was asked about carts north of the oak", kind="conversation")
        st.advance_minutes(5)
        if st.location != "lantern-inn":
            return BeatOutcome(ack="You look for Borin.", narration="Wherever Borin is drinking tonight, it is not here.", kind="talk")
        if first_ask:
            # Only a real conversation — Borin actually present — moves his meter.
            st.adjust_attitude("borin", +5, "asked about carts north of the oak")
        if st.borin_down:
            return BeatOutcome(
                ack="Borin eyes you over his bruises.",
                narration="Borin is on the porch, reconsidering his choices and his jaw. He "
                          "does not have a lot to say to you. He does, however, still have "
                          "the grudge — which is halfway to a rumour.",
                kind="talk",
                dialogue=[{"speaker": "Borin", "line": "Carts. North of the oak. That is all you get from me, broken ribs and all."}],
            )
        return BeatOutcome(
            ack="Borin warms to his theme.",
            narration=BORIN_TALK_NARRATION,
            kind="talk",
            dialogue=[{"speaker": "Borin", "line": BORIN_TALK_LINE}],
        )

    def _beat_confront(self, text: str) -> BeatOutcome:
        st = self.state
        st.advance_minutes(20)
        if st.completed:
            return BeatOutcome(ack="The undercroft is quiet.", narration=CONFRONT_QUIET, kind="resolve")
        if st.location == "lantern-inn":
            return BeatOutcome(
                ack="You consider the inn's cellar door.",
                narration="The inn's cellar holds barrels and salt and one padlocked door that "
                          "the old gripes say goes further than it should — but the guild's "
                          "business is not kept under Marla's feet. Whatever is below, it is "
                          "below the old monastery. The road goes there when you do.",
                kind="inspect",
            )
        if st.location != "old-monastery":
            return BeatOutcome(
                ack="You would need to get there first.",
                narration="The cold you are after comes from under the Old Monastery. The "
                          "monastery path leaves Ravenford along the north road, past the "
                          "oak.",
                kind="inspect",
            )
        if st.solution_path is None:
            return BeatOutcome(ack="You weigh the door.", narration=CONFRONT_HINT, kind="inspect")
        # Resolution: the travelers come out, the arc completes.
        self._advance_to("solved")
        st.travelers_freed = True
        st.completed = True
        st.note("resolved:travelers")
        self._remember(
            "marla", "saw the missing travelers brought home safe out of the dark",
            kind="resolve", sentiment=2, salience=5, delta=+35,
            reason="brought the missing travelers home safe",
        )
        self._remember(
            "borin", "heard the travelers were pulled out of the dark below the monastery",
            kind="resolve", sentiment=2, salience=4, delta=+35,
            reason="pulled the travelers out of the dark below the monastery",
        )
        self._remember(
            "sella", "counted two travelers back among the living, and knows who did it",
            kind="resolve", sentiment=2, salience=4, delta=+35,
            reason="brought two travelers back among the living",
        )
        st.pc.setdefault("achievements", []).append(f"Freed the missing travelers (Day {st.day})")
        return BeatOutcome(
            ack="You go down into the cold.",
            narration=CONFRONT_READY,
            kind="resolve",
            dialogue=[
                {
                    "speaker": "The elder traveler",
                    "line": "You came. Nobody came for three nights. We had started to think the road forgot us too.",
                },
                {"speaker": "The chronicler", "line": EPILOGUE},
            ],
        )

    # -- Marla's memory greeting --------------------------------------------
    _GREETING_TEMPLATES = (
        "Welcome back. Marla looks up from the bar — she remembers {recalled}.",
        ("The door has hardly shut before Marla says it: she remembers {recalled}, "
         "and does not pretend otherwise."),
        ("Marla slides a cup across the bar without being asked. \"Back, then,\" she "
         "says, and lets you hear the rest in the pause: {recalled}."),
    )

    def _marla_greeting(self) -> str:
        st = self.state
        memories: list[str] = []
        for tag in st.marla_memory:
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
            elif tag.startswith("saw:"):
                memories.append("how you eyed her strongbox")
            elif tag.startswith("resolved:"):
                memories.append("the travelers you brought home")
        recalled = "; ".join(memories) if memories else "a quiet first visit"
        template = self._GREETING_TEMPLATES[(st.visits - 1) % len(self._GREETING_TEMPLATES)]
        return template.format(recalled=recalled)

    # -- pipeline fallback ---------------------------------------------------
    def _beat_pipeline(self, text: str) -> tuple[BeatOutcome, str | None]:
        """Unrecognised action: interpreter -> checks -> narrator (stub-friendly)."""
        st = self.state
        st.advance_minutes(5)
        pipeline = Pipeline(provider=self.provider, meter=self.meter)
        action = ActionInput(
            campaign_id=self.session.campaign_id,
            text=text,
            scene=SceneContext(
                npcs_present=self.present_npcs(),
                npc_memories=self._npc_memories_for_present(),
            ),
            seed_roll=self._seed_roll,
        )
        self._seed_roll = None
        try:
            result = pipeline.orchestrate(
                action, state=self._pipeline_state(), prefs=self.prefs,
                suspend_on_check=self._suspend_on_check,
            )
        except CheckSuspension:
            raise  # two-phase throw: the player's die resolves this check
        except Exception:  # noqa: BLE001 - provider failure never loses the turn
            out = BeatOutcome(
                ack="The chronicler's quill falters.",
                narration=(
                    "The moment hangs unfinished — the sending failed before the "
                    "chronicler could answer. Nothing was lost; try the words again."
                ),
                kind="",
            )
            return out, None

        mechanics = None
        for check in result.checks:
            snapshot = check.request_snapshot
            if check.surfaced:
                if mechanics is None:
                    mechanics = {
                        "label": f"{snapshot.skill} check",
                        "roll": "d20",
                        "total": check.total,
                        "detail": check.cost,
                        "d20": check.roll,
                        "outcome": check.outcome.value if hasattr(check.outcome, "value") else check.outcome,
                        "dc": snapshot.dc,
                        "skill": snapshot.skill,
                    }
                continue
            if check.hidden and not check.trivial_auto:
                # Quiet checks roll behind the screen — the player gets no die,
                # but still gets the verdict in the chronicle (passed/failed
                # with the math). Trivial autos never rolled; stay silent.
                passed = check.outcome in SUCCESS_OUTCOMES
                self._feed(
                    "system",
                    text=f"⚄ {snapshot.skill} check — {'passed' if passed else 'failed'} · {check.total} vs DC {snapshot.dc}.",
                )

        narration = result.narration.narration
        out = BeatOutcome(
            ack="The chronicler weighs your move.",
            narration=narration,
            kind="",  # free text: persist, no autosave churn
            mechanics=mechanics,
            dialogue=[{"speaker": d.npc, "line": d.line} for d in result.narration.npc_dialogue],
            suggestions=[{"label": s.label, "command": s.command}
                         for s in result.narration.suggested_actions],
        )
        # world-fact attempts are recorded but change nothing (authority invariant)
        if result.intent.world_fact_attempt:
            self._feed("system", text="❧ (the world does not take dictation)")
        return out, None

    # -- world views ----------------------------------------------------------
    def present_npcs(self) -> list[str]:
        return npc_names(self.state)

    def _npc_memories_for_present(self) -> dict[str, list[str]]:
        """Top memories each present NPC keeps about the player (for the narrator)."""
        out: dict[str, list[str]] = {}
        for name in self.present_npcs():
            mems = self.state.memories_for(npc_slug(name), limit=NARRATOR_MEMORY_LIMIT)
            if mems:
                out[name] = [m["text"] for m in mems]
        return out

    def _pipeline_state(self) -> dict[str, Any]:
        st = self.state
        pc = st.pc
        return {
            "inventory": list(pc.get("equipment", [])),
            "npcs_alive": {"borin": not st.borin_down, "marla": True},
            "location": st.location,
            "known_locations": ["lantern-inn", "northern-road", "market"],
            "facts": [],
            "pc_name": pc.get("name", "the hero"),
        }
