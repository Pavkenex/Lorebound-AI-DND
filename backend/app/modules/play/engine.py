"""Live act engine: player text -> engine-resolved beat -> narration + effects.

Invariants (GDD): the model is never the database; player text is an attempt
only. Beats below are engine-authored (slice content); anything unrecognised
falls through to the 10-step Pipeline (interpreter -> checks -> narrator) where
the stub provider keeps the game playable with zero models.

The engine owns: state transitions, d20 checks (rules engine), feed events,
lead/clue progression, Marla's memory. The provider only writes prose.
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Any

from app.modules.actions.pipeline import ActionInput, Pipeline
from app.modules.actions.suggest import SceneContext
from app.modules.ai.metering import MeterRegistry
from app.modules.ai.providers import Provider
from app.modules.narrator.prefs import ContentPrefs
from app.modules.play.session import PlaySession
from app.modules.rules.checks import (
    CheckRequest,
    CheckResult,
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


@dataclass
class BeatOutcome:
    ack: str
    narration: str
    kind: str = "scene_transition"  # checkpoint kind; None -> persist only
    mechanics: dict[str, Any] | None = None
    dialogue: list[dict[str, str]] = field(default_factory=list)
    new_leads: list[str] = field(default_factory=list)


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

_MARLA = re.compile(r"\bmarla\b|\binnkeeper\b", re.I)
_INN = re.compile(r"\binn\b|\blantern\b", re.I)
_BORIN = re.compile(r"\bborin\b|\bmercenary\b|\bdrunk\b", re.I)
_BOX = re.compile(r"strongbox|strong box|lockbox|storeroom|pantry|till|till box", re.I)
_BOARD = re.compile(r"notice|board|plea|posting|parchment", re.I)
_LEDGER = re.compile(r"ledger|guest ?book|books", re.I)
_CELLAR = re.compile(r"cellar|trapdoor|cellars", re.I)
_MARKET = re.compile(r"\bmarket\b|\bstalls?\b|\bshops?\b|sella", re.I)
_MONASTERY = re.compile(r"monastery|chapel|anselm|beacon|monks?", re.I)


def route(text: str) -> str:
    """Classify player text to a beat name (or ``pipeline`` fallback)."""
    t = text.strip()
    # Target-specific rules first: naming the ledger/board/cellar wins over the
    # generic "mentions Marla" rules ("I search Marla's ledger" is an inspection).
    if _BOX.search(t) and re.search(_STEAL_V, t, re.I):
        return "steal"
    if _BORIN.search(t) and re.search(_FIGHT_V, t, re.I):
        return "fight"
    if _BOARD.search(t) and (re.search(_INSPECT_V, t, re.I) or not re.search(_TALK_V, t, re.I)):
        return "inspect_board"
    if _LEDGER.search(t) and (re.search(_INSPECT_V, t, re.I) or not re.search(_TALK_V, t, re.I)):
        return "inspect_ledger"
    if _CELLAR.search(t) and re.search(_INSPECT_V, t, re.I):
        return "inspect_cellar"
    if _MARLA.search(t) and re.search(_TALK_V, t, re.I):
        return "talk"
    if _MARLA.search(t) and re.search(_INSPECT_V, t, re.I):
        return "talk"
    if re.search(r"\b(ask|question|talk|speak)\b.*\b(wagon|travelers?|guests?|missing)\b", t, re.I):
        return "talk"
    if re.search(_REST_V, t, re.I):
        return "rest"
    if _MARKET.search(t) and re.search(r"\b(go|walk|head|travel|visit|leave|enter|toward|towards|to)\b", t, re.I):
        return "travel_market"
    if _MONASTERY.search(t) and re.search(r"\b(go|walk|head|travel|visit|leave|enter|toward|towards|to)\b", t, re.I):
        return "travel_monastery"
    if _INN.search(t) and re.search(_RETURN_V, t, re.I):
        return "return"
    if re.search(_LEAVE_V, t, re.I):
        return "leave"
    if re.search(_INSPECT_V, t, re.I):
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

    # -- helpers ------------------------------------------------------------
    @property
    def state(self):  # noqa: ANN201 - PlayState (avoids import cycle for typing)
        return self.session.state

    def _modifiers(self, skill: str) -> tuple[int, int]:
        attr = SKILL_ATTRIBUTE.get(skill, "Wits")
        value = int(self.state.pc.get("attributes", {}).get(attr, 10))
        return (value - 10) // 2, TRAINED_BONUS

    def _check(self, skill: str, difficulty: str, label: str) -> tuple[dict[str, Any], CheckResult]:
        """Roll one d20 check for a beat; returns (mechanics contract, result)."""
        attr_mod, skill_mod = self._modifiers(skill)
        request = CheckRequest(
            campaign_id=self.session.campaign_id,
            skill=skill.title(),
            attribute_mod=attr_mod,
            skill_mod=skill_mod,
            dc=dc_for_band(difficulty) or 10,
            difficulty=difficulty,
        )
        result = roll_check(request, roll=self._seed_roll)
        self._seed_roll = None  # a seed only steers the action's first check
        mech = {
            "label": label,
            "roll": f"d20{attr_mod + skill_mod:+d}" if (attr_mod + skill_mod) else "d20",
            "total": result.total,
            "detail": result.cost,
            "d20": result.roll,
            "outcome": result.outcome.value,
        }
        return mech, result

    @staticmethod
    def _succeeded(result: CheckResult) -> bool:
        return result.outcome in SUCCESS_OUTCOMES

    def _feed(self, kind: str, **payload: Any) -> None:
        self.state.append_feed(kind, **payload)

    def _narrate_event(self, text: str) -> None:
        self._feed("narration", text=text)

    def _discover_lead(self, outcome: BeatOutcome) -> None:
        if self.state.set_lead_stage("rumored"):
            outcome.new_leads.append("Missing Travelers")
            self._feed("lead", lead="Missing Travelers — two travelers vanished on the Northern Road")

    def _advance_to(self, stage: str) -> bool:
        return self.state.set_lead_stage(stage)

    # -- public API ----------------------------------------------------------
    def act(self, text: str, seed_roll: int | None = None) -> tuple[dict[str, Any], str | None]:
        """Resolve one action. Returns (frontend-contract response, checkpoint kind)."""
        self._seed_roll = seed_roll if seed_roll is not None else None
        t = (text or "").strip()
        self.state.actions_taken += 1
        self._feed("system", text=f"❧ {t}")

        beat = route(t)
        handler = getattr(self, f"_beat_{beat}")
        if beat == "pipeline":
            outcome, checkpoint = handler(t)
        else:
            outcome = handler(t)
            checkpoint = outcome.kind

        response = {
            "ack": outcome.ack,
            "mechanics": outcome.mechanics,
            "narration": outcome.narration,
            "dialogue": outcome.dialogue,
            "newLeads": outcome.new_leads,
        }
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
        elif st.lead_stage == "rumored":
            self._advance_to("accepted")
            out.narration = TALK_ACCEPT_NARRATION
            out.dialogue = [{"speaker": "Marla Voss", "line": TALK_ACCEPT_LINE}]
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
        elif result.outcome == Outcome.CriticalFailure:
            st.note("saw:sneaking")
            narration = STRONGBOX_CAUGHT
        else:
            st.note("heard:noise")
            narration = STRONGBOX_FAIL
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
        st.advance_minutes(10)
        return BeatOutcome(ack="You step out into the rain.", narration=LEAVE_NARRATION, kind="travel")

    def _beat_return(self, text: str) -> BeatOutcome:
        st = self.state
        st.location = "lantern-inn"
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
        st.advance_minutes(15)
        return BeatOutcome(ack="You take the market road.", narration=MARKET_NARRATION, kind="travel")

    def _beat_travel_monastery(self, text: str) -> BeatOutcome:
        st = self.state
        st.location = "old-monastery"
        st.advance_minutes(25)
        return BeatOutcome(ack="You climb the monastery path.", narration=MONASTERY_NARRATION, kind="travel")

    # -- Marla's memory greeting --------------------------------------------
    _GREETING_TEMPLATES = (
        "Welcome back. Marla looks up from the bar — she remembers {recalled}.",
        "The door has hardly shut before Marla says it: she remembers {recalled}, "
        "and does not pretend otherwise.",
        "Marla slides a cup across the bar without being asked. \"Back, then,\" she "
        "says, and lets you hear the rest in the pause: {recalled}.",
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
            scene=SceneContext(npcs_present=self.present_npcs()),
            seed_roll=self._seed_roll,
        )
        self._seed_roll = None
        try:
            result = pipeline.orchestrate(action, state=self._pipeline_state(), prefs=self.prefs)
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
            if check.surfaced:
                mechanics = {
                    "label": f"{check.request_snapshot.skill} check",
                    "roll": "d20",
                    "total": check.total,
                    "detail": check.cost,
                    "d20": check.roll,
                    "outcome": check.outcome.value if hasattr(check.outcome, "value") else check.outcome,
                }
                break

        narration = result.narration.narration
        out = BeatOutcome(
            ack="The chronicler weighs your move.",
            narration=narration,
            kind="",  # free text: persist, no autosave churn
            mechanics=mechanics,
        )
        # world-fact attempts are recorded but change nothing (authority invariant)
        if result.intent.world_fact_attempt:
            self._feed("system", text="❧ (the world does not take dictation)")
        return out, None

    # -- world views ----------------------------------------------------------
    def present_npcs(self) -> list[str]:
        st = self.state
        if st.location == "lantern-inn":
            npcs = ["Marla"]
            if not st.borin_down:
                npcs.append("Borin")
            return npcs
        if st.location == "northern-road":
            return []
        if st.location == "market":
            return ["Sella", "Tomm"]
        if st.location == "old-monastery":
            return ["Brother Anselm"]
        if st.location == "cellar":
            return []
        return []

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
