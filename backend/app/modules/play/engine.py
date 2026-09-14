"""Live act engine: player text -> engine-resolved beat -> narration + effects.

Invariants (GDD): the model is never the database; player text is an attempt
only. The engine owns every beat's *facts* — state transitions, d20 checks
(rules engine), feed events, lead/clue progression, Marla's memory — and the
narrator writes every word of story from them (P14, owner ruling: no scripted
answers anywhere). Anything unrecognised falls through to the 10-step Pipeline
(interpreter -> checks -> narrator). Without a connected model nothing is
narrated at all: the turn fails machine-readably (P12) rather than in cover
prose.
"""
from __future__ import annotations

import hashlib
import random
import re
from dataclasses import dataclass, field
from typing import Any

from app.modules.actions.pipeline import ActionInput, Pipeline
from app.modules.actions.suggest import SceneContext
from app.modules.ai.metering import MeterRegistry
from app.modules.ai.providers import Provider, ProviderError
from app.modules.memory.npc_memory import display_name, named_npc, npc_slug
from app.modules.narrator.prefs import ContentPrefs
from app.modules.narrator.prompts import PromptContext
from app.modules.narrator.service import narrate
from app.modules.npc.mood import DEFAULT_MOOD, surfaced_mood
from app.modules.npc.personality import (
    Price,
    SocialContext,
    approach_for_skill,
    biased_mood,
    disposition_for,
    profile_for,
    scale_relationship_delta,
    social_adjustment,
)
from app.modules.play.session import PlaySession
from app.modules.play.state import CLUES, SOLUTIONS
from app.modules.play.view import location_name, npc_names, npcs_present
from app.modules.progression.xp import play_skill_xp
from app.modules.rules.checks import (
    CheckRequest,
    CheckResult,
    CheckSuspension,
    Outcome,
    dc_for_band,
    is_long_odds,
    roll_check,
)
from app.modules.story.saga import refresh_saga
from app.modules.story.scenes import (
    PROLOGUE,
    UPSTAIRS,
    UPSTAIRS_GOAL,
    UPSTAIRS_LABEL,
    SceneDirector,
    invitation_open,
    possible_moves,
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
#: Recent chronicle entries the narrator prompt carries for continuity (§25):
#: the model continues the live tale instead of restarting the scene.
NARRATOR_CHRONICLE_LIMIT = 8

SUCCESS_OUTCOMES = {Outcome.Success, Outcome.SuccessWithCost, Outcome.Exceptional}

# ---------------------------------------------------------------------------
# Beat narration (P14): every word the player reads as story is the model's
#
# The engine owns the state machine, the dice, and the facts. A beat hands the
# narrator a BeatBrief — its machine id, the facts its prose must convey, who
# speaks, and any staging the moment needs — and the narrator writes the prose
# and the dialogue from it. Nothing pre-written may stand as narration or
# dialogue (owner ruling, 2026-09-14): no authored beat text, no rotating
# variants, no greeting builders, no diminishing lines. Interface strings —
# dice voice, button and scene labels, short action echoes — stay code.
# ---------------------------------------------------------------------------

#: The beat-narration contract. Suggestions are the beat's own
#: (``possible_moves``): the model is not asked to invent buttons for a beat
#: the engine has already resolved.
BEAT_SCHEMA = (
    'Return ONLY the JSON object {"narration": "…", "npc_dialogue": '
    '[{"npc": "Name", "line": "…"}]} — raw JSON, no code fences, no '
    'commentary before or after. Write this beat of the tale in the '
    "narrator's voice, in your own words: convey every established fact "
    "above, phrased naturally — do not quote this brief — voice the speakers "
    "listed, and invent nothing beyond the facts. npc_dialogue is empty when "
    "nobody speaks. Every spoken line must appear exactly once: either woven "
    "into the narration as a quote or listed in npc_dialogue, never both."
)


@dataclass(frozen=True)
class BeatBrief:
    """One beat's narration request: the engine's facts, the model's words."""

    event: str
    facts: tuple[str, ...] = ()
    speakers: tuple[str, ...] = ()
    direction: str = ""
    length: str = "Concise"


#: The lead's live state, as facts for the prompt's [Active leads] block (§1).
LEAD_NOTES: dict[str, str] = {
    "rumored": "the two travelers vanished on the Northern Road; nothing else is known yet",
    "accepted": "the player promised Marla to find out what happened to them",
    "investigating": "the player is following the threads toward the monastery",
    "solved": "the travelers are home safe; the chapter is closed",
}
#: The second lead's title, once the lanterns are in play.
MONASTERY_LIGHTS = "The Monastery Lights"

#: A scene transition reads as a scene change, not a new map pin (§7). The
#: glyph is its own: ❧ marks clues/echoes, ❖ the relationship meter.
SCENE_MARK = "▸ Scene — {label}"

#: Difficulty of the contested ask when coin does not decide it (§6). A guarded
#: factor and a monastery porter are harder to talk round than a friendly room.
SOCIAL_DIFFICULTY: dict[str, str] = {
    "marla": "Moderate",
    "borin": "Moderate",
    "tomm": "Moderate",
    "sella": "Difficult",
    "anselm": "Difficult",
}


@dataclass
class BeatOutcome:
    """One resolved beat: state already changed, the words still to write.

    ``brief`` is the narration the beat owes the player (P14): the engine fills
    every fact and ``act`` has the narrator write it *after* the scene guard
    has decided whether this is the beat's own answer or a diminished one —
    so a guarded repeat costs one model call, never two. A ``brief`` of None
    means the narration is already final (the pipeline beat) or there is none
    (a pending throw, an interface-only echo).
    """

    ack: str
    narration: str = ""
    kind: str = "scene_transition"  # checkpoint kind; None -> persist only
    mechanics: dict[str, Any] | None = None
    dialogue: list[dict[str, str]] = field(default_factory=list)
    new_leads: list[str] = field(default_factory=list)
    suggestions: list[dict[str, str]] = field(default_factory=list)
    brief: BeatBrief | None = None


class PendingCheckStale(Exception):
    """The board moved between calling a check and throwing the die."""


class ActFailure(Exception):
    """A turn that could not be narrated honestly (P12 — never cover prose).

    Raised instead of returning in-world text when

    - ``connect_your_ai``: no model is connected (the HTTP surface refuses
      these before the engine; this is the engine's own backstop, so no
      caller can reach the built-in stub), or
    - ``provider_failed`` / ``turn_failed``: the model call failed mid-turn.
      ``detail`` is the scrubbed, player-visible message.

    A failed turn persists nothing (the checkpoint happens only after ``act``
    returns), so the same action can be sent again cleanly — with the same
    idempotency key, which the failure never consumed.
    """

    def __init__(self, code: str, detail: str = "") -> None:
        super().__init__(code)
        self.code = code
        self.detail = detail


#: Anything key-shaped or token-shaped that must never ride a failure detail.
_TOKEN_LIKE_RE = re.compile(r"(Bearer\s+\S+|sk-[A-Za-z0-9_\-]{6,})", re.IGNORECASE)


def _scrub_provider_error(message: str) -> str:
    """Strip key-shaped material from a provider failure, then cap the length.

    The transport's own message is already key-free; a failure detail is
    player-visible, so this is belt and braces.
    """
    text = _TOKEN_LIKE_RE.sub("[redacted]", str(message or ""))
    text = " ".join(text.split())
    return text[:200] or "the model call failed"


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

#: The prologue's one door: entering town and the Lantern (§intro).
_ENTER_INN_RE = re.compile(
    r"\b(enter|go in|get in|go inside|get inside|step in|step inside|inside|"
    r"in through|through the door|the door|the inn|into town|down into|"
    r"walk down|head down|go down|descend)\b",
    re.IGNORECASE,
)
#: At the prologue, walking on north is a real direction: the road out of town.
_NORTH_AWAY_RE = re.compile(
    r"\b(northern road|north road|head north|keep going|walk on|onward|set off)\b",
    re.IGNORECASE,
)

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
#: Overt romantic interest (systems slice 3): sets a mood, never a world fact.
_FLIRT_RE = re.compile(r"\b(flirt\w*|wink\w*|make eyes|tease)\b", re.IGNORECASE)
#: Leverage (systems slice 4, §6): an offer of coin, either word order — or a
#: payment aimed at a named character ("I pay the factor for the truth").
_OFFER_RE = re.compile(
    r"\b(?:offer\w*|pay|pays|paid|brib\w*|gift\w*|tip|tips|tipped|slip|slips|hand over|purchase|buy|buys|bought)\b"
    r"[^.]{0,60}\b(?:guilders?|silver|coin|coins|money|purse|price|fee|payment|bribe)\b"
    r"|\b(?:guilders?|silver|coin|coins|money|purse)\b[^.]{0,60}\b(?:offer\w*|pay|brib\w*|buy|buys|bought|purchase|tip)\b"
    r"|\b(?:pay|pays|paid|brib\w*|slip|slips|tip|tips|tipped)\b[^.]{0,60}"
    r"\b(?:marla|borin|sella|tomm|anselm|innkeeper|peddler|factor|porter|mercenary|monk)\b",
    re.IGNORECASE,
)
#: An offer that is really an ask for something (§6): only these can fall
#: through to a contested check when coin is not what the character wants.
_ASK_RE = re.compile(
    r"\b(truth|answers?|information|tells?|told|talk|word|words|secret|name|names|"
    r"know\w*|where|what|why|who|help|story|rumou?rs?)\b",
    re.IGNORECASE,
)

# --- inspiration (table applause) -------------------------------------------
#: Earned on story-driving beats, spent for advantage on one surfaced check.
INSPIRATION_MAX = 3
#: Band ladder rank: earning fires when a meter crosses into a warmer band.
_BAND_RANK = {"Hostile": 0, "Wary": 1, "Neutral": 2, "Warm": 3, "Bonded": 4}
#: Spending words: "I spend my inspiration", "I call on inspiration".
_SPEND_RE = re.compile(
    r"\b(spend|burn|use|using|call on|invoke|invoking)\b[^.]{0,24}\binspiration\b"
    r"|\binspiration\b[^.]{0,24}\b(spend|burn|use|using)\b",
    re.IGNORECASE,
)

# --- scene flow routing (slice 5, §7) ---------------------------------------
#: The stairs up to Marla's room: a micro-scene inside the inn, reached by
#: asking, by following her, or by taking her up on the invitation. The guard
#: never blocks it — an invitation is a transition trigger.
_UPSTAIRS_RE = re.compile(
    r"\b(upstairs|up the stairs|up those stairs|take the stairs|up to the room|"
    r"marla'?s room|her room|the room above)\b"
    r"|\b(go|going|head|heading|walk|walking|step|come|coming|lead|leading|"
    r"follow|following)\b[^.]{0,20}\bup\b[^.]{0,12}\b(stairs|steps|room)\b",
    re.IGNORECASE,
)
#: Back down: leaving the micro-scene resolves it back to its parent scene.
_DOWNSTAIRS_RE = re.compile(
    r"\b(downstairs|down the stairs|back down|go(es)? down|heading down|"
    r"leave the room|back to the bar|back to the common room|return to the bar|"
    r"the common room|the bar)\b",
    re.IGNORECASE,
)


def _action_key(beat: str, text: str) -> str:
    """Fingerprint of one action inside a scene (the anti-loop's repeat key).

    The same beat plus the same words is a repeat; three different inspections
    are only a run of idle beats. Normalized so punctuation and casing never
    split a repeat in two.
    """
    norm = re.sub(r"[^a-z0-9 ]+", " ", str(text or "").lower())
    norm = " ".join(norm.split())
    return f"{beat}:{norm[:60]}"


def route(text: str) -> str:
    """Classify player text to a beat name (or ``pipeline`` fallback)."""
    t = text.strip()
    # Spending Inspiration is a standing offer, never narrator fodder.
    if _SPEND_RE.search(t):
        return "spend_inspiration"
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
    if _OFFER_RE.search(t):
        return "offer"
    if _AMBUSH_RE.search(t):
        return "ambush"
    if _CONFRONT_RE.search(t):
        return "confront"
    if _FLIRT_RE.search(t):
        return "flirt"
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
        self._seed_roll2: int | None = None
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

    def _seen(self, beat: str) -> int:
        """How many times this beat family already resolved in this scene.

        Read *before* the beat is noted (0 on the first try). The beat hands
        the count to the narrator as a fact — "the player has done this
        before" — so the model writes a fresh second look at the same room
        instead of repeating itself (P13/P14).
        """
        return SceneDirector(self.state).current().seen(beat)

    def _check_spec(self, skill: str, attr_mod: int, skill_mod: int,
                    dc: int, difficulty: str, label: str, *,
                    dc_base: int | None = None, dc_why: str = "") -> dict[str, Any]:
        """Player-facing description of a check, for the pending-throw prompt.

        ``dc`` is the DC as adjusted (social checks carry the §6 shift);
        ``dc_base`` is the band's own value and ``dc_why`` the reason the two
        differ, so the player sees why the odds are what they are.
        ``long_odds`` marks a check only a natural 20 can pass (§6).
        """
        total_mod = attr_mod + skill_mod
        return {
            "label": label,
            "skill": skill.title(),
            "attribute": SKILL_ATTRIBUTE.get(skill, "Wits"),
            "attribute_mod": attr_mod,
            "skill_mod": skill_mod,
            "total_mod": total_mod,
            "dc": dc,
            "dc_base": int(dc if dc_base is None else dc_base),
            "dc_why": dc_why or None,
            "long_odds": is_long_odds(dc, total_mod),
            "difficulty": difficulty,
        }

    def _social_context(self, npc: str, skill: str, approach: str = "") -> SocialContext:
        """What a social check knows about its target right now (§6).

        The mood is read through the same content gate every surface uses, so
        the odds the player is shown match the chip and the narrator.
        """
        nsfw = bool(self.prefs and self.prefs.nsfw)
        mood = self.state.mood_of(npc)
        return SocialContext(
            slug=npc,
            approach=approach or approach_for_skill(skill),
            mood=surfaced_mood(str(mood["mood"]), nsfw=nsfw),
            mood_intensity=float(mood["intensity"]),
            attitude=self.state.attitude_for(npc),
        )

    def _check(self, skill: str, difficulty: str, label: str, *,
               npc: str = "", approach: str = "") -> tuple[dict[str, Any], CheckResult]:
        """Roll one d20 check for a beat; returns (mechanics contract, result).

        With ``suspend_on_check`` (and no seeded die) the roll is deferred —
        the beat raises :class:`CheckSuspension` so the player throws the die;
        the second call carries the thrown face as ``seed_roll``.

        A social check aimed at ``npc`` is adjusted per §6 (approach + mood −
        relationship credit) and reports the reason alongside the DC.
        """
        attr_mod, skill_mod = self._modifiers(skill)
        base_dc = dc_for_band(difficulty) or 10
        dc, why = base_dc, ""
        if npc:
            adjustment = social_adjustment(
                self._social_context(npc, skill, approach), base_dc
            )
            dc, why = adjustment.adjusted_dc, adjustment.why
        if self._suspend_on_check and self._seed_roll is None:
            spec = self._check_spec(
                skill, attr_mod, skill_mod, dc, difficulty, label,
                dc_base=base_dc, dc_why=why,
            )
            if self.state.inspired:
                # The prompt throws twice and keeps the higher (advantage).
                spec["advantage"] = True
            raise CheckSuspension(spec)
        request = CheckRequest(
            campaign_id=self.session.campaign_id,
            skill=skill.title(),
            attribute_mod=attr_mod,
            skill_mod=skill_mod,
            dc=dc,
            difficulty=difficulty,
        )
        roll2 = self._seed_roll2
        self._seed_roll2 = None
        burning = bool(self.state.inspired)
        if burning:
            # A spent point fires on the next surfaced resolution — win or
            # lose, it is gone. Seeded calls without a second face draw one.
            self.state.inspired = False
            if roll2 is None:
                roll2 = random.randint(1, 20)
        result = roll_check(request, roll=self._seed_roll,
                            roll2=roll2 if burning else None)
        self._seed_roll = None  # a seed only steers the action's first check
        # Every resolved attempt trains the skill that made it — a failed
        # persuasion still feeds Persuasion XP (play_skill_xp grades it).
        if not result.trivial_auto:
            xp = play_skill_xp(result.outcome)
            if xp:
                self.state.award_skill(skill, xp, label.split("—")[0].strip())
        mech = {
            "label": label,
            "roll": f"d20{attr_mod + skill_mod:+d}" if (attr_mod + skill_mod) else "d20",
            "total": result.total,
            "detail": result.cost,
            "d20": result.roll,
            "outcome": result.outcome.value,
            "dc": result.dc,
            "dc_why": why or None,
            "skill": skill.title(),
            "attribute": SKILL_ATTRIBUTE.get(skill, "Wits"),
        }
        if burning and result.kept_from:
            dropped = result.kept_from[1] if result.kept_from[0] == result.roll else result.kept_from[0]
            mech["advantage"] = True
            mech["d20_second"] = dropped
            self._feed("system", text=(
                f"✦ Inspiration burns — two dice fall ({result.kept_from[0]}, "
                f"{result.kept_from[1]}), the {result.roll} stands."
            ))
        return mech, result

    @staticmethod
    def _succeeded(result: CheckResult) -> bool:
        return result.outcome in SUCCESS_OUTCOMES

    def _feed(self, kind: str, **payload: Any) -> dict[str, Any]:
        """Append one feed event (the player-visible chronicle) and return it."""
        return self.state.append_feed(kind, **payload)

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
        Personality weights the size of the move (§5): a proud character takes
        an insult double, a lonely one over-weights attention.
        """
        if not self.state.remember(npc, text, kind=kind, sentiment=sentiment, salience=salience):
            return False
        delta = scale_relationship_delta(npc, kind, delta)
        if delta:
            self.state.adjust_attitude(npc, delta, reason or text)
        return True

    def _mood(self, npc: str, mood: str, intensity: float = 0.6) -> dict[str, Any]:
        """Set one NPC's live mood for an event (design §4).

        The engine owns moods, never the model — beats call this the way they
        call :meth:`_remember`. The raw event mood is first read through the
        character's personality (§5: a proud character's fear curdles into a
        grudge, a wary one's sudden warmth reads as a play); gated words
        (flirty/horny) then fall back to their same-family word unless the
        campaign's content settings allow them, so the save, the chip and the
        narrator prompt all agree.
        """
        nsfw = bool(self.prefs and self.prefs.nsfw)
        word = biased_mood(npc, mood)
        return self.state.set_mood(npc, surfaced_mood(word, nsfw=nsfw), intensity)

    def _narrate_event(self, text: str) -> dict[str, Any]:
        """Append one narration line to the chronicle and return the event."""
        return self._feed("narration", text=text)

    def _discover_lead(self, outcome: BeatOutcome) -> None:
        if self.state.set_lead_stage("rumored"):
            outcome.new_leads.append("Missing Travelers")
            self._feed("lead", lead="Missing Travelers — two travelers vanished on the Northern Road")

    def _advance_to(self, stage: str) -> bool:
        return self.state.set_lead_stage(stage)

    # -- scene flow (§7) ------------------------------------------------------
    def _route(self, text: str) -> str:
        """Route an action scene-aware: micro-scene moves win over map beats.

        The map's beats are location-level ("leave", "return", "travel"); a
        micro-scene has its own doors — upstairs and back down — and the room
        the player actually stands in decides which one the words name. At
        the prologue only its one door exists: town and the Lantern (§intro).
        """
        if self.state.location == PROLOGUE:
            # The prologue's one door: town and the Lantern. Walking on north
            # keeps the map honest; everything else rides the narrator live —
            # Marla is behind a door twenty paces off, not in this scene.
            # Spending Inspiration is a standing offer, even on the road.
            if _SPEND_RE.search(text):
                return "spend_inspiration"
            if _ENTER_INN_RE.search(text):
                return "enter_inn"
            if _NORTH_AWAY_RE.search(text):
                return "leave"
            if re.search(_LEAVE_V, text, re.IGNORECASE):
                return "enter_inn"
            if re.search(r"\b(look|view|survey|see|watch|town|below|gate)\b", text, re.IGNORECASE):
                return "prologue_look"
            if re.search(r"\b(listen|hear|quiet|silence|rain|river|night)\b", text, re.IGNORECASE):
                return "prologue_listen"
            return "pipeline"
        if self.state.scene == UPSTAIRS:
            if _DOWNSTAIRS_RE.search(text) and not _UPSTAIRS_RE.search(text):
                return "downstairs"
            return route(text)
        if _UPSTAIRS_RE.search(text):
            return "upstairs"
        return route(text)

    def _progress_markers(self) -> tuple[Any, ...]:
        """The story markers whose change means a beat made progress (§7).

        Progress is what a scene does *within* itself: a clue found, a lead
        advanced, a note recorded, a route opened, the world changed, the sheet
        or a relationship moved. Never the clock (every idle beat advances it)
        and never the transition itself — entering a scene is its opening, not
        its progress, which is what lets a room walked out of mid-flow read as
        unfinished rather than resolved.
        """
        st = self.state
        pc = st.pc or {}
        hp = pc.get("hp") or {}
        return (
            st.lead_stage,
            tuple(st.clues),
            tuple(st.marla_memory),
            st.solution_path,
            st.travelers_freed,
            st.completed,
            st.borin_down,
            st.silver,
            int(hp.get("cur", 0) or 0),
            tuple(sorted((str(k), int(v)) for k, v in st.attitudes.items())),
            tuple(sorted((str(k), len(v)) for k, v in st.moods.items())),
            len(st.npc_memory_log),
            tuple(sorted((str(k), int(v)) for k, v in st.skills.items())),
        )

    # -- inspiration (table applause) --------------------------------------
    def _earn_inspiration(self, lead_before: str,
                          markers_before: tuple[Any, ...],
                          bands_before: dict[str, str]) -> None:
        """Award Inspiration for story-driving beats (capped, one line each).

        The table applauds progress, not dice: a lead advanced, a clue found,
        a route opened, the travelers freed, the tale closed, or a meter
        crossing into a warmer band. Silver, HP and idle chatter earn nothing.
        """
        st = self.state
        room = INSPIRATION_MAX - int(st.inspiration or 0)
        if room <= 0:
            return
        earned = 0
        if st.lead_stage != lead_before:
            earned += 1
        if tuple(st.clues) != tuple(markers_before[1]):
            earned += 1
        if st.solution_path is not None and markers_before[3] is None:
            earned += 1
        if st.travelers_freed and not markers_before[4]:
            earned += 1
        if st.completed and not markers_before[5]:
            earned += 1
        for slug in st.attitudes:
            before = bands_before.get(slug, "Neutral")
            after = st.attitude_band_for(slug)
            if _BAND_RANK.get(after, 2) > _BAND_RANK.get(before, 2):
                earned += 1
                break
        granted = max(0, min(earned, room))
        if granted:
            st.inspiration = int(st.inspiration or 0) + granted
            self._feed("system", text=(
                f"✦ Inspiration +{granted} — the table applauds ({st.inspiration})."
            ))

    def _beat_spend_inspiration(self, text: str) -> BeatOutcome:
        """Burn one Inspiration: the next surfaced check throws twice."""
        st = self.state
        if st.inspired:
            return BeatOutcome(
                ack="Your inspiration is already burning.",
                narration="You hold the moment a breath longer — the next roll throws twice.",
                kind="",
            )
        if int(st.inspiration or 0) <= 0:
            return BeatOutcome(
                ack="No inspiration to spend — yet.",
                narration=(
                    "The table is quiet. Drive the story — a clue, a lead, "
                    "a warmer heart — and the applause will come."
                ),
                kind="",
            )
        st.inspiration = int(st.inspiration) - 1
        st.inspired = True
        self._feed("system", text=(
            f"✦ Inspiration spent ({st.inspiration} banked) — the next roll throws twice."
        ))
        return BeatOutcome(
            ack="✦ Inspiration burns.",
            narration=(
                "You breathe out, and the room seems to lean in with you. "
                "The next roll throws twice — the higher stands."
            ),
            kind="",
        )

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
    def narrate_opening(self) -> dict[str, Any]:
        """Write the chronicle's opening page (§intro, P14).

        The one story surface with no beat behind it: a new journey stands on
        the road above Ravenford before the player has done anything, so the
        engine hands the narrator the prologue's facts and the player's own
        sheet and the model writes the first page. Written at most once per
        journey — ``opening_pending`` is the one truth — so a reload reads the
        chronicle instead of re-narrating it, and a sheet that lands after the
        fact opens the chronicle as itself (``reopen_prologue_opening``).
        """
        st = self.state
        if not st.opening_pending:
            return {"narration": "", "dialogue": [], "suggestions": [], "already": True}
        pc = st.pc or {}
        name = str(pc.get("name") or "a nameless traveler")
        facts = [
            (
                "the tale opens on the road above Ravenford at dusk, in the rain: a "
                "long ridge of hedgerows and standing water, and below it one small "
                "walled town"
            ),
            (
                "Ravenford from the road: rooftops descending to a river, a stone "
                "bridge, and a single lantern burning in the one street that "
                "matters — the inn"
            ),
            (
                f"the newcomer is {name}"
                + (f", {pc['epithet']}" if pc.get("epithet") else "")
                + ", walking the road on their own two feet and known to nobody here yet"
            ),
        ]
        equipment = [str(item) for item in (pc.get("equipment") or []) if str(item).strip()]
        if equipment:
            facts.append("what they carry: " + ", ".join(equipment[:6]))
        drives = [str(d) for d in (pc.get("drives") or []) if str(d).strip()]
        if drives:
            facts.append("what drives them: " + "; ".join(drives[:3]))
        background = str(pc.get("background") or "").strip()
        if background:
            facts.append(f"what the road knows about them: {background}")
        facts.append(
            "nobody has seen them yet: the inn's door is the first choice the road "
            "offers, and the rain keeps falling until it is made"
        )
        brief = BeatBrief(
            event="prologue.opening",
            facts=tuple(facts),
            length="Standard",
            direction=(
                "open the tale: the world first, then the newcomer inside it; close "
                "on the inn's light and the road down to its door"
            ),
        )
        narration, dialogue = self._narrate(
            "the tale opens on the road above Ravenford", brief
        )
        st.opening_pending = False
        event = self._narrate_event(narration)
        # A beat can slip in before the page asks for its first page: the
        # opening is the chronicle's first line, so it goes back to the top.
        if len(st.feed) > 1 and st.feed[-1] is event:
            st.feed.remove(event)
            st.feed.insert(0, event)
        return {
            "narration": narration,
            "dialogue": dialogue,
            "suggestions": possible_moves(st),
            "already": False,
        }

    def act(
        self,
        text: str,
        seed_roll: int | None = None,
        *,
        suspend_on_check: bool = False,
        pending_token: str | None = None,
        seed_roll2: int | None = None,
    ) -> tuple[dict[str, Any], str | None]:
        """Resolve one action. Returns (frontend-contract response, checkpoint kind).

        Two-phase throws: with ``suspend_on_check`` the engine stops before the
        action's first surfaced check and returns a ``pending_check`` payload
        instead of rolling. The player throws the die; the caller calls again
        with that face as ``seed_roll`` and the payload's ``pending_token`` to
        resolve the beat. A stale token means the board moved — nothing lands.

        Every resolved beat is also scene bookkeeping (§7): the director counts
        it, notes whether it moved the story, answers repeated/idle actions
        with a diminishing reply pointing at what is still possible, and knows
        which scene the player stands in — while a transition itself is never
        blocked by that guard.
        """
        self._seed_roll = seed_roll if seed_roll is not None else None
        self._seed_roll2 = seed_roll2 if seed_roll2 is not None else None
        self._suspend_on_check = suspend_on_check
        t = (text or "").strip()
        self.state.actions_taken += 1
        self._feed("system", text=f"❧ {t}")
        # Fingerprint the moment the check was called from: the board must be
        # untouched between the call and the throw for the die to land home.
        self._act_token = self._pending_token(t)
        if pending_token is not None and pending_token != self._act_token:
            raise PendingCheckStale("the board has moved since the check was called")

        director = SceneDirector(self.state)
        director.ensure()  # a legacy save boots into its map location's scene
        lead_before = self.state.lead_stage
        scene_before = self.state.scene
        markers_before = self._progress_markers()
        bands_before = {
            slug: self.state.attitude_band_for(slug)
            for slug in list(self.state.attitudes.keys())
        }
        beat = self._route(t)
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

        # -- scene bookkeeping (§7): count the beat, note progress, guard loops
        transitioned = scene_before != self.state.scene
        if transitioned:
            # A transition names the new scene for the chronicle; its bridging
            # narration is the beat's own (§7: on transition — bridging prose,
            # a clock that moved, a room that changed).
            self._feed("system", text=SCENE_MARK.format(label=director.current().label))
        progress = self._progress_markers() != markers_before
        key = _action_key(beat, t)
        director.note_beat(key=key, progress=progress)
        if not progress and not transitioned and director.diminishing(key):
            # Ground already walked: the room answers shorter and points
            # forward, in words the model writes fresh. A live transition is
            # never guarded — the fiction opened a door and the engine takes
            # it (user ruling, §7) — and neither is new ground: a genuinely
            # fresh attempt must narrate, so the wall can never settle over
            # the whole room (P13). The beat's own brief is replaced here,
            # before it is written, so a guarded repeat costs one call.
            self._diminish(outcome, t)
            director.note_diminished()
        if self.state.lead_stage != lead_before:
            # A story-beat boundary moves the scene's aim — never the player.
            director.story_boundary()

        # The words are the model's (P14): one call per beat, made here — after
        # the guard, so this is either the beat's own answer or the diminished
        # one, and the chronicle never shows prose the guard would have
        # replaced. Nothing pre-written stands in when it fails (P12).
        if outcome.brief is not None:
            outcome.narration, outcome.dialogue = self._narrate(
                t, outcome.brief, outcome.mechanics
            )

        # Saga digest (#4 continuity): checkpoint beats re-roll the rolling
        # recap so the narrator keeps act one in mind by act ten; idle beats
        # refresh only on the fallback cadence. Deterministic — no model call.
        refresh_saga(self.state, location_label=location_name(self.state),
                     force=bool(transitioned or progress))
        # Inspiration (table applause): story-driving beats earn it.
        self._earn_inspiration(lead_before, markers_before, bands_before)

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

    # -- narration (P14) ------------------------------------------------------

    def _narrate(self, text: str, brief: BeatBrief,
                 mechanics: dict[str, Any] | None = None
                 ) -> tuple[str, list[dict[str, str]]]:
        """Write one beat's prose (P14): the engine's facts, the model's words.

        Returns ``(narration, dialogue)``. The prompt carries the promised
        state — scene and goal, present NPCs with their memories, moods and
        dispositions, the leads, the chronicle tail, the saga, the dice
        result — plus the beat's own facts, so the phrasing is free but the
        truth is not. Nothing pre-written may stand as narration: a turn that
        cannot be narrated honestly fails machine-readably (P12), so no caller
        is ever handed cover prose.
        """
        if self.provider is None:
            raise ActFailure("connect_your_ai", "no model connected")
        scene = SceneDirector(self.state).scene_block()
        pc = self.state.pc or {}
        ctx = PromptContext(
            location=location_name(self.state),
            scene=f"{scene['label']} ({scene['state']}) — scene goal: {scene['goal']}",
            player_character={
                "name": pc.get("name", "the hero"),
                "description": pc.get("epithet") or "an adventurer",
            },
            npcs=self._narrator_npcs(),
            leads=self._narrator_leads(),
            chronicle=self._chronicle_tail(),
            saga=self.state.saga or "",
            player_action=text,
            mechanical_result=dict(mechanics or {}),
            beat_event=brief.event,
            beat_facts=list(brief.facts),
            beat_speakers=list(brief.speakers),
            beat_direction=brief.direction,
            length=brief.length,
            output_schema=BEAT_SCHEMA,
        )
        try:
            output, _bundle = narrate(
                ctx, provider=self.provider, meter=self.meter,
                campaign_id=self.session.campaign_id, prefs=self.prefs,
            )
        except ProviderError as exc:
            raise ActFailure("provider_failed", _scrub_provider_error(str(exc))) from exc
        except ActFailure:
            raise
        except Exception as exc:  # an unexpected error is still not cover prose
            raise ActFailure(
                "turn_failed", _scrub_provider_error(f"{type(exc).__name__}: {exc}")
            ) from exc
        return output.narration, [
            {"speaker": d.npc, "line": d.line} for d in output.npc_dialogue
        ]

    def _narrator_npcs(self) -> list[dict[str, Any]]:
        """Present NPCs as the narrator prompt wants them (§25 + slices 2-5).

        Moods ride surfaced (content-gated at the same helper the chip uses)
        and memories ride bounded — the same shape the pipeline's scene
        context builds, so one round of context serves both callers.
        """
        memories = self._npc_memories_for_present()
        moods = self._npc_moods_for_present()
        dispositions = self._npc_dispositions_for_present()
        out: list[dict[str, Any]] = []
        for npc in npcs_present(self.state, self.prefs):
            full = str(npc.get("name") or "")
            short = full.split()[0]
            entry: dict[str, Any] = {"name": full, "note": str(npc.get("note") or "")}
            mood = moods.get(short) or {}
            if mood:
                entry["mood"] = mood.get("mood")
                entry["mood_intensity"] = mood.get("intensity")
            remembered = memories.get(short)
            if remembered:
                entry["remembers"] = remembered
            disposition = dispositions.get(short)
            if disposition:
                entry["disposition"] = disposition
            out.append(entry)
        return out

    def _narrator_leads(self) -> list[dict[str, str]]:
        """The live leads, stated as facts for the prompt (§1)."""
        note = LEAD_NOTES.get(self.state.lead_stage)
        if not note:
            return []
        leads = [{"title": "Missing Travelers", "status": note}]
        if self.state.lead_stage in ("investigating", "solved") or "lanterns" in self.state.clues:
            leads.append({
                "title": MONASTERY_LIGHTS,
                "status": "lanterns move through the trees to the monastery bells",
            })
        return leads

    def _diminishing_brief(self, text: str) -> BeatBrief:
        """The brief for a reply on ground the player has already walked (§7).

        One call, never two: the beat's own narration is not written and then
        thrown away (the P13 finding — a guarded repeat wasted a provider
        call). The facts are the engine's (this is a repeat, nothing here
        changed, these are the open moves); the words are the model's, written
        fresh every time, so no two diminishing replies are one wall.
        """
        options = possible_moves(self.state)
        names = " · ".join(m["label"] for m in options[:3]) or "the road ahead"
        return BeatBrief(
            event="scene.diminished",
            facts=(
                f"the player repeats an action this scene has already answered: {text}",
                ("nothing new comes of it — the room, the road and the people in it "
                "are unchanged by the attempt"),
                f"still open here: {names}",
            ),
            direction=(
                "answer in one or two short sentences; do not re-narrate the scene "
                "and do not repeat what this action was told the first time; close "
                "by pointing at what is still open"
            ),
        )

    def _diminish(self, outcome: BeatOutcome, text: str) -> None:
        """Answer ground already walked briefly, in the narrator's own words.

        Everything the beat actually did to the state stands: only the room's
        voice changes, and it changes every time.
        """
        outcome.ack = "Familiar ground."
        outcome.brief = self._diminishing_brief(text)
        outcome.dialogue = []
        outcome.suggestions = possible_moves(self.state)

    # -- beats ---------------------------------------------------------------
    def _beat_spend_inspiration(self, text: str) -> BeatOutcome:
        """Burn one Inspiration: the next surfaced check throws twice.

        The counters and the ✦ system line are interface; the beat's own words
        are the model's like every other beat's (P14).
        """
        st = self.state
        if st.inspired:
            return BeatOutcome(
                ack="Your inspiration is already burning.",
                kind="",
                brief=BeatBrief(
                    event="inspiration.already",
                    facts=(
                        "the player tries to spend inspiration, but a spent point is already burning",
                        "the next surfaced roll already throws twice — nothing is spent now",
                    ),
                    direction="a held breath, not an event; nobody speaks",
                ),
            )
        if int(st.inspiration or 0) <= 0:
            return BeatOutcome(
                ack="No inspiration to spend — yet.",
                kind="",
                brief=BeatBrief(
                    event="inspiration.none",
                    facts=(
                        "the player reaches for inspiration, but the table has granted none yet",
                        ("inspiration is earned by story-driving beats — a clue, a lead, "
                        "a warmer heart — not by asking for it"),
                    ),
                    direction="keep it light; the fiction does not move",
                ),
            )
        st.inspiration = int(st.inspiration) - 1
        st.inspired = True
        self._feed("system", text=(
            f"✦ Inspiration spent ({st.inspiration} banked) — the next roll throws twice."
        ))
        return BeatOutcome(
            ack="✦ Inspiration burns.",
            kind="",
            brief=BeatBrief(
                event="inspiration.spend",
                facts=(
                    f"the player spends one inspiration — {st.inspiration} still banked",
                    ("the next surfaced roll will throw twice and keep the higher: the "
                    "table is watching, and the tale itself does not change here"),
                ),
                direction="a moment of resolve, not an event; nobody speaks",
            ),
        )

    def _beat_talk(self, text: str) -> BeatOutcome:
        if self.state.scene == UPSTAIRS:
            return self._talk_upstairs(text)
        st = self.state
        st.note("talked:travelers")
        st.advance_minutes(5)
        out = BeatOutcome(ack="Marla sets the cup down.", kind="talk")
        if st.lead_stage == "unheard":
            out.brief = BeatBrief(
                event="talk.first",
                facts=(
                    "the player asks Marla about the road and the missing travelers",
                    ("Marla answers: two guests bound for the Old Monastery walked out "
                    "into that rain three nights back and never came down again"),
                    ("the watch says travelers wander off; she says travelers do not "
                    "leave their packs behind"),
                    ("she asks the player to find out what happened to them, and offers "
                    "to keep their tab open here and their lamp lit while they do"),
                ),
                speakers=("Marla Voss",),
                direction=(
                    "she takes her time answering — the question is worth marking; the "
                    "room is hers and the rain keeps on outside"
                ),
                length="Standard",
            )
            self._discover_lead(out)
            self._remember(
                "marla", "asked about the travelers who never came back",
                kind="conversation", delta=+5,
                reason="asked about the travelers who never came back",
            )
            # Kindness of attention: the room's warmth is a mood, not just a meter.
            self._mood("marla", "warm", 0.5)
        elif st.lead_stage == "rumored":
            self._advance_to("accepted")
            out.brief = BeatBrief(
                event="talk.accepted",
                facts=(
                    "Marla accepts the player's word to look into the missing travelers",
                    "she asks them to find out what happened and bring her word — or better",
                    ("she adds the invitation: the rain is keeping the room cold, and if "
                    "the player wants the rest of it away from the bar they may come up "
                    "when she banks the fire — the stair is hers, and tonight she is "
                    "choosing to lend it"),
                ),
                speakers=("Marla Voss",),
                direction="a promise is about to be made; the room goes quiet around it",
                length="Standard",
            )
            out.suggestions = [{"label": "Take the stairs with her",
                                "command": "I follow Marla upstairs"}]
            self._remember(
                "marla", "promised to look into the missing travelers",
                kind="promise", sentiment=1, salience=3, delta=+10,
                reason="promised to look into the missing travelers",
            )
            self._mood("marla", "warm", 0.7)
        elif st.lead_stage == "investigating":
            out.brief = BeatBrief(
                event="talk.investigating",
                facts=(
                    "the player asks Marla about the road again",
                    ("she has noticed the player asking around: word travels ahead of "
                    "them and half the market has seen them at it"),
                    ("she does not ask them to stop — from Marla that is closer to a "
                    "request to finish"),
                ),
                speakers=("Marla Voss",),
            )
        elif st.lead_stage == "solved":
            out.brief = BeatBrief(
                event="talk.solved",
                facts=(
                    ("the travelers are home and the tale is told; the player asks Marla "
                    "about the road one last time"),
                    ("she pours two cups — one for the player, one set opposite, for the "
                    "tale that freed her ledger of ghosts"),
                    "the inn will hear it all by morning",
                ),
                speakers=("Marla Voss",),
            )
        else:
            out.brief = BeatBrief(
                event="talk.repeat",
                facts=(
                    (f"the player asks Marla about the road again — the {self._seen('talk') + 1}th "
                    "time in this scene"),
                    ("the road has not changed since they last asked and she has no new "
                    "answer; her patience for it is thin but not unkind"),
                    ("she nods at the ledger under the bar: whatever the player needs to "
                    "know, it will answer to paper sooner than to words"),
                ),
                speakers=("Marla Voss",),
            )
        return out

    # -- micro-scenes: the inn's upstairs room (§7) ---------------------------
    def _beat_upstairs(self, text: str) -> BeatOutcome:
        """Enter the upstairs room — a scene, not a map move (§7).

        The stair is Marla's to lend. Before she has told you anything the
        fiction answers with a refusal (not a wall — asking her opens it); once
        she has, going up is a transition the anti-loop guard never blocks.
        """
        st = self.state
        st.advance_minutes(5)
        if st.scene == UPSTAIRS:
            return BeatOutcome(
                ack="You are already upstairs.",
                kind="",
                brief=BeatBrief(
                    event="upstairs.already",
                    facts=(
                        "the player is already in Marla's room above the common room",
                        ("the inn's noise is below their feet; nothing changes by trying "
                        "to go up again"),
                    ),
                ),
            )
        if st.location != "lantern-inn":
            return BeatOutcome(
                ack="No stairs lead up from here.",
                kind="",
                brief=BeatBrief(
                    event="upstairs.elsewhere",
                    facts=(
                        "the player looks for Marla's stair, but they are not in the Lantern",
                        "that way up lives in the inn's common room, and it is hers to lend",
                    ),
                ),
            )
        if not invitation_open(st):
            return BeatOutcome(
                ack="Marla's hand finds the stair rail.",
                kind="",
                brief=BeatBrief(
                    event="upstairs.refused",
                    facts=(
                        "the player tries to go up to Marla's room",
                        ("Marla stops them: the stair is hers to lend and she has not "
                        "lent it — not yet, and not to a stranger with nothing said between them"),
                        ("asking her about the road and the missing travelers is what "
                        "would open it"),
                    ),
                    speakers=("Marla Voss",),
                ),
            )
        director = SceneDirector(st)
        move = director.enter(
            UPSTAIRS,
            label=UPSTAIRS_LABEL,
            location="lantern-inn",
            parent="lantern-inn",
            goal=UPSTAIRS_GOAL,
        )
        if move.first_visit:
            brief = BeatBrief(
                event="upstairs.first",
                facts=(
                    ("the player takes Marla's stair up into her room above the common "
                    "room — a scene of its own inside the inn"),
                    ("the room: low-browed and warm, a chair turned to a small fire, a "
                    "window holding the whole wet street, and a bed with the same neat "
                    "corners she keeps in her ledger"),
                    ("Marla tells the player to sit: below stairs she is the Lantern and "
                    "every soul is owed her face; up here she is only Marla, and they "
                    "should ask what they climbed up to ask"),
                ),
                speakers=("Marla Voss",),
                length="Standard",
            )
        elif director.ended(UPSTAIRS):
            brief = BeatBrief(
                event="upstairs.aftermath",
                facts=(
                    ("the player returns to the room where Marla said what she would not "
                    "say at the bar"),
                    ("the thing she brought them up to say has been said; the room is "
                    "quieter than memory and nothing here is waiting on the player now"),
                ),
            )
        else:
            brief = BeatBrief(
                event="upstairs.return",
                facts=(
                    ("the player comes back up to Marla's room — the conversation here "
                    "was left unfinished"),
                    "the room takes them back exactly as they left it, and she is there",
                ),
            )
        return BeatOutcome(
            ack="You take the stairs.",
            kind="scene",
            brief=brief,
            suggestions=possible_moves(st),
        )

    def _beat_downstairs(self, text: str) -> BeatOutcome:
        """Leave the micro-scene: it resolves back to its parent scene (§7)."""
        st = self.state
        st.advance_minutes(5)
        if st.scene != UPSTAIRS:
            return BeatOutcome(
                ack="You are already down.",
                kind="",
                brief=BeatBrief(
                    event="downstairs.already",
                    facts=(
                        ("the player is already in the common room — down is exactly "
                        "where they stand"),
                    ),
                ),
            )
        SceneDirector(st).enter("lantern-inn")
        return BeatOutcome(
            ack="You take the stairs down.",
            kind="scene",
            brief=BeatBrief(
                event="downstairs",
                facts=(
                    ("the player takes the stairs back down from Marla's room into "
                    "lamplight and the smell of wet peat"),
                    ("the common room gathers them up as if they had only stepped out "
                    "for a moment — the same low fire, the same cups"),
                ),
            ),
            suggestions=possible_moves(st),
        )

    def _talk_upstairs(self, text: str) -> BeatOutcome:
        """Marla's private word — the upstairs scene's goal (§7).

        The room exists for this: what she will not say at the bar. Telling it
        resolves the scene, so the room is remembered as finished and a return
        visit finds the aftermath, never the opening again.
        """
        st = self.state
        st.advance_minutes(10)
        director = SceneDirector(st)
        if director.resolve_current():
            self._remember(
                "marla", "told you, behind a closed door, what the road took from her",
                kind="conversation", sentiment=1, salience=3, delta=+5,
                reason="told you what she would not say at the bar",
            )
            self._mood("marla", "warm", 0.6)
            return BeatOutcome(
                ack="Marla says it once, and plainly.",
                kind="scene",
                brief=BeatBrief(
                    event="upstairs.word",
                    facts=(
                        "the player asks Marla what she brought them up here to say",
                        ("she answers flatly and once: she came north with the caravan "
                        "that burned — she was the one who walked out of that fire, and "
                        "she has been paying the road back in beds and soup ever since"),
                        ("she says that is the whole of what she has, and that she "
                        "wanted it said in a room with a door on it"),
                        ("what she needs: the road to stop taking — fourteen years behind "
                        "the bar, and these are the first names she has not been able "
                        "to drink away"),
                    ),
                    speakers=("Marla Voss",),
                    length="Standard",
                ),
                suggestions=possible_moves(st),
            )
        return BeatOutcome(
            ack="The room has said what it had.",
            kind="talk",
            brief=BeatBrief(
                event="upstairs.word.again",
                facts=(
                    ("the player asks again, but what Marla would say in this room has "
                    "already been said once"),
                    "she will not repeat it; the room keeps what it is given",
                ),
                speakers=("Marla Voss",),
            ),
            suggestions=possible_moves(st),
        )

    def _beat_inspect_board(self, text: str) -> BeatOutcome:
        st = self.state
        st.note("inspected:notice-board")
        st.advance_minutes(5)
        first = st.lead_stage == "unheard"
        seen = self._seen("inspect_board")
        if first:
            facts = (
                "the player reads the inn's notice board",
                ("its newest parchment is a plea in three hands: two travelers bound "
                "for the Old Monastery are missing on the Northern Road"),
                ("someone has underscored the word 'again'; the rain has got at the "
                "edges, but the names are clear enough"),
            )
        elif seen <= 1:
            facts = (
                "the player reads the notice board again",
                ("the plea still hangs there, rain-curled at the corners — two names, "
                "one road, no answers"),
                ("somebody has since pinned a charcoal sketch of the missing wagon "
                "below it"),
            )
        else:
            facts = (
                "the player reads the notice board yet again",
                ("the plea is where they left it: two names, one road, and the charcoal "
                "wagon sketch nobody has taken down"),
                ("the rain has turned its corners soft and the names have not changed "
                "at all"),
            )
        out = BeatOutcome(
            ack="You read the notice board.",
            kind="inspect",
            brief=BeatBrief(event="inspect.board", facts=facts),
        )
        self._discover_lead(out)
        return out

    def _beat_inspect_ledger(self, text: str) -> BeatOutcome:
        st = self.state
        st.note("inspected:marlas-ledger")
        st.advance_minutes(10)
        mech, result = self._check("investigation", "Moderate", "Investigation — Marla's guest ledger")
        seen = self._seen("inspect_ledger")
        if seen <= 0:
            facts = (
                ("the player turns the guest ledger open behind the bar — Marla counts "
                "her stock nightly, and tonight she has counted it twice; the pages "
                "are damp at the edges"),
            )
        else:
            facts = (
                ("the player goes back through the guest ledger — pages they have "
                "already bothered once, damp at the edges and patient as a creditor"),
                "the damp has not moved and neither have the names",
            )
        if self._succeeded(result):
            facts = facts + (
                ("read this far in: two signatures for the Old Monastery road, three "
                "nights apart, and no line drawn through either — they never signed out"),
                "in the margin, one small hand has pencilled the same word twice: 'again'",
            )
        else:
            facts = facts + (
                ("the damp has won the last few pages — the ink runs together into "
                "dark smears"),
                ("boots and a guild mark can just be made out; nothing certain comes "
                "of the reading"),
            )
        out = BeatOutcome(
            ack="You turn the ledger pages.",
            kind="inspect",
            brief=BeatBrief(event="inspect.ledger", facts=facts),
            mechanics=mech,
        )
        self._remember(
            "marla", "went through her guest ledger page by page",
            kind="curiosity", delta=-5, reason="went through her guest ledger page by page",
        )
        # Prying where she can see it leaves her watching the room harder.
        self._mood("marla", "suspicious", 0.4)
        if self._succeeded(result):
            if st.find_clue("ledger"):
                self._feed("system", text="❧ Clue found — the ledger's unsigned guests.")
            self._discover_lead(out)
            self._advance_to("investigating")
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
            self._mood("marla", "suspicious", 0.5)
        seen = self._seen("inspect_cellar")
        if st.travelers_freed:
            facts = (
                ("the player looks at the inn's cellar door: it stands open now, "
                "hooked back against the wall"),
                ("cold air climbs the steps from the inn's deep stores — barrels, "
                "salt, and the shape of the story that used to live down here"),
                "nobody down there is waiting on the player anymore",
            )
        elif st.lead_stage in ("accepted", "investigating"):
            facts = (
                ("the player looks at the inn's cellar door: barred as always, but the "
                "bar has been lifted recently"),
                ("the dust on its bracket is disturbed, and a thin woman's scarf — "
                "grey, and not Marla's — is caught on the latch"),
                ("behind the inn's own stores the passage keeps going down; opening it "
                "would want a reason Marla would accept"),
            )
        else:
            facts = (
                ("the player looks at the inn's cellar door: shut fast, barred and "
                "padlocked, the way it has been since they were small enough to be "
                "scared of it"),
                ("the old gripes say that one step past the stores and the Lantern "
                "becomes a warren; the door has never once confirmed it"),
            )
        if seen >= 1:
            facts = facts + (
                ("the player has looked at this door before in this scene; nothing "
                "about it has changed"),
            )
        return BeatOutcome(
            ack="You consider the cellar door.",
            kind="inspect",
            brief=BeatBrief(event="inspect.cellar", facts=facts),
        )

    def _beat_inspect(self, text: str) -> BeatOutcome:
        st = self.state
        target = "the room"
        for word in ("hearth", "fire", "window", "bar", "tables", "tankards", "bottles", "door"):
            if word in text.lower():
                target = word
                break
        st.note(f"inspected:{target}")
        st.advance_minutes(5)
        seen = target if target.startswith("the ") else f"the {target}"
        seen_count = self._seen("inspect")
        facts = (
            f"the player gives {seen} the traveler's once-over, where they stand",
        )
        if seen_count <= 0:
            facts = facts + (
                "it is as it was: nothing here has moved for the player's looking at it",
            )
        else:
            facts = facts + (
                (f"the player has gone over this ground before in this scene "
                f"({seen_count + 1} times now): same wear, same dust, same patience — "
                "if the place is holding anything back it is holding it well"),
            )
        return BeatOutcome(
            ack="You look closer.",
            kind="inspect",
            brief=BeatBrief(event="inspect.room", facts=facts),
        )

    def _beat_steal(self, text: str) -> BeatOutcome:
        st = self.state
        st.advance_minutes(15)
        mech, result = self._check("stealth", "Moderate", "Stealth — the storeroom strongbox")
        if self._succeeded(result):
            st.silver += 14
            st.note("stole:storeroom-strongbox")
            if result.outcome == Outcome.SuccessWithCost:
                st.note("saw:sneaking")
                facts = (
                    ("the player gets the storeroom latch open and takes a pouch of "
                    "guilders — fourteen, heavier than an innkeeper's till has any "
                    "right to be in one night"),
                    ("on the way out a floorboard speaks under their heel: one loud note "
                    "in the rain-soft house, and Marla says nothing as the door eases shut"),
                    "she will remember this even if the watch can never prove it",
                )
                self._remember(
                    "marla", "was robbed at the storeroom — and glimpsed who did it",
                    kind="theft", sentiment=-2, salience=4, delta=-30,
                    reason="robbed the storeroom strongbox — and was glimpsed doing it",
                )
                self._mood("marla", "suspicious", 0.8)
            else:
                facts = (
                    ("the player gets the storeroom latch open with a practiced nudge "
                    "and takes a pouch of guilders — fourteen — from the strongbox"),
                    ("Marla counts her stock nightly and tonight she will count it again; "
                    "the road eats coin as fast as it eats travelers"),
                )
                self._remember(
                    "marla", "was robbed — the storeroom strongbox came up light",
                    kind="theft", sentiment=-2, salience=4, delta=-25,
                    reason="robbed the storeroom strongbox",
                )
                self._mood("marla", "suspicious", 0.5)
        elif result.outcome == Outcome.CriticalFailure:
            st.note("saw:sneaking")
            facts = (
                ("the strongbox is not locked — it is a decoy, and decoys ring: the "
                "little bell inside jumps once"),
                "Marla is in the doorway before the player's hand clears the lid",
                ("she looks at them the way a ledger looks at a debt and says nothing "
                "at all; that silence will cost more than any fine"),
            )
            self._remember(
                "marla", "caught them red-handed at the storeroom strongbox",
                kind="theft", sentiment=-2, salience=5, delta=-35,
                reason="caught red-handed at the storeroom strongbox",
            )
            self._mood("marla", "angry", 0.8)
        else:
            st.note("heard:noise")
            facts = (
                ("the storeroom latch holds fast to its frame, and the second try "
                "costs the player a scrape across the knuckles"),
                ("they gather themselves in the dark storeroom and decide the strongbox "
                "can wait for better trade"),
            )
            self._remember(
                "marla", "heard a suspicious clatter by the storeroom",
                kind="suspicion", sentiment=-1, salience=2, delta=-5,
                reason="made a suspicious clatter by the storeroom",
            )
            self._mood("marla", "suspicious", 0.5)
        return BeatOutcome(
            ack="Your hand finds the storeroom latch.",
            kind="steal",
            brief=BeatBrief(event="steal", facts=facts),
            mechanics=mech,
        )

    def _beat_fight(self, text: str) -> BeatOutcome:
        st = self.state
        st.advance_minutes(10)
        mech, result = self._check("swordsmanship", "Moderate", "Swordsmanship — Borin the drunk")
        pc = st.pc
        hp = pc.get("hp", {"cur": 10, "max": 10})
        scene_fact = (
            "the fight is by the inn's hearth, the room watching and the fire hissing",
        )
        if result.outcome == Outcome.Exceptional:
            st.borin_down = True
            st.note("fought:drunk-mercenary")
            hp["cur"] = max(1, hp["cur"] - 1)
            facts = scene_fact + (
                ("Borin swings first, the way he always does, and that is the last "
                "choice the fight lets him make: the player puts him down flat beside "
                "the hearth"),
                ("Marla hauls him none too gently out to the porch, and remembers — in "
                "writing and otherwise — exactly who started it and who finished it"),
            )
        elif self._succeeded(result):
            st.borin_down = True
            st.note("fought:drunk-mercenary")
            cost = result.outcome == Outcome.SuccessWithCost
            hp["cur"] = max(1, hp["cur"] - (6 if cost else 3))
            if cost:
                facts = scene_fact + (
                    ("the player wins, but it is a close, ugly thing: a chair dies, a "
                    "ridge of the hearth rakes their ribs, and Borin goes down wearing "
                    "his own apology in a split lip"),
                    "Marla's look could stop the rain; the porch claims Borin for the night",
                )
            else:
                facts = scene_fact + (
                    ("the player wins it: tankards scatter, the fire hisses, and Borin "
                    "goes down flat beside the hearth"),
                    "Marla remembers exactly who started it and who finished it",
                )
        elif result.outcome == Outcome.CriticalFailure:
            st.note("fought:drunk-mercenary")
            hp["cur"] = max(1, hp["cur"] - 9)
            if "Bruised" not in pc.setdefault("conditions", []):
                pc["conditions"].append("Bruised")
            facts = scene_fact + (
                ("Borin catches the player flush with the flat of a tankard and the "
                "floor introduces itself"),
                ("Marla breaks it apart with a cudgel from under the bar and a voice "
                "the player has never heard her use"),
                ("the player wakes on the settle with their ribs in bandages and Borin "
                "snoring on the porch — and every soul in the inn now knows their "
                "business"),
            )
        else:
            st.note("fought:drunk-mercenary")
            hp["cur"] = max(1, hp["cur"] - 6)
            facts = scene_fact + (
                ("Borin gets inside the player's arm, the room tilts, and tonight the "
                "player loses"),
                ("Marla's voice cuts the brawl apart before it becomes a hanging "
                "matter; the player comes up with a split brow and an education"),
            )
        pc["hp"] = hp
        self._remember(
            "marla",
            "started a brawl by her hearth" + (" — and won it" if st.borin_down else ""),
            kind="violence", sentiment=-1, salience=3, delta=-15,
            reason="started a brawl by her hearth",
        )
        # A brawl by her hearth angers her; the loser stews, the winner smirks.
        self._mood("marla", "angry", 0.7)
        if st.borin_down:
            self._remember(
                "borin", "was knocked down in a brawl by the fire",
                kind="violence", sentiment=-1, salience=2, delta=-15,
                reason="was knocked down in a brawl by the fire",
            )
            self._mood("borin", "angry", 0.8)
        else:
            self._remember(
                "borin", "got the better of them in the brawl by the fire",
                kind="violence", sentiment=-1, salience=2, delta=-5,
                reason="brawled with them by the fire",
            )
            self._mood("borin", "amused", 0.6)
        return BeatOutcome(
            ack="The hearthlight swings as the fight starts.",
            kind="fight",
            brief=BeatBrief(event="fight", facts=facts),
            mechanics=mech,
        )

    def _return_brief(self) -> BeatBrief:
        """Coming back in: the arrival, plus what Marla holds of the player."""
        return BeatBrief(
            event="return.inn",
            facts=(
                ("the player comes back in out of the rain to the Lantern Inn — "
                "firelight, the smell of wet peat, the door finding its latch"),
                ("Marla looks up from the bar in the small silence that always follows "
                "an arrival"),
            ) + self._marla_remembers(),
            speakers=("Marla Voss",),
        )

    def _beat_leave(self, text: str) -> BeatOutcome:
        st = self.state
        if st.location == "northern-road":
            return BeatOutcome(
                ack="You are already on the road.",
                kind="travel",
                brief=BeatBrief(
                    event="leave.already",
                    facts=(
                        ("the player is already out on the Northern Road, in its long "
                        "grey corridor of rain"),
                        ("behind them the Lantern's light; ahead, the dark that keeps "
                        "its books badly"),
                    ),
                ),
            )
        if st.location == PROLOGUE:
            # From the prologue's ridge "leave" means walking on into the dark
            # past the town — the road's usual arrival would misread here.
            st.location = "northern-road"
            st.visit_location("northern-road")
            st.advance_minutes(10)
            SceneDirector(st).enter("northern-road")
            return BeatOutcome(
                ack="You give the town's light your back.",
                kind="travel",
                brief=BeatBrief(
                    event="prologue.north",
                    facts=(
                        ("the player walks on north past the last lamp, and Ravenford "
                        "gathers itself behind them — roofs, warmth, the inn's one light"),
                        ("ahead, the Northern Road takes the rain quietly, as it takes "
                        "everything quietly"),
                    ),
                ),
            )
        st.location = "northern-road"
        st.visit_location("northern-road")
        st.advance_minutes(10)
        # The map moves and the scene moves with it (§7, trigger a).
        SceneDirector(st).enter("northern-road")
        return BeatOutcome(
            ack="You step out into the rain.",
            kind="travel",
            brief=BeatBrief(
                event="leave.road",
                facts=(
                    ("the player shoulders their cloak and steps out of the Lantern — "
                    "the door lets in the whole wet breath of the night at once"),
                    ("behind them the inn's light shrinks to a coin; the Northern Road "
                    "takes them the way it takes every traveler, promising nothing but "
                    "the walking"),
                ),
            ),
        )

    def _beat_return(self, text: str) -> BeatOutcome:
        st = self.state
        st.location = "lantern-inn"
        st.visit_location("lantern-inn")
        st.visits += 1
        st.advance_minutes(10)
        # Coming back restores the inn's remembered scene — a sub-scene left
        # standing (the upstairs room) resolves back into it here.
        SceneDirector(st).enter("lantern-inn")
        return BeatOutcome(
            ack="The door gives way to firelight.",
            kind="travel",
            brief=self._return_brief(),
        )

    def _beat_enter_inn(self, text: str) -> BeatOutcome:
        """Arrive (§intro): the road lets go and the Lantern takes you in.

        Entering from the prologue is a scene transition like any other — the
        anti-loop never blocks it — and the inn's opening (the room, the
        welcome) plays exactly once, on this first visit. Reached from
        anywhere past the prologue it degrades to the usual return greeting.
        """
        st = self.state
        if st.location != PROLOGUE:
            return self._beat_return(text)
        st.advance_minutes(5)
        st.location = "lantern-inn"
        st.visit_location("lantern-inn")
        move = SceneDirector(st).enter("lantern-inn")
        if move.first_visit:
            brief = BeatBrief(
                event="inn.first",
                facts=(
                    ("the player takes the last stretch down into Ravenford and comes "
                    "in out of the rain: the door gives, and the night lets go of them "
                    "all at once"),
                    ("the room, as the chronicle first finds it: rain needling the "
                    "shutters, the hearth throwing long shadows across the notice board, "
                    "where one parchment hangs newer than the rest — a plea about "
                    "travelers who never came back down the Northern Road"),
                    ("Marla welcomes them in: come in from the rain, the fire is warm "
                    "and the road is bad; sit where she can see them, and questions "
                    "come cheaper than silver here"),
                ),
                speakers=("Marla Voss",),
                length="Standard",
            )
        else:
            brief = self._return_brief()
        return BeatOutcome(
            ack="You take the last stretch down into Ravenford.",
            kind="travel",
            brief=brief,
            suggestions=possible_moves(st),
        )

    def _beat_prologue_look(self, text: str) -> BeatOutcome:
        """The prologue's view: quiet, and it never moves the player (§intro)."""
        self.state.advance_minutes(2)
        facts = (
            "the player looks out over the valley from the ridge, in the rain",
            ("Ravenford from here: rooftops descending to the river like ledger "
            "columns, the old bridge holding its arch against the current, and a "
            "single lantern burning on the one street that matters — the inn's"),
            "past the last houses the Northern Road runs out into the dark",
        )
        if self._seen("prologue_look"):
            facts = facts + (
                ("this is a second look: nothing below has changed for the player's "
                "looking at it"),
            )
        return BeatOutcome(
            ack="You look out over the valley.",
            kind="",
            brief=BeatBrief(event="prologue.look", facts=facts),
            suggestions=possible_moves(self.state),
        )

    def _beat_prologue_listen(self, text: str) -> BeatOutcome:
        """The prologue's night, heard: quiet, and it never moves the player (§intro)."""
        self.state.advance_minutes(2)
        facts = (
            "the player stands still a moment and lets the night say what it has",
            ("rain on hedge and slate, the river's low argument under the bridge, a "
            "shutter working loose somewhere below"),
            ("under all of it, faint as coin under cloth, the murmur of the inn's "
            "common room: a fire, voices, a door worth opening"),
        )
        if self._seen("prologue_listen"):
            facts = facts + (
                "this is a second hearing: the same night, closer now than it was",
            )
        return BeatOutcome(
            ack="You hold still and listen.",
            kind="",
            brief=BeatBrief(event="prologue.listen", facts=facts),
            suggestions=possible_moves(self.state),
        )

    def _beat_rest(self, text: str) -> BeatOutcome:
        st = self.state
        st.advance_minutes(360)
        pc = st.pc
        for key in ("hp", "stamina"):
            bar = pc.get(key, {})
            bar["cur"] = bar.get("max", bar.get("cur", 10))
            pc[key] = bar
        healed = False
        if "Bruised" in pc.get("conditions", []) and pc["hp"]["cur"] >= pc["hp"]["max"]:
            pc["conditions"].remove("Bruised")
            healed = True
        # A long night kept the inn: whoever held the bar is up past their rest.
        self._mood("marla", "tired", 0.6)
        facts = [
            "the player takes a room at the inn and sleeps the night through",
            "they wake rested: vitality and stamina are back to full",
            ("the rain is still at the shutters and the road is still out there; the "
            "morning is theirs to spend"),
        ]
        if healed:
            facts.insert(2, "the Bruised condition has passed off")
        return BeatOutcome(
            ack="You take your rest.",
            kind="rest",
            brief=BeatBrief(event="rest", facts=tuple(facts)),
        )

    def _beat_travel_market(self, text: str) -> BeatOutcome:
        st = self.state
        st.location = "market"
        st.visit_location("market")
        st.advance_minutes(15)
        SceneDirector(st).enter("market")
        return BeatOutcome(
            ack="You take the market road.",
            kind="travel",
            brief=BeatBrief(
                event="travel.market",
                facts=(
                    ("the player walks to Ravenford's market on a bad morning in a good "
                    "coat: canvas snapping, gutters roaring, and guild silver changing "
                    "hands where the stalls stay dry"),
                    ("a factor in grey gloves watches the price of silver climb, and does "
                    "not bother to look away when the player catches her at it"),
                ),
            ),
        )

    def _beat_travel_monastery(self, text: str) -> BeatOutcome:
        st = self.state
        st.location = "old-monastery"
        st.visit_location("old-monastery")
        st.advance_minutes(25)
        SceneDirector(st).enter("old-monastery")
        return BeatOutcome(
            ack="You climb the monastery path.",
            kind="travel",
            brief=BeatBrief(
                event="travel.monastery",
                facts=(
                    ("the player climbs the path to the Old Monastery, standing above "
                    "the treeline like a promise someone broke a long time ago"),
                    ("rain floods the cart ruts on the path; the bells are silent in the "
                    "towers, but a lantern burns low at the porter's door"),
                ),
            ),
        )

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
                kind="inspect",
                brief=BeatBrief(
                    event="clue.tracks.elsewhere",
                    facts=(
                        "the player looks for tracks worth reading",
                        ("the ruts worth reading are out on the Northern Road; here, the "
                        "mud has better manners"),
                    ),
                ),
            )
        if st.lead_stage == "unheard":
            return BeatOutcome(
                ack="The mud is dumb.",
                kind="inspect",
                brief=BeatBrief(
                    event="clue.tracks.unheard",
                    facts=(
                        ("the player reads the wagon ruts, but without a story to follow "
                        "they are only mud"),
                        ("what the road has been swallowing is known under the Lantern's "
                        "roof; ask there first and the road will start to make sense"),
                    ),
                ),
            )
        mech, result = self._check("perception", "Moderate", "Perception — the wagon ruts")
        if self._succeeded(result):
            self._clue("tracks")
            self._advance_to("investigating")
            self._maybe_open_ledger_trail()
            facts = (
                ("the player kneels where the mud still holds its shape and reads it: "
                "two wheel-ruts leave the monastery road at the lightning-bent oak"),
                ("they cut away toward the deep forest, where no map of Ravenford "
                "admits a road at all"),
                ("guild-narrow wheels, double-laden by the look of the rills: whatever "
                "came back driverless, something heavier followed it in"),
            )
            ack = "You read the road."
        else:
            facts = (
                ("the rain has combed the last day's ruts to soup: carts came and went, "
                "that much the mud will swear to, but which way the last one turned it "
                "keeps to itself"),
                "the weather will hold long enough to come at it fresh",
            )
            ack = "The rain owns the road."
        return BeatOutcome(
            ack=ack, kind="inspect", brief=BeatBrief(event="clue.tracks", facts=facts),
            mechanics=mech,
        )

    def _beat_clue_lanterns(self, text: str) -> BeatOutcome:
        st = self.state
        st.advance_minutes(30)
        if st.location not in ("northern-road", "old-monastery"):
            return BeatOutcome(
                ack="You find a vantage.",
                kind="inspect",
                brief=BeatBrief(
                    event="clue.lanterns.elsewhere",
                    facts=(
                        "the player goes looking for the lights over the treeline",
                        ("they are watched from the road or the monastery path; from "
                        "here, every lantern is just a lantern"),
                    ),
                ),
            )
        if st.lead_stage == "unheard":
            return BeatOutcome(
                ack="The dark stays dark.",
                kind="inspect",
                brief=BeatBrief(
                    event="clue.lanterns.unheard",
                    facts=(
                        ("the player watches the treeline, but the dark is only dark and "
                        "dark is not yet evidence"),
                        ("learn first what the road has been swallowing, then watch what "
                        "the night carries — the two will start to rhyme"),
                    ),
                ),
            )
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
            facts = (
                "the player folds into a hollow or the woodline and waits",
                ("near the second bell, lights come swinging down through the trees: "
                "three lanterns, one route, carried low and slow with weight"),
                ("they pass near enough to smell of tallow and wet wool, and take the "
                "wide path east where the ridge folds — the mouth of the old supply "
                "tunnel"),
            )
            ack = "You keep still and count lanterns."
        else:
            facts = (
                ("the player waits through one bell and two, until the cold owns the "
                "hollow and the rain finds a way past their collar"),
                ("lanterns or fireflies, the trees keep their business to themselves "
                "tonight"),
            )
            ack = "The cold wins."
        return BeatOutcome(
            ack=ack, kind="inspect", brief=BeatBrief(event="clue.lanterns", facts=facts),
            mechanics=mech,
        )

    def _beat_persuade(self, text: str) -> BeatOutcome:
        st = self.state
        st.advance_minutes(10)
        if not _SELLA.search(text):
            return self._beat_talk_borin(text)
        if st.location != "market":
            return BeatOutcome(
                ack="You straighten your cuffs.",
                kind="talk",
                brief=BeatBrief(
                    event="persuade.sella.elsewhere",
                    facts=(
                        "the player prepares to make a case to Sella Voss",
                        ("Sella keeps to the guild's stall in the market; they would have "
                        "to go to her, and be ready to be seen doing it"),
                    ),
                ),
            )
        if st.lead_stage == "unheard":
            return BeatOutcome(
                ack="You would not know what to ask.",
                kind="talk",
                brief=BeatBrief(
                    event="persuade.sella.unheard",
                    facts=(
                        ("the player would press Sella Voss for answers, but does not yet "
                        "know enough to make anyone nervous"),
                        "learn what the road has been swallowing first",
                    ),
                ),
            )
        mech, result = self._check(
            "persuasion", "Difficult", "Persuasion — Sella Voss",
            npc="sella", approach=approach_for_skill("persuasion", text),
        )
        if self._succeeded(result):
            self._advance_to("investigating")
            self._unlock("talk-it-out")
            facts = (
                "the player makes their case quietly across the guild stall",
                ("it is not the plea that lands — it is Marla's name; Sella closes the "
                "ledger and looks up the road a long moment"),
                ("she tells them: the guild rents the old monastery cellar, the one the "
                "order calls sealed; if their two went up that road, they are under the "
                "hill"),
                ("she tells them to go before the bells ring again, and that they never "
                "heard it here"),
            )
        else:
            facts = (
                "the player makes their case quietly, and it does not land",
                ("Sella says it is guild business, not unkindly, and slides the ledger "
                "out from under their eyes — the kindness is real, and so is the ledger"),
            )
        return BeatOutcome(
            ack="You make your case quietly.",
            kind="talk",
            brief=BeatBrief(event="persuade.sella", facts=facts, speakers=("Sella Voss",)),
            mechanics=mech,
        )

    def _beat_intimidate(self, text: str) -> BeatOutcome:
        st = self.state
        st.advance_minutes(10)
        if _SELLA.search(text):
            if st.location != "market":
                return BeatOutcome(
                    ack="You set your jaw.",
                    kind="talk",
                    brief=BeatBrief(
                        event="intimidate.sella.elsewhere",
                        facts=(
                            "the player squares up to lean on Sella Voss",
                            ("they will have to go to the market for that — making a "
                            "guild factor nervous in her own stall is a thing done in "
                            "person"),
                        ),
                    ),
                )
            if st.lead_stage == "unheard":
                return BeatOutcome(
                    ack="You would not know where to press.",
                    kind="talk",
                    brief=BeatBrief(
                        event="intimidate.sella.unheard",
                        facts=(
                            ("the player would lean on Sella Voss, but threats without a "
                            "question behind them are just noise"),
                            "learn the story first, then choose who sweats",
                        ),
                    ),
                )
            mech, result = self._check(
                "intimidation", "Difficult", "Intimidation — Sella Voss",
                npc="sella", approach="pressure",
            )
            profile = profile_for("sella")
            if self._succeeded(result):
                self._advance_to("investigating")
                self._unlock("lean-on-them")
                facts = (
                    ("something in the player's stillness reaches Sella after all; her "
                    "own stillness breaks first and she turns a page she is not reading"),
                    ("she tells them: the cellar under the monastery, the old one — "
                    "guild rent, guild carriers, and their travelers still breathing, "
                    "because the guild wants the road worried about, not closed"),
                    "that is all they get",
                )
                # Pressure is not free (§6): she does as she is told, and the
                # fear is weighted by her pride — a grudge, not a lesson.
                self._remember(
                    "sella", "was made afraid, and did as she was told",
                    kind="intimidation", sentiment=-2, salience=3,
                    delta=profile.pressure.hit, reason="was made afraid",
                )
                self._mood("sella", "afraid", 0.6)
            else:
                backfire = profile.pressure.backfire
                line = "was threatened and did not bend"
                if result.outcome == Outcome.CriticalFailure:
                    backfire *= 2
                    line = "was threatened, and the guild heard about it"
                    self._feed("system", text="❧ The grey gloves have taken an interest.")
                facts = (
                    ("the player is three words into the hard voice when two "
                    "grey-gloved men find reasons to stand behind Sella's shoulders"),
                    "she smiles the whole way; nothing is said that a magistrate could use",
                )
                self._remember(
                    "sella", line, kind="intimidation", sentiment=-1, salience=2,
                    delta=backfire, reason=line,
                )
                self._mood("sella", "amused", 0.4)
            return BeatOutcome(
                ack="You lean into the space between you.",
                kind="talk",
                brief=BeatBrief(event="intimidate.sella", facts=facts, speakers=("Sella Voss",)),
                mechanics=mech,
            )

        # Borin: he can be cowed (slice NPC: "backs down if beaten or cowed").
        if st.location != "lantern-inn":
            return BeatOutcome(
                ack="Borin is not here.",
                kind="talk",
                brief=BeatBrief(
                    event="intimidate.borin.elsewhere",
                    facts=(
                        "the player looks for Borin to lean on him",
                        "wherever Borin is drinking tonight, it is not here",
                    ),
                ),
            )
        if st.borin_down:
            return BeatOutcome(
                ack="Borin avoids your eye.",
                kind="talk",
                brief=BeatBrief(
                    event="intimidate.borin.down",
                    facts=(
                        ("the player squares up to Borin again, but he has already been "
                        "beaten tonight and is in no hurry to be reacquainted"),
                        ("he mutters something about carts turning north of the oak and "
                        "finds his cup suddenly fascinating"),
                    ),
                    speakers=("Borin",),
                ),
            )
        mech, result = self._check(
            "intimidation", "Moderate", "Intimidation — Borin",
            npc="borin", approach="pressure",
        )
        profile = profile_for("borin")
        if self._succeeded(result):
            st.borin_down = True
            st.note("cowed:borin")
            self._remember(
                "borin", "was frightened into talking about the carts",
                kind="intimidation", sentiment=-2, salience=3,
                delta=profile.pressure.hit,  # authored on the profile (§5)
                reason="was frightened into talking about the carts",
            )
            self._mood("borin", "afraid", 0.7)
            facts = (
                ("the player does not put a hand on Borin — they do not need to: they "
                "lean in close enough that the fire's crackle cannot cover their voice"),
                ("the mercenary decides he has somewhere else to be, and mutters one "
                "thing on his way out: carts, north of the oak — that is all the player "
                "gets from him"),
            )
            ack = "You lean in close."
        else:
            # A failed attempt is not free either (§6): he remembers who pushed.
            backfire = profile.pressure.backfire
            line = "was leaned on and did not blink"
            if result.outcome == Outcome.CriticalFailure:
                backfire *= 2
                line = "was leaned on badly, and the story of it got around"
            self._remember(
                "borin", line, kind="intimidation", sentiment=-1, salience=2,
                delta=backfire, reason=line,
            )
            self._mood("borin", "amused", 0.4)
            facts = (
                ("the player leans on Borin, and he has been intimidated by "
                "professionals — tonight the player is not one of them"),
                "he grins into his cup and stays exactly where he is",
            )
            ack = "You test the room."
        return BeatOutcome(
            ack=ack, kind="talk",
            brief=BeatBrief(event="intimidate.borin", facts=facts, speakers=("Borin",)),
            mechanics=mech,
        )

    def _beat_ambush(self, text: str) -> BeatOutcome:
        st = self.state
        st.advance_minutes(20)
        if st.location != "northern-road":
            return BeatOutcome(
                ack="You pick your ground.",
                kind="inspect",
                brief=BeatBrief(
                    event="ambush.elsewhere",
                    facts=(
                        ("the player picks ground for an ambush, but ambushes happen on "
                        "the road and they are not on it"),
                        "the carriers come and go on the Northern Road after dark",
                    ),
                ),
            )
        if st.lead_stage != "investigating":
            return BeatOutcome(
                ack="The road is just road.",
                kind="inspect",
                brief=BeatBrief(
                    event="ambush.early",
                    facts=(
                        ("the player could wait out here, but does not yet know what is "
                        "worth lying in wait for"),
                        ("follow the threads first — the north road is where they will end "
                        "up crossing"),
                    ),
                ),
            )
        mech, result = self._check("swordsmanship", "Difficult", "Swordsmanship — the road carriers")
        if self._succeeded(result):
            st.note("fought:road-carriers")
            self._unlock("blades-out")
            facts = (
                ("they come up the road late with hooded lanterns and a hand-cart: Fenn "
                "in front, a caravan guard twice the player's width behind"),
                ("the fight is short and muddy and ends with the guard sitting in the "
                "ditch reconsidering his career; the player wins it"),
                ("Fenn talks, because Fenn always talks: a cellar under the old "
                "monastery, guild rent, and two travelers kept alive because a closed "
                "road earns the guild more than an empty one"),
                "the tunnel mouth, he says, is east where the ridge folds",
            )
            ack = "Lanterns, then shouting."
        else:
            pc = st.pc
            hp = pc.get("hp", {"cur": 10, "max": 10})
            hp["cur"] = max(1, hp["cur"] - (9 if result.outcome == Outcome.CriticalFailure else 6))
            pc["hp"] = hp
            st.note("fought:road-carriers")
            st.location = "lantern-inn"
            # Waking back at the inn is a transition too (§7, trigger a).
            SceneDirector(st).enter("lantern-inn")
            facts = (
                ("the road is bad at this hour and worse at ambushes; the player learns "
                "that with their ribs and loses it"),
                ("they wake at the Lantern Inn with linen bound tight and a headache in "
                "their teeth"),
                ("Marla says nothing about it, which is the loudest thing she has ever "
                "said to them; the road keeps its carriers — for now"),
            )
            ack = "The mud gets its say."
        return BeatOutcome(
            ack=ack, kind="fight", brief=BeatBrief(event="ambush", facts=facts),
            mechanics=mech,
        )

    def _beat_talk_borin(self, text: str) -> BeatOutcome:
        st = self.state
        st.note("talked:borin")
        first_ask = st.remember("borin", "was asked about carts north of the oak", kind="conversation")
        st.advance_minutes(5)
        if st.location != "lantern-inn":
            return BeatOutcome(
                ack="You look for Borin.",
                kind="talk",
                brief=BeatBrief(
                    event="talk.borin.elsewhere",
                    facts=(
                        "the player looks for Borin to ask him about the carts",
                        "wherever Borin is drinking tonight, it is not here",
                    ),
                ),
            )
        if first_ask:
            # Only a real conversation — Borin actually present — moves his meter.
            st.adjust_attitude("borin", +5, "asked about carts north of the oak")
            self._mood("borin", "amused", 0.4)
        if st.borin_down:
            return BeatOutcome(
                ack="Borin eyes you over his bruises.",
                kind="talk",
                brief=BeatBrief(
                    event="talk.borin.down",
                    facts=(
                        ("the player asks Borin about the carts again, but he is on the "
                        "porch reconsidering his choices and his jaw"),
                        ("he does not have a lot to say to them — he does, however, still "
                        "have the grudge, which is halfway to a rumour"),
                        ("what he does say is the same thing again: carts, north of the "
                        "oak; broken ribs and all, that is all they get from him"),
                    ),
                    speakers=("Borin",),
                ),
            )
        return BeatOutcome(
            ack="Borin warms to his theme.",
            kind="talk",
            brief=BeatBrief(
                event="talk.borin",
                facts=(
                    "the player asks Borin about carts and the road",
                    ("he does not look up from his cup, but for Borin the not-looking is "
                    "practically confidence — the way of a man deciding how much of a "
                    "secret he can afford to be careless with"),
                    ("what he says: carts turn north of the oak, nights, since Midwinter; "
                    "ask the woman with the scales why the guild pays over rate for "
                    "silver, then ask her where the carts come back from — and they did "
                    "not hear it here"),
                ),
                speakers=("Borin",),
            ),
        )

    # -- flirt: romantic interest as a mood, never a world fact --------------
    def _named_present(self, text: str, *, fallback: bool = True) -> str | None:
        """The present character the text names — else, by default, the first present.

        Nobody present at this location means nobody answers to it at all; a
        beat that needs a *named* recipient asks for ``fallback=False``.
        """
        present = [npc_slug(n["name"]) for n in npcs_present(self.state)]
        if not present:
            return None
        return named_npc(text, present) or (present[0] if fallback else None)

    _flirt_target = _named_present

    def _beat_flirt(self, text: str) -> BeatOutcome:
        st = self.state
        st.advance_minutes(5)
        target = self._flirt_target(text)
        if target is None:
            return BeatOutcome(
                ack="Your charm finds no purchase.",
                kind="talk",
                brief=BeatBrief(
                    event="flirt.empty",
                    facts=(
                        ("the player makes their interest plain, but there is nobody here "
                        "to catch their eye"),
                        "the road keeps its own company tonight, and charm needs a witness",
                    ),
                ),
            )
        self._remember(
            target, "was flirted with, and took their time about answering",
            kind="flirt", delta=+3, reason="flirted with them",
        )
        # Gated at set (_mood consults the campaign's content settings) and
        # again on every surface (chip + narrator prompt), so one settings
        # flip re-gates every surface at once.
        self._mood(target, "flirty", 0.6)
        name = display_name(target)
        return BeatOutcome(
            ack="You make your interest plain.",
            kind="talk",
            brief=BeatBrief(
                event="flirt",
                facts=(
                    (f"the player leans in on {name} with a slow, unhurried interest and "
                    "lets it sit there — no hurry, no hiding the ask"),
                    (f"{name} holds their look a moment longer than politeness runs and "
                    "gives them nothing back but the room going quiet around the two of "
                    "them"),
                ),
                speakers=(name,),
            ),
        )

    # -- leverage: a need named in coin (systems slice 4, §6) ----------------
    def _offer_target(self, text: str) -> str | None:
        """Who an offer lands on: the one named, else the first one present.

        An offer names its recipient — paying for a peddler's word at the inn
        is offering coin to nobody.
        """
        return self._named_present(text, fallback=False)

    def _leverage_state(self, target: str) -> None:
        """The state a bought (or won) answer moves — routes, notes, a rumour."""
        if target == "sella":
            self._advance_to("investigating")
            self._unlock("talk-it-out")
        elif target in ("borin", "tomm"):
            self._feed(
                "system",
                text="❧ Word bought — the carts turn north of the oak, nights, since Midwinter.",
            )

    def _leverage_paid(self, target: str, price: Price) -> BeatOutcome:
        """The shortcut: the purse meets the price, so no die is thrown (§6)."""
        st = self.state
        name = display_name(target)
        st.silver -= price.cost
        self._feed("system", text=f"❧ Coin answers — {name}'s price met (−{price.cost} guilders).")
        self._remember(
            target, f"took {price.cost} guilders and told what coin buys",
            kind="leverage", sentiment=1, salience=2, delta=+5,
            reason=f"was paid {price.cost} guilders",
        )
        self._mood(target, "warm", 0.5)
        self._leverage_state(target)
        if target == "sella":
            facts = (
                (f"the player puts {price.cost} guilders down first and {name} does not "
                "touch the coin — she notes it, the way a factor notes everything, and "
                "the number goes into a column with the player's name at the top of it"),
                ("she says: the guild rents the old monastery cellar, the one the order "
                "calls sealed; their travelers are under that hill"),
                ("and she adds: they have not bought this from her — they have bought "
                "the guild's silence about her having said it"),
            )
        elif target == "borin":
            facts = (
                (f"the player puts {price.cost} guilders down first; {name} takes the "
                "coin without counting it and grins at nothing in particular"),
                ("he says: carts, north of the oak, nights, since Midwinter — and that "
                "they paid for what he would have told them for the asking, but he will "
                "drink to the difference"),
            )
        elif target == "tomm":
            facts = (
                (f"the player puts {price.cost} guilders down first; {name}'s hand closes "
                "on the coin before his conscience can comment"),
                ("he says, fast and low: the guild's men buy silver over rate, and the "
                "carts that carry it turn north of the oak — nights, since Midwinter; "
                "he never told them that"),
            )
        else:
            facts = (
                (f"the player puts {price.cost} guilders down first and {name} takes it, "
                "paying out exactly what coin buys — a word, and not a kind one"),
            )
        return BeatOutcome(
            ack="You put the coin down first.",
            kind="talk",
            brief=BeatBrief(event="offer.paid", facts=facts, speakers=(name,)),
        )

    def _beat_offer(self, text: str) -> BeatOutcome:
        """Leverage (§6): coin answers a real need — or it buys nothing at all.

        When the character has a price and the purse meets it, the price
        decides the outcome and no die is thrown. When the purse is short, or
        coin is simply not what they want, the offer moves no state: the ask
        still has to be won as its own contested social check.
        """
        st = self.state
        st.advance_minutes(5)
        target = self._offer_target(text)
        if target is None:
            return BeatOutcome(
                ack="Your coin finds no taker.",
                kind="talk",
                brief=BeatBrief(
                    event="offer.empty",
                    facts=(
                        ("the player weighs coin in their palm, but there is nobody here "
                        "to take it"),
                        "a price needs a person, and the road keeps its own company tonight",
                    ),
                ),
            )
        if target == "sella" and st.lead_stage == "unheard":
            # The arc rail holds: coin cannot buy an answer you cannot ask for.
            return BeatOutcome(
                ack="You would not know what to ask.",
                kind="talk",
                brief=BeatBrief(
                    event="offer.sella.unheard",
                    facts=(
                        ("the player offers coin, but it gets them nothing yet — they do "
                        "not know enough to make a guild factor nervous"),
                        "learn what the road has been swallowing first",
                    ),
                ),
            )
        profile = profile_for(target)
        price = profile.price
        name = display_name(target)
        if price is not None and st.silver >= price.cost:
            return self._leverage_paid(target, price)
        if price is not None:
            facts = (
                (f"the player counts the purse twice: {st.silver} guilders against a "
                f"price of {price.cost}"),
                (f"{name} watches the arithmetic cross their face and lets it fail — "
                "the ask will have to be made the hard way now"),
            )
        else:
            facts = (
                (f"{name} does not even look at the player's hand: coin is not what "
                f"{name} wants, and both of them can hear the offer land wrong"),
                f"the ask has to be made in the only currency {name} trades in",
            )
            if not _ASK_RE.search(text):
                return BeatOutcome(
                    ack="Your coin lands wrong.",
                    kind="talk",
                    brief=BeatBrief(event="offer.no-price", facts=facts, speakers=(name,)),
                )
        mech, result = self._check(
            "persuasion", SOCIAL_DIFFICULTY.get(target, "Moderate"),
            f"Persuasion — {name}", npc=target, approach="coin",
        )
        if self._succeeded(result):
            self._leverage_state(target)
            if target == "sella":
                facts = (
                    ("the player makes their case with coin on the table, and it is not "
                    "the coin that lands — it is Marla's name; Sella closes the ledger "
                    "and looks up the road a long moment"),
                    ("she tells them: the guild rents the old monastery cellar, the one "
                    "the order calls sealed; if their two went up that road, they are "
                    "under the hill"),
                    ("she tells them to go before the bells ring again, and that they "
                    "never heard it here"),
                )
            else:
                facts = (
                    (f"{name} studies the player a moment longer than the offer deserved "
                    "and gives them the thing coin could not buy anyway: a word out of "
                    "the column, told the way favors are told"),
                )
        return BeatOutcome(
            ack="You ask it the hard way.",
            kind="talk",
            brief=BeatBrief(event="offer.ask", facts=facts, speakers=(name,)),
            mechanics=mech,
        )

    def _beat_confront(self, text: str) -> BeatOutcome:
        st = self.state
        st.advance_minutes(20)
        if st.completed:
            return BeatOutcome(
                ack="The undercroft is quiet.",
                kind="resolve",
                brief=BeatBrief(
                    event="confront.quiet",
                    facts=(
                        ("the player returns to the undercroft: quiet now, a cold "
                        "stair, a smell of lamp oil and rain"),
                        ("whatever the guild kept down here, the light and the law have "
                        "both found it; the player does not need to go down again"),
                    ),
                ),
            )
        if st.location == "lantern-inn":
            return BeatOutcome(
                ack="You consider the inn's cellar door.",
                kind="inspect",
                brief=BeatBrief(
                    event="confront.inn",
                    facts=(
                        "the player considers going down after the guild's business",
                        ("the inn's cellar holds barrels and salt and one padlocked door "
                        "the old gripes say goes further than it should"),
                        ("but the guild's business is not kept under Marla's feet: "
                        "whatever is below is below the old monastery, and the road goes "
                        "there when the player does"),
                    ),
                ),
            )
        if st.location != "old-monastery":
            return BeatOutcome(
                ack="You would need to get there first.",
                kind="inspect",
                brief=BeatBrief(
                    event="confront.elsewhere",
                    facts=(
                        ("the player makes for the cold they are after, but it comes from "
                        "under the Old Monastery"),
                        ("the monastery path leaves Ravenford along the north road, past "
                        "the oak"),
                    ),
                ),
            )
        if st.solution_path is None:
            return BeatOutcome(
                ack="You weigh the door.",
                kind="inspect",
                brief=BeatBrief(
                    event="confront.hint",
                    facts=(
                        ("the player stands at the monastery's undercroft door and feels "
                        "the cold through their boots"),
                        ("something below is worth the guild's money and the order's "
                        "silence — but they hold threads, not a way in"),
                        ("someone knows it: a factor with a ledger, a mercenary with a "
                        "grudge, or the road itself; finish one of those threads first"),
                    ),
                ),
            )
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
        # Resolution lifts the whole room: relief, cheer, and a factor's pride.
        self._mood("marla", "happy", 0.9)
        self._mood("borin", "happy", 0.6)
        self._mood("sella", "proud", 0.6)
        st.pc.setdefault("achievements", []).append(f"Freed the missing travelers (Day {st.day})")
        return BeatOutcome(
            ack="You go down into the cold.",
            kind="resolve",
            brief=BeatBrief(
                event="confront.resolve",
                facts=(
                    ("the player goes in: the tunnel mouth breathes cold and lamp oil, "
                    "and they go low along the wall past stacked silver that nobody in "
                    "Ravenford will ever admit to"),
                    ("the two travelers are there — rope-burned, hollow-eyed, and alive; "
                    "the player cuts them loose before the bells ring again and the hill "
                    "lets all three of them out into the rain"),
                    ("the elder traveler says: nobody came for three nights; they had "
                    "started to think the road forgot them too"),
                    ("by dawn it is over Ravenford: the guild's sealed cellar, the rent "
                    "ledger, the two names the notice board had begun to forget; "
                    "Sergeant Dain takes Fenn's statement twice, Marla sets two extra "
                    "cups on the bar and does not charge for them, and the travelers "
                    "walk the north road with the rain finally behind them"),
                    "the chapter closes grieving for nothing",
                ),
                speakers=("The elder traveler",),
                length="Detailed",
            ),
        )

    # -- Marla's memory greeting --------------------------------------------
    def _marla_remembers(self) -> tuple[str, ...]:
        """What Marla holds of the player, stated as facts for an arrival.

        The tags are the engine's (``marla_memory``); this reads them into plain
        statements the narrator can greet with, in words it writes itself.
        """
        st = self.state
        facts: list[str] = []
        for tag in st.marla_memory:
            if tag.startswith("stole:"):
                facts.append("she has not forgotten her missing silver")
            elif tag.startswith("fought:"):
                facts.append("she has not forgotten the brawl the player started by her hearth")
            elif tag.startswith("talked:"):
                facts.append(
                    f"she remembers the player asking after {tag.split(':', 1)[1]}"
                )
            elif tag == "shared:lead":
                facts.append("she remembers the missing travelers the player promised to find")
            elif tag.startswith("inspected:"):
                what = tag.split(":", 1)[1]
                what = what if what.startswith("the ") else f"the {what}"
                facts.append(f"she noticed the player poking around {what}")
            elif tag.startswith("saw:"):
                facts.append("she saw how the player eyed her strongbox")
            elif tag.startswith("resolved:"):
                facts.append("she remembers the travelers the player brought home")
        if not facts:
            facts.append(
                "this is a quiet first visit back: she has nothing on the player yet, "
                "and greets them as the innkeeper she is"
            )
        return tuple(facts)

    # -- pipeline fallback ---------------------------------------------------
    def _beat_pipeline(self, text: str) -> tuple[BeatOutcome, str | None]:
        """Unrecognised action: interpreter -> checks -> narrator (model path).

        The one beat whose prose the pipeline assembles itself (it carries the
        whole 10-step context: intent, checks, retrieved memories, the scene
        line). Authored beats go through ``_narrate`` with their own facts —
        same narrator, same rule: no model, no narration (P12/P14).
        """
        st = self.state
        if self.provider is None:
            raise ActFailure("connect_your_ai", "no model connected")
        st.advance_minutes(5)
        pipeline = Pipeline(provider=self.provider, meter=self.meter)
        action = ActionInput(
            campaign_id=self.session.campaign_id,
            text=text,
            scene=SceneContext(
                npcs_present=self.present_npcs(),
                npc_memories=self._npc_memories_for_present(),
                npc_moods=self._npc_moods_for_present(),
                npc_attitudes=self._npc_attitudes_for_present(),
                npc_dispositions=self._npc_dispositions_for_present(),
            ),
            seed_roll=self._seed_roll,
            seed_roll2=self._seed_roll2,
            inspired=self.state.inspired,
        )
        self._seed_roll = None
        self._seed_roll2 = None
        try:
            result = pipeline.orchestrate(
                action, state=self._pipeline_state(), prefs=self.prefs,
                suspend_on_check=self._suspend_on_check,
            )
        except CheckSuspension:
            raise  # two-phase throw: the player's die resolves this check
        except ProviderError as exc:
            # A failed model call never reads as fiction: the turn fails
            # machine-readably, persists nothing, and can be sent again.
            raise ActFailure("provider_failed", _scrub_provider_error(str(exc))) from exc
        except Exception as exc:  # an unexpected error is still not cover prose
            raise ActFailure(
                "turn_failed", _scrub_provider_error(f"{type(exc).__name__}: {exc}")
            ) from exc

        mechanics = None
        for check in result.checks:
            snapshot = check.request_snapshot
            if not check.trivial_auto:
                # The attempt itself teaches: a free-text check trains its
                # skill whatever the outcome, same scale as authored beats.
                xp = play_skill_xp(check.outcome)
                if xp:
                    self.state.award_skill(
                        str(snapshot.skill).strip().lower(), xp, str(snapshot.skill),
                    )
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
                    if check.kept_from:
                        # Inspiration burned on this resolution: show both dice.
                        kept = check.kept_from
                        dropped = kept[1] if kept[0] == check.roll else kept[0]
                        mechanics["advantage"] = True
                        mechanics["d20_second"] = dropped
                        self.state.inspired = False
                        self._feed("system", text=(
                            f"✦ Inspiration burns — two dice fall ({kept[0]}, "
                            f"{kept[1]}), the {check.roll} stands."
                        ))
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

    def _npc_moods_for_present(self) -> dict[str, dict[str, Any]]:
        """Live (non-neutral) moods of present NPCs, for the narrator prompt.

        Settled characters are simply absent — the block stays lean, the same
        way ``remembers`` only appears when there is something to remember.
        """
        out: dict[str, dict[str, Any]] = {}
        for name in self.present_npcs():
            entry = self.state.mood_of(npc_slug(name))
            if entry["intensity"] > 0 and entry["mood"] != DEFAULT_MOOD:
                out[name] = entry
        return out

    def _npc_attitudes_for_present(self) -> dict[str, int]:
        """The live relationship meter of present NPCs (§3, into the pipeline)."""
        return {name: self.state.attitude_for(npc_slug(name)) for name in self.present_npcs()}

    def _npc_dispositions_for_present(self) -> dict[str, str]:
        """The authored disposition line of present NPCs, for the narrator (§5)."""
        out: dict[str, str] = {}
        for name in self.present_npcs():
            line = disposition_for(npc_slug(name))
            if line:
                out[name] = line
        return out

    def _chronicle_tail(self) -> list[dict[str, Any]]:
        """The tail of the player-visible chronicle, for narrator continuity.

        The narrator must continue the live tale, never restart it: the last
        beats the player has already read — their own action echoes, the prose,
        the spoken lines, the notices — ride the prompt the way the retrieved
        memory slices do. A bounded tail, never the transcript (§25).
        """
        out: list[dict[str, Any]] = []
        for event in self.state.feed:
            kind = str(event.get("kind") or "")
            if kind not in ("narration", "dialogue", "system"):
                continue
            entry: dict[str, Any] = {"kind": kind, "text": str(event.get("text") or "")}
            if kind == "dialogue" and event.get("speaker"):
                entry["speaker"] = str(event["speaker"])
            out.append(entry)
        return out[-NARRATOR_CHRONICLE_LIMIT:]

    def _pipeline_state(self) -> dict[str, Any]:
        st = self.state
        pc = st.pc
        scene = SceneDirector(st).scene_block()
        return {
            "inventory": list(pc.get("equipment", [])),
            # Continuity (§25): the narrator prompt carries the tail of what
            # the player has already read, so the scene advances.
            "chronicle": self._chronicle_tail(),
            # Saga digest (#4): the rolling recap, refreshed below at
            # checkpoint beats; the pipeline feeds it straight to the prompt.
            "saga": st.saga or "",
            "npcs_alive": {"borin": not st.borin_down, "marla": True},
            "location": st.location,
            "known_locations": ["lantern-inn", "northern-road", "market"],
            "facts": [],
            "pc_name": pc.get("name", "the hero"),
            # Scene flow (§7/§8): the narrator sees where the moment happens,
            # what the scene is for, and how far it has moved.
            "scene_label": scene["label"],
            "scene_goal": scene["goal"],
            "scene_state": scene["state"],
        }
