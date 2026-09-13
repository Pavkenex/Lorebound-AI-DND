"""Character personality: authored trait profiles and the social-check math.

docs/SYSTEMS_DESIGN.md §5 (personality) + §6 (social checks, the hail-mary
rule). Pure data + pure functions — no state, no DB, no model.

A profile carries the trait axes (``-1`` is the far side, ``+1`` the near side),
the **soft spots** a character actually answers to, optional **vows**
(conditional and per-character — only some carry them, and a vow is a strong
modifier, never a wall), the **price** leverage can meet, and the social knobs
(approach and mood modifiers, mood biases, the disposition line the narrator
sees).

Three hooks consume it, all engine-authoritative:

- :func:`scale_relationship_delta` — traits scale how hard one interaction
  lands on the meter (§3 × §5, the slice-2 hook, called from
  ``Engine._remember``);
- :func:`biased_mood` — traits decide the mood an event actually leaves (§5,
  the slice-3 hook, called from ``Engine._mood``);
- :func:`social_adjustment` — ``DC = base + approach + mood − relationship
  credit`` (§6), consumed by the engine's social checks and by
  ``rules.checks.apply_social_policy``.

Coefficients here are tunable knobs, not contracts: the math (base, caps,
rounding) is the contract, the numbers are authored fiction.
"""
from __future__ import annotations

import math
import re
from dataclasses import dataclass, field

from app.modules.npc.mood import DEFAULT_MOOD

#: Trait axes (§5). Every profile uses a subset; unknown axes read as 0.0.
TRAIT_AXES: tuple[str, ...] = (
    "pride", "faith", "greed", "kindness", "caution", "loyalty",
)

#: Social approaches a check can be made with.
APPROACHES: tuple[str, ...] = (
    "appeal", "charm", "pressure", "deceive", "coin", "flirt",
)

#: What a character actually answers to (§5 "soft spots").
SOFT_SPOT_VOCAB: tuple[str, ...] = (
    "coin", "drink", "duty", "family", "faith", "knowledge", "loneliness",
    "safety", "vanity",
)

#: Which skill spells which approach (the pipeline only knows the skill).
#: Keys are lower-case: callers pass both "Persuasion" and "persuasion".
SKILL_APPROACH: dict[str, str] = {
    "persuasion": "charm",
    "intimidation": "pressure",
    "deception": "deceive",
}

#: A plea is a different approach from charm: it asks rather than sells.
_PLEAD_RE = re.compile(r"\b(plead\w*|beg\w*|implor\w*|reason with|talk down|coax)\b", re.IGNORECASE)


def approach_for_skill(skill: str, text: str = "") -> str:
    """The approach a check is made with: skill first, plea-shaped text refines it."""
    approach = SKILL_APPROACH.get(str(skill).strip().lower(), "appeal")
    if approach == "charm" and text and _PLEAD_RE.search(text):
        return "appeal"
    return approach


def _round_away_from_zero(value: float) -> int:
    """Round half away from zero — deltas and DC shifts stay predictable."""
    return math.floor(value + 0.5) if value >= 0 else -math.floor(-value + 0.5)


# ---------------------------------------------------------------------------
# Authored profiles
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class Vow:
    """A value a character holds. Conditional and personal — never a wall."""

    words: str
    #: Approaches the vow backs (a discount) and resists (a penalty).
    favors: tuple[str, ...] = ()
    forbids: tuple[str, ...] = ()


@dataclass(frozen=True)
class MoodBias:
    """A raw event mood this character wears differently, gated by one trait."""

    axis: str
    cutoff: float  # fires when traits[axis] >= cutoff
    from_mood: str
    to_mood: str


@dataclass(frozen=True)
class Price:
    """Leverage: what coin buys, and what the shortcut costs (§6)."""

    cost: int
    words: str


@dataclass(frozen=True)
class Pressure:
    """What leaning on this character really costs (§6 "pressure carries its price")."""

    hit: int  # meter move when the pressure works
    backfire: int  # meter move when it does not
    words: str = ""


@dataclass(frozen=True)
class Profile:
    """One authored character: traits, needs, vows and the social knobs."""

    slug: str
    name: str
    disposition: str
    traits: dict[str, float] = field(default_factory=dict)
    soft_spots: tuple[str, ...] = ()
    vows: tuple[Vow, ...] = ()
    approach_mods: dict[str, int] = field(default_factory=dict)
    mood_mods: dict[str, int] = field(default_factory=dict)
    mood_biases: tuple[MoodBias, ...] = ()
    #: Interaction kind -> {axis: weight}: traits scale the meter move (§5).
    delta_weights: dict[str, dict[str, float]] = field(default_factory=dict)
    price: Price | None = None
    pressure: Pressure = field(default_factory=lambda: Pressure(hit=-10, backfire=-5))

    def trait(self, axis: str) -> float:
        return float(self.traits.get(axis, 0.0))


#: The five Ravenford characters (docs §5).
PROFILES: dict[str, Profile] = {
    "marla": Profile(
        slug="marla",
        name="Marla Voss",
        disposition=(
            "proud, watchful, kind under the armor; the inn's good name is her own, "
            "and she pays her own way"
        ),
        traits={"pride": 0.6, "caution": 0.6, "loyalty": 0.8, "kindness": 0.5,
                "greed": 0.0, "faith": 0.2},
        soft_spots=("duty", "family"),
        vows=(Vow("hospitality — no one is turned out of her house at night",
                  favors=("appeal",)),),
        approach_mods={"appeal": -2, "charm": -1, "pressure": 4, "deceive": 4,
                       "coin": 2, "flirt": -1},
        mood_mods={"warm": -2, "happy": -2, "amused": -1, "curious": -1, "sad": 1,
                   "tired": 1, "suspicious": 3, "angry": 4, "resentful": 3,
                   "afraid": -1},
        mood_biases=(MoodBias("pride", 0.6, "afraid", "resentful"),),
        delta_weights={"insult": {"pride": 1.0}},
        price=None,  # coin is not what she wants (§6: leverage must match the need)
        pressure=Pressure(hit=-20, backfire=-10,
                          words="the inn is not made small in her hearing"),
    ),
    "borin": Profile(
        slug="borin",
        name="Borin",
        disposition=(
            "greedy, careless, quick to laugh and quicker to sell an answer; "
            "kept at the bar by drink and the want of an audience"
        ),
        traits={"greed": 0.8, "pride": 0.4, "caution": -0.4, "kindness": -0.4,
                "loyalty": 0.1, "faith": -0.3},
        soft_spots=("coin", "drink", "loneliness"),
        vows=(),  # the one who carries none
        approach_mods={"appeal": 1, "charm": -1, "pressure": -1, "deceive": 0,
                       "coin": -2, "flirt": 0},
        mood_mods={"angry": 3, "suspicious": 2, "tired": 2, "amused": -1,
                   "warm": -2, "happy": -1, "afraid": -2},
        mood_biases=(
            MoodBias("greed", 0.8, "warm", "curious"),   # kindness is a transaction
            MoodBias("caution", -0.4, "anxious", "amused"),  # he does not sweat
        ),
        delta_weights={"gift": {"greed": 1.0}, "leverage": {"greed": 1.0},
                       "insult": {"pride": 1.0}},
        price=Price(cost=6, words="six guilders buys whatever he has been drinking to forget"),
        pressure=Pressure(hit=-20, backfire=-5, words="he does not forget who leaned on him"),
    ),
    "sella": Profile(
        slug="sella",
        name="Sella Voss",
        disposition=(
            "proud, guarded, mercenary to the marrow — and a Voss before she is a "
            "factor, which is the door that opens on her"
        ),
        traits={"pride": 0.7, "greed": 0.8, "caution": 0.7, "loyalty": 0.5,
                "kindness": 0.0, "faith": 0.1},
        soft_spots=("coin", "family"),
        vows=(Vow("a factor's word is her bond — a bargain struck is kept",
                  favors=("coin",), forbids=("deceive",)),),
        approach_mods={"appeal": 1, "charm": 1, "pressure": 1, "deceive": 3,
                       "coin": -2, "flirt": 2},
        mood_mods={"suspicious": 4, "angry": 4, "resentful": 3, "afraid": 2,
                   "curious": -1, "warm": -1, "happy": -1, "tired": 1},
        mood_biases=(MoodBias("pride", 0.7, "afraid", "resentful"),),
        delta_weights={"gift": {"greed": 1.0}, "leverage": {"greed": 1.0},
                       "insult": {"pride": 1.0}},
        price=Price(cost=10, words="ten guilders, and the guild calls it a consulting fee"),
        pressure=Pressure(hit=-15, backfire=-5, words="the guild hears about it either way"),
    ),
    "tomm": Profile(
        slug="tomm",
        name="Tomm Ash",
        disposition=(
            "anxious, decent, permanently braced for the guild's eye; his stock is "
            "his word and he is terrified of losing either"
        ),
        traits={"caution": 0.8, "kindness": 0.6, "greed": 0.3, "pride": -0.2,
                "loyalty": 0.4, "faith": 0.3},
        soft_spots=("safety", "coin"),
        vows=(Vow("a peddler's word is his stock — a bargain struck is kept",
                  favors=("appeal",)),),
        approach_mods={"appeal": -2, "charm": -1, "pressure": 2, "deceive": 2,
                       "coin": -2, "flirt": 1},
        mood_mods={"afraid": 3, "anxious": 3, "suspicious": 3, "angry": 1,
                   "warm": -2, "happy": -1},
        mood_biases=(MoodBias("caution", 0.8, "warm", "suspicious"),),
        delta_weights={"gift": {"greed": 1.0}, "leverage": {"greed": 1.0}},
        price=Price(cost=4, words="four guilders settles a nervous man's account"),
        pressure=Pressure(hit=-10, backfire=-5, words="he remembers who frightened him"),
    ),
    "anselm": Profile(
        slug="anselm",
        name="Brother Anselm",
        disposition=(
            "devout, gentle, frightened of the stairs he guards; his vows are real "
            "and his hands shake on the keys"
        ),
        traits={"faith": 0.9, "kindness": 0.7, "caution": 0.6, "loyalty": 0.7,
                "greed": -0.6, "pride": 0.1},
        soft_spots=("faith", "duty", "loneliness"),
        vows=(
            Vow("the order's silence — a thing told at the porter's door is never told again",
                forbids=("pressure",)),
            Vow("the road is owed hospitality — a traveler is never turned away",
                favors=("appeal",)),
        ),
        approach_mods={"appeal": -2, "charm": 0, "pressure": 3, "deceive": 2,
                       "coin": 2, "flirt": 1},
        mood_mods={"afraid": 2, "anxious": 2, "suspicious": 1, "angry": 2,
                   "warm": -2, "happy": -2},
        mood_biases=(MoodBias("faith", 0.9, "afraid", "anxious"),),
        delta_weights={"gift": {"greed": 1.0}},  # coin lands at half weight on him
        price=None,  # the porter cannot be bought — his need is the order's silence
        pressure=Pressure(hit=-10, backfire=-5, words="the order closes ranks around him"),
    ),
}

#: The generic guest: no traits, no soft spots, no modifiers of its own.
NEUTRAL_PROFILE = Profile(slug="", name="", disposition="")


def profile_for(slug: str | None) -> Profile:
    """The authored profile for an NPC slug (unknown slugs read as neutral)."""
    return PROFILES.get(str(slug or "").strip().lower(), NEUTRAL_PROFILE)


def disposition_for(slug: str | None) -> str:
    """The one-line disposition the narrator sees for a character ("" if unknown)."""
    return profile_for(slug).disposition


# ---------------------------------------------------------------------------
# Hook 1 — traits scale relationship deltas (§5, slice-2 hook)
# ---------------------------------------------------------------------------

#: How far a trait may bend a meter move, either way.
DELTA_FACTOR_MIN = 0.5
DELTA_FACTOR_MAX = 2.0

#: Interactions that answer a soft spot land harder on the meter (§3: "a lonely
#: one over-weights attention").
SOFT_SPOT_KINDS: dict[str, tuple[str, ...]] = {
    "conversation": ("loneliness", "vanity"),
    "flirt": ("loneliness", "vanity"),
    "gift": ("coin", "vanity"),
    "leverage": ("coin",),
    "help": ("duty", "safety"),
}
SOFT_SPOT_WEIGHT = 0.5


def delta_factor(slug: str | None, kind: str) -> float:
    """The multiplier a character's traits put on one interaction's meter move."""
    profile = profile_for(slug)
    factor = 1.0
    for axis, weight in profile.delta_weights.get(str(kind or ""), {}).items():
        factor += weight * profile.trait(axis)
    for spot in SOFT_SPOT_KINDS.get(str(kind or ""), ()):
        if spot in profile.soft_spots:
            factor += SOFT_SPOT_WEIGHT
    return max(DELTA_FACTOR_MIN, min(DELTA_FACTOR_MAX, factor))


def scale_relationship_delta(slug: str | None, kind: str, delta: int) -> int:
    """Weight one meter move by personality (§5): a proud character takes an
    insult double, a lonely one over-weights attention, a greedy one a gift.

    Every move keeps its authored direction; only the size changes, and a zero
    move stays zero.
    """
    if not delta:
        return 0
    return _round_away_from_zero(delta * delta_factor(slug, kind))


# ---------------------------------------------------------------------------
# Hook 2 — traits bias the mood an event leaves (§5, slice-3 hook)
# ---------------------------------------------------------------------------


def biased_mood(slug: str | None, mood: str) -> str:
    """The mood this character actually wears after an event (§5).

    A raw event mood is re-read through the character's own biases: a proud
    character's fear curdles into a grudge, a wary one's sudden warmth reads as
    a play, a devout one's fear turns to prayer-tight anxiety. First matching
    bias wins; no bias means the event mood stands.
    """
    word = str(mood or DEFAULT_MOOD).strip().lower()
    profile = profile_for(slug)
    for bias in profile.mood_biases:
        if bias.from_mood == word and profile.trait(bias.axis) >= bias.cutoff:
            return bias.to_mood
    return word


# ---------------------------------------------------------------------------
# Hook 3 — social DCs (§6)
# ---------------------------------------------------------------------------

#: Relationship credit: every 10 points of standing is 1 DC, capped at 10.
CREDIT_PER_POINT = 0.1
CREDIT_CAP = 10

#: What a vow does to an approach: it backs one (discount) or resists one.
VOW_FAVORS = 2
VOW_FORBIDS = 5

#: Player-facing phrasing for each approach (the "why" the card shows).
APPROACH_PHRASE: dict[str, str] = {
    "appeal": "a plain plea",
    "charm": "easy charm",
    "pressure": "a hard word",
    "deceive": "a smooth lie",
    "coin": "coin on the bar",
    "flirt": "a warm look",
}


def relationship_credit(attitude: int) -> int:
    """The DC a relationship is worth (§6): warmer = lower, colder = higher."""
    try:
        value = int(attitude)
    except (TypeError, ValueError):
        return 0
    return max(-CREDIT_CAP, min(CREDIT_CAP, _round_away_from_zero(value * CREDIT_PER_POINT)))


@dataclass(frozen=True)
class SocialContext:
    """Everything a social check knows about the character it is aimed at."""

    slug: str = ""
    approach: str = ""
    mood: str = ""
    mood_intensity: float = 0.0
    attitude: int = 0


@dataclass(frozen=True)
class SocialAdjustment:
    """A social DC, opened up: what moved it, and by how much."""

    base_dc: int
    approach_mod: int
    mood_mod: int
    credit: int
    vow_mod: int = 0
    parts: tuple[str, ...] = ()

    @property
    def shift(self) -> int:
        """Total DC shift: approach + mood + vow − relationship credit."""
        return self.approach_mod + self.mood_mod + self.vow_mod - self.credit

    @property
    def adjusted_dc(self) -> int:
        return max(1, self.base_dc + self.shift)

    @property
    def why(self) -> str:
        """The one-line reason the check prompt shows ("why this DC")."""
        return " · ".join(self.parts)


def _signed(value: int) -> str:
    return f"+{value}" if value > 0 else f"−{abs(value)}"


def _mood_mod(profile: Profile, mood: str, intensity: float) -> int:
    """A mood's DC weight, scaled by how strong the mood actually is."""
    try:
        level = max(0.0, min(1.0, float(intensity)))
    except (TypeError, ValueError):
        level = 0.0
    if level <= 0:
        return 0
    base = profile.mood_mods.get(str(mood).strip().lower(), 0)
    if not base:
        return 0
    return _round_away_from_zero(base * level)


def _vow_mod(profile: Profile, approach: str) -> tuple[int, str]:
    """A vow is a strong modifier on one approach, never an absolute wall."""
    mod = 0
    words = ""
    for vow in profile.vows:
        if approach and approach in vow.forbids:
            mod += VOW_FORBIDS
            words = vow.words
        elif approach and approach in vow.favors:
            mod -= VOW_FAVORS
            words = words or vow.words
    return mod, words


def _possessive(profile: Profile) -> str:
    return f"{profile.name}'s" if profile.name else "their"


def social_adjustment(social: SocialContext, base_dc: int) -> SocialAdjustment:
    """``adjusted DC = base + approach + mood − relationship credit`` (§6).

    Every term is reported in :attr:`SocialAdjustment.parts` so the check
    prompt can show the player exactly why the DC is what it is.
    """
    profile = profile_for(social.slug)
    approach = str(social.approach or "").strip().lower()
    approach_mod = int(profile.approach_mods.get(approach, 0))
    mood_word = str(social.mood or "").strip().lower()
    mood_mod = _mood_mod(profile, mood_word, social.mood_intensity)
    credit = relationship_credit(social.attitude)
    vow_mod, vow_words = _vow_mod(profile, approach)

    parts: list[str] = []
    if approach_mod:
        parts.append(f"{APPROACH_PHRASE.get(approach, approach or 'the approach')} "
                     f"({_signed(approach_mod)})")
    if mood_mod:
        parts.append(f"{profile.name or 'they'} is {mood_word} ({_signed(mood_mod)})")
    if credit:
        if credit > 0:
            parts.append(f"{_possessive(profile)} warmth counts for you ({_signed(-credit)})")
        else:
            parts.append(f"{_possessive(profile)} coldness stands against you ({_signed(-credit)})")
    if vow_mod:
        parts.append(f"a vow holds: {vow_words} ({_signed(vow_mod)})")

    return SocialAdjustment(
        base_dc=int(base_dc), approach_mod=approach_mod, mood_mod=mood_mod,
        credit=credit, vow_mod=vow_mod, parts=tuple(parts),
    )
