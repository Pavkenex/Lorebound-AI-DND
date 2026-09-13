"""Mood vocabulary and rules (docs/SYSTEMS_DESIGN.md §4; systems slice 3).

Pure data + pure functions — no state, no DB: the curated v1 vocabulary, the
content-gated words and their fallbacks, intensity clamping, the per-character
baseline hook, and the decay rate. :class:`app.modules.play.state.PlayState`
stores the live values (``moods``) and owns decay on the clock; the view
(``GET /state``) and the narrator prompt both surface moods through
:func:`surfaced_mood`.

Gating rule (§4): NSFW-adjacent moods (flirty / horny) only surface when the
campaign's content settings allow them (``ContentPrefs.nsfw`` — the master
switch in ``modules/narrator/prefs.py``); otherwise they fall back to a
same-family word. The engine gates when it *sets* a mood (it only stores a
word the settings allowed at that moment) and every surface gates again
through :func:`surfaced_mood`, so a settings flip can never leak a word the
current boundaries do not allow.
"""
from __future__ import annotations

import math

#: Curated v1 vocabulary (§4). Extensible — unknown words raise on set so the
#: save never carries a mood the UI has no chip for.
MOOD_VOCAB: tuple[str, ...] = (
    "neutral", "happy", "amused", "warm", "sad", "lonely", "angry", "afraid",
    "anxious", "tired", "curious", "suspicious", "grateful", "resentful",
    "proud", "flirty", "horny",
)

#: The resting mood: what a character settles back to as intensity fades.
DEFAULT_MOOD = "neutral"

#: Moods that only surface under NSFW content settings.
NSFW_MOODS: frozenset[str] = frozenset({"flirty", "horny"})

#: What a gated mood reads as when settings do not allow it (§4).
MOOD_FALLBACK: dict[str, str] = {"flirty": "warm", "horny": "amused"}

#: Per-character baseline (slice 4 fills the hook from the authored profiles):
#: a character at rest reads as their own word, and every live mood decays back
#: to it. The five Ravenford characters rest as authored; Marla and Borin rest
#: neutral (their beats author every mood they wear).
MOOD_BASELINES: dict[str, str] = {
    "marla": "neutral",
    "borin": "neutral",
    "sella": "curious",
    "tomm": "anxious",
    "anselm": "anxious",
}

#: How much intensity fades per hour of in-game time.
MOOD_DECAY_PER_HOUR = 0.25

#: At or below this a mood has settled: it snaps back to the baseline word.
MOOD_SETTLED = 0.02


def mood_word(value: object) -> str:
    """Validate a mood word; raises ValueError on anything off-vocabulary."""
    word = str(value).strip().lower()
    if word not in MOOD_VOCAB:
        raise ValueError(f"unknown mood {value!r}")
    return word


def clamp_intensity(value: object) -> float:
    """Keep a mood intensity inside 0..1 (junk input reads as 0)."""
    try:
        level = float(value)  # type: ignore[arg-type]
    except (TypeError, ValueError):
        return 0.0
    if math.isnan(level) or level < 0:
        return 0.0
    return min(1.0, level)


def baseline_mood(slug: str) -> str:
    """The mood a character rests at (baseline hook for personality, §5)."""
    return MOOD_BASELINES.get(slug, DEFAULT_MOOD)


def is_gated(mood: str) -> bool:
    """True when a mood needs NSFW content settings to surface."""
    return mood in NSFW_MOODS


def surfaced_mood(mood: str, *, nsfw: bool = False) -> str:
    """The word a mood surfaces as under the campaign's content settings.

    Gated moods fall back to a same-family word unless ``nsfw`` is on; every
    other word passes through untouched (fallbacks are never gated, so this
    is idempotent).
    """
    word = str(mood).strip().lower()
    if is_gated(word) and not nsfw:
        return MOOD_FALLBACK.get(word, DEFAULT_MOOD)
    return word
