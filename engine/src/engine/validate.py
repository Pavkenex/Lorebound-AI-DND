"""State validator — the only writer of model-proposed state (spec §6).

Contract (R2 card):
- ``LEAD_TRANSITIONS`` is THE legal lead-stage machine. Allowed transitions:
    unheard -> rumored | abandoned
    rumored -> accepted | abandoned
    accepted -> in_progress | abandoned | failed
    in_progress -> complicated | resolved | failed | abandoned
    complicated -> in_progress | resolved | failed | abandoned
    resolved / failed / abandoned -> (terminal)
  No skipping: unheard -> resolved is ILLEGAL.
- ``Validator.validate(deltas, turn=...)`` returns a Verdict per delta:
    accepted | clamped (with clamped_to) | rejected (with note).
  Rules: kind must be known + required keys present; relationship category
  known, per-event |delta| clamped to config; mood deltas clamped to [-1,1]
  per event and total [-1,1]; currency conservation vs store (reject if
  insufficient); inventory_remove conservation; hp/stat range clamps;
  fact rate limit per turn (config.memory.fact_rate_limit_per_turn);
  lead transitions against the machine + store's current stage; dead NPCs
  (alive == 0) cannot receive mood/relationship/fact deltas sourced from them.
- ``Validator.commit(verdicts, turn=...)`` applies accepted+clamped deltas to
  the store (relationship -> ledger append; mood -> moods upsert; fact ->
  world_facts insert with contradiction check; lead -> stage update + history).
  Returns CommitReport. Rejected deltas are NEVER written.
- ``check_contradiction(statement)``: embedding/lexical similarity + numeric
  conflict heuristics against existing (esp. pinned) facts; returns conflicting
  fact ids. New facts that contradict an existing fact with ``contradicts``
  linkage are rejected; near-duplicates reinforce instead of duplicate.

Conventions this module pins (callers depend on them; all are deterministic):

* **Targets.** ``mood``/``relationship`` targets accept ``"npc:<id>"``,
  ``"<id>"`` or an exact NPC name and are written under the canonical
  ``"npc:<id>"`` key so ``moods``/``relationship_ledger`` never hold two
  spellings of one NPC. Character targets (``hp``/``stat``/``status_*``) accept
  ``"<id>"``, ``config.player_id`` or an exact character name; in this
  single-character campaign (spec's single-player assumption) the player row is
  the configured id when it exists, else the only/first row.
* **Character sheet.** Sheet numbers live in ``characters.stats``:
  ``hp`` (clamped to ``0..stats["max_hp"]`` when ``max_hp`` is present, else
  floored at 0), ``currency`` (``gold`` is accepted as a read alias for worlds
  that name it that; the balance is written back under the key that exists),
  and attribute stats clamped to ``stat_bounds`` (default ``(-10, 30)``,
  constructor-overridable). Items live in ``characters.inventory`` as
  ``{item_id, qty, flags}``.
* **Character-scoped deltas.** ``currency``, ``inventory_*`` and ``status_*``
  act on the player character by default (the spec's single-player campaign);
  the inventory kinds additionally accept ``data["character_id"]`` (their
  ``target`` is the item id, per ARCHITECTURE.md's payload table).
* **Clamp vs reject.** Number-range rules (mood, relationship, hp, stat) clamp
  and report ``clamped`` with ``clamped_to`` = the value actually applied
  (a dict for mood). Conservation rules (currency, inventory) REJECT outright —
  a spend never applies partially. Facts/leads reject; they have no clamp.
* **Multi-delta batches.** ``validate`` tracks the running state of the batch
  (balances, mood, relationship value, lead stage, facts accepted) so two
  deltas in one turn are checked against each other as well as the store;
  ``validate`` itself writes NOTHING.
* **Relationship current value.** The ±100 total clamp uses the decayed current
  from ``memory.RelationshipLedger`` when that module is implemented, else the
  raw ``disposition_base + Σ ledger deltas`` (an undecayed upper bound;
  conservative). An NPC already outside the range can only be pushed back
  toward it, never further out.
* **Relationship decay class (post-rebuild decision 1a, 2026-09-14).** At commit
  the delta's tagged class wins when it is one of ``DECAY_CLASSES``; otherwise
  the class derives from the APPLIED (post-clamp) magnitude via
  ``memory.RelationshipLedger.default_decay_class`` — ``|delta| >= 25`` durable,
  ``>= 10`` slow, else fast. This mirrors ``memory.append_delta``, so validator-
  and ledger-written rows can no longer disagree.
* **Commit order.** ``commit`` applies the verdicts it is handed, in order,
  inside ``store.transaction()`` when the store provides one; verdicts rejected
  at validate time are never re-applied, and facts are re-checked at commit
  time (a batch can itself create a contradiction).
* **Mood decay** is R3's concern: commit writes the post-delta valence/arousal
  and ``last_updated_turn``; ``MoodTracker`` decays lazily at retrieval.
"""
from __future__ import annotations

import contextlib
import re
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from typing import Any

from .config import EngineConfig
from .models import (
    DELTA_KINDS,
    DELTA_REQUIRED,
    NPC,
    Character,
    CommitReport,
    Delta,
    Lead,
    MoodState,
    RelationshipCategory,
    Verdict,
    VerdictKind,
    WorldFact,
    from_row,
    to_row,
)
from .similarity import LexicalSimilarity, Similarity

LEAD_TRANSITIONS: dict[str, frozenset[str]] = {
    "unheard": frozenset({"rumored", "abandoned"}),
    "rumored": frozenset({"accepted", "abandoned"}),
    "accepted": frozenset({"in_progress", "failed", "abandoned"}),
    "in_progress": frozenset({"complicated", "resolved", "failed", "abandoned"}),
    "complicated": frozenset({"in_progress", "resolved", "failed", "abandoned"}),
    "resolved": frozenset(),
    "failed": frozenset(),
    "abandoned": frozenset(),
}

# Bounds and thresholds (documented above; overridable — see ConflictPolicy and
# the Validator constructor's stat_bounds).
MOOD_FLOOR, MOOD_CEILING = -1.0, 1.0
RELATIONSHIP_FLOOR, RELATIONSHIP_CEILING = -100.0, 100.0
DEFAULT_STAT_BOUNDS = (-10.0, 30.0)
HP_KEY = "hp"
MAX_HP_KEY = "max_hp"
CURRENCY_KEY = "currency"
CURRENCY_ALIASES = ("gold",)
DECAY_CLASSES = frozenset({"durable", "slow", "fast"})
# No flat default: untagged relationship deltas derive their class from the
# applied magnitude in ``_apply_relationship`` (decision 1a, 2026-09-14).
REINFORCE_SIMILARITY = 0.85  # >= this against an existing fact: reinforce, don't duplicate
CONFLICT_SIM = 0.30  # similarity floor for a conflict with an unpinned fact
CONFLICT_SIM_PINNED = 0.20  # ... pinned facts are protected more aggressively (bias)
CONFLICT_OVERLAP = 0.50  # content-token overlap floor, unpinned
CONFLICT_OVERLAP_PINNED = 0.34  # ... pinned


@dataclass(frozen=True)
class ConflictPolicy:
    """Thresholds for the contradiction heuristics (validator-overridable)."""

    reinforce_similarity: float = REINFORCE_SIMILARITY
    sim_floor: float = CONFLICT_SIM
    sim_floor_pinned: float = CONFLICT_SIM_PINNED
    overlap_floor: float = CONFLICT_OVERLAP
    overlap_floor_pinned: float = CONFLICT_OVERLAP_PINNED


DEFAULT_CONFLICT_POLICY = ConflictPolicy()

_EFFECT_KEY: dict[str, str] = {
    "relationship": "delta",
    "hp": "delta",
    "stat": "delta",
    "currency": "amount",
    "inventory_add": "qty",
    "inventory_remove": "qty",
}
_NPC_KEY_FORMAT = "npc:{id}"
_TARGET_RE = re.compile(r"^(?:npc|lead)[:_-]?(\d+)$", re.IGNORECASE)

_TOKEN_RE = re.compile(r"[a-z0-9']+")
# Same stopword set as similarity.py (kept in sync so overlap ratios track the
# similarity scorer), EXCEPT that single-character digits survive here — the
# numeric-conflict heuristic needs them.
_STOPWORDS = frozenset(
    "a an and are as at be by for from in is it of on or that the to was were with you your i he she they them his her their we our".split()
)
_NEGATORS = frozenset({
    "not", "no", "never", "none", "cannot", "cant", "can't", "isn't", "isnt",
    "wasn't", "wasnt", "aren't", "arent", "doesn't", "doesnt", "didn't",
    "didnt", "won't", "wont", "without", "nor", "neither",
})
_ANTONYMS: tuple[tuple[str, str], ...] = (
    ("dead", "alive"), ("dead", "living"), ("dead", "lives"), ("dead", "revived"),
    ("dead", "resurrected"), ("dead", "survived"), ("died", "alive"), ("died", "lives"),
    ("killed", "alive"), ("killed", "lives"), ("dead", "well"),
    ("locked", "unlocked"), ("open", "closed"),
    ("friend", "enemy"), ("ally", "enemy"), ("safe", "endangered"),
    ("destroyed", "intact"), ("lost", "found"), ("promised", "refused"),
    ("true", "false"), ("guilty", "innocent"),
)
# Unambiguous life-claiming words used by the revival-by-fiat rule (a fact that
# names a known-dead NPC and claims one of these is refused outright).
_REVIVE_WORDS = frozenset({
    "alive", "living", "revive", "revived", "revives", "resurrect",
    "resurrected", "resurrects", "survives", "survived",
})
_WORD_NUMBERS: dict[str, float] = {
    "one": 1, "two": 2, "three": 3, "four": 4, "five": 5, "six": 6,
    "seven": 7, "eight": 8, "nine": 9, "ten": 10, "eleven": 11, "twelve": 12,
}


# --------------------------------------------------------------------------- #
# Small helpers
# --------------------------------------------------------------------------- #

def _tokenize(text: str) -> list[str]:
    return [
        token for token in _TOKEN_RE.findall((text or "").lower())
        if token not in _STOPWORDS and (len(token) > 1 or token.isdigit())
    ]


def _clamp(value: float, low: float, high: float) -> float:
    return max(low, min(high, value))


def _round6(value: float) -> float:
    return round(float(value), 6)


def _as_number(value: Any) -> float | None:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return None
    number = float(value)
    if number != number or number in (float("inf"), float("-inf")):
        return None
    return number


def _as_int(value: Any) -> int | None:
    number = _as_number(value)
    if number is None or number != int(number):
        return None
    return int(number)


def _target_number(target: str) -> int | None:
    text = str(target or "").strip()
    if text.isdigit():
        return int(text)
    match = _TARGET_RE.match(text)
    return int(match.group(1)) if match else None


def _as_delta(raw: Any) -> Delta | None:
    """Normalize a Delta or a mapping (e.g. decoded JSON) to a Delta."""
    if isinstance(raw, Delta):
        return raw
    if isinstance(raw, Mapping):
        data = raw.get("data")
        return Delta(
            kind=str(raw.get("kind") or ""),
            target=str(raw.get("target") or ""),
            data=dict(data) if isinstance(data, Mapping) else {},
            reason=str(raw.get("reason") or ""),
        )
    return None


def _accept(delta: Delta, note: str) -> Verdict:
    return Verdict(kind=VerdictKind.ACCEPTED.value, delta=delta, note=note)


def _clamped(delta: Delta, clamped_to: Any, note: str) -> Verdict:
    return Verdict(kind=VerdictKind.CLAMPED.value, delta=delta, note=note, clamped_to=clamped_to)


def _reject(delta: Delta, note: str) -> Verdict:
    return Verdict(kind=VerdictKind.REJECTED.value, delta=delta, note=note)


def _overlap_ratio(a: Sequence[str], b: Sequence[str]) -> float:
    set_a, set_b = set(a), set(b)
    if not set_a or not set_b:
        return 0.0
    return len(set_a & set_b) / min(len(set_a), len(set_b))


def _number_anchors(tokens: Sequence[str]) -> dict[str, float]:
    """``value -> anchor word`` for numbers in ``tokens`` ("3 guards" -> guards: 3)."""
    anchors: dict[str, float] = {}
    for index, token in enumerate(tokens):
        if token.isdigit():
            value = float(token)
        elif token in _WORD_NUMBERS:
            value = float(_WORD_NUMBERS[token])
        else:
            continue
        anchor = ""
        if index + 1 < len(tokens):
            anchor = tokens[index + 1]
        elif index > 0:
            anchor = tokens[index - 1]
        if anchor and anchor not in anchors:
            anchors[anchor] = value
    return anchors


def conflict_reason(
    new_statement: str,
    existing_statement: str,
    *,
    pinned: bool,
    sim: Similarity | None = None,
    policy: ConflictPolicy = DEFAULT_CONFLICT_POLICY,
) -> str | None:
    """Why ``new_statement`` conflicts with ``existing_statement`` (None = no conflict).

    Deterministic heuristics, gated by similarity + shared-subject overlap and
    then requiring an actual conflict marker: a numeric mismatch on the same
    anchor ("3 guards" vs "5 guards"), a negation flip, or an antonym pair
    (dead/alive, locked/unlocked, ...). Pinned existing facts use lower gates —
    canon is protected harder (the "bias pinned" rule). Statements that are
    merely similar (no marker) are NOT conflicts; exact/near duplicates are
    handled separately as reinforcement.
    """
    scorer = sim or LexicalSimilarity()
    tokens_new, tokens_existing = _tokenize(new_statement), _tokenize(existing_statement)
    similarity = scorer.score(new_statement, existing_statement)
    sim_floor = policy.sim_floor_pinned if pinned else policy.sim_floor
    overlap_floor = policy.overlap_floor_pinned if pinned else policy.overlap_floor
    if similarity < sim_floor or _overlap_ratio(tokens_new, tokens_existing) < overlap_floor:
        return None
    numeric = _number_anchors(tokens_new)
    other_numeric = _number_anchors(tokens_existing)
    for anchor, value in numeric.items():
        if anchor in other_numeric and other_numeric[anchor] != value:
            return f"numeric conflict on {anchor!r} ({value:g} vs {other_numeric[anchor]:g})"
    negated_new = any(token in _NEGATORS for token in tokens_new)
    negated_existing = any(token in _NEGATORS for token in tokens_existing)
    if negated_new != negated_existing:
        return "negation flip"
    tokens_new_set, tokens_existing_set = set(tokens_new), set(tokens_existing)
    for left, right in _ANTONYMS:
        if left in tokens_new_set and right in tokens_existing_set:
            return f"antonym pair {left!r}/{right!r}"
        if right in tokens_new_set and left in tokens_existing_set:
            return f"antonym pair {right!r}/{left!r}"
    return None


def _pick_character_row(rows: list[dict], target: str, player_id: str) -> tuple[dict | None, str]:
    """-> (characters row | None, why-not note). See the module docstring (Targets)."""
    if not rows:
        return None, "no character row exists in the store"
    text = str(target or "").strip()
    number = _target_number(text)
    if number is not None:
        row = next((r for r in rows if r["id"] == number), None)
        return (row, "") if row is not None else (None, f"no character with id {number}")
    if text.lower() in {"", player_id.lower()}:
        row = next((r for r in rows if str(r["id"]) == player_id), rows[0])
        return row, ""
    row = next((r for r in rows if str(r.get("name") or "").strip().lower() == text.lower()), None)
    return (row, "") if row is not None else (None, f"no character named {text!r}")


# --------------------------------------------------------------------------- #
# Per-call batch state
# --------------------------------------------------------------------------- #

class _TurnContext:
    """Running state for one ``validate``/``commit`` pass.

    Caches store reads and applies the deltas accepted so far in the batch, so
    a single turn proposing several related deltas is checked as one coherent
    change (spend-then-spend, unheard->rumored->accepted, ...).
    """

    def __init__(self, validator: Validator, turn: int) -> None:
        self.validator = validator
        self.turn = turn
        self.store = validator.store
        self.characters: dict[int, Character] = {}
        self.character_ids: list[int] | None = None
        self.npcs_loaded = False
        self.npc_rows: dict[int, dict] = {}
        self.npc_names: dict[str, dict] = {}
        self.moods: dict[str, tuple[float, float, float, float]] = {}
        self.relationships: dict[str, dict[str, float]] = {}
        self.leads: dict[int, Lead] = {}
        self.facts: list[WorldFact] | None = None
        self.batch_facts: list[tuple[str, bool]] = []
        self.fact_inserts = 0
        self.facts_this_turn: int | None = None

    # -- npcs ---------------------------------------------------------------
    def _load_npcs(self) -> None:
        if self.npcs_loaded or self.store is None:
            return
        self.npcs_loaded = True
        for row in self.store.find("npcs", order_by="id"):
            self.npc_rows[row["id"]] = row
            name = str(row.get("name") or "").strip().lower()
            if name:
                self.npc_names.setdefault(name, row)

    def resolve_npc(self, target: str) -> tuple[dict | None, str]:
        """-> (npcs row | None, canonical npc key)."""
        text = str(target or "").strip()
        number = _target_number(text)
        self._load_npcs()
        row: dict | None = None
        if number is not None:
            row = self.npc_rows.get(number)
        else:
            row = self.npc_names.get(text.lower())
        key = _NPC_KEY_FORMAT.format(id=row["id"]) if row is not None else text
        return row, key

    def dead_npcs(self) -> list[NPC]:
        self._load_npcs()
        return [from_row(NPC, row) for row in self.npc_rows.values() if not row.get("alive", 1)]

    # -- characters ---------------------------------------------------------
    def character(self, target: str) -> tuple[Character | None, str]:
        """-> (working Character copy | None, why-not note)."""
        if self.store is None:
            return None, "no store supplied; character rules cannot be checked"
        rows = self.store.find("characters", order_by="id")
        if self.character_ids is None:
            self.character_ids = [row["id"] for row in rows]
        row, why = _pick_character_row(rows, target, str(self.validator.config.player_id))
        if row is None:
            return None, why
        row_id = int(row["id"])
        if row_id not in self.characters:
            self.characters[row_id] = from_row(Character, row)
        return self.characters[row_id], ""

    # -- mood ---------------------------------------------------------------
    def mood_state(self, npc_key: str, npc_row: dict) -> tuple[float, float, float, float]:
        """-> (valence, arousal, baseline_valence, baseline_arousal)."""
        if npc_key in self.moods:
            return self.moods[npc_key]
        row = self.store.find_one("moods", {"npc_id": npc_key}) if self.store is not None else None
        if row is not None:
            state = from_row(MoodState, row)
            current = (state.valence, state.arousal, state.baseline_valence, state.baseline_arousal)
        else:
            personality = npc_row.get("personality") or {}
            if isinstance(personality, str):  # raw row handed in before decoding
                personality = {}
            baseline_valence = _as_number(personality.get("baseline_valence")) or 0.0
            baseline_arousal = _as_number(personality.get("baseline_arousal")) or 0.0
            current = (baseline_valence, baseline_arousal, baseline_valence, baseline_arousal)
        self.moods[npc_key] = current
        return current

    def set_mood(self, npc_key: str, valence: float, arousal: float) -> None:
        _v, _a, base_v, base_a = self.moods[npc_key]
        self.moods[npc_key] = (valence, arousal, base_v, base_a)

    # -- relationships ------------------------------------------------------
    def relationship_current(self, npc_key: str, category: str, npc_row: dict) -> float:
        cached = self.relationships.setdefault(npc_key, {})
        if category in cached:
            return cached[category]
        value = self._read_relationship_current(npc_key, category, npc_row)
        cached[category] = value
        return value

    def _read_relationship_current(self, npc_key: str, category: str, npc_row: dict) -> float:
        # Preferred: R3's decayed ledger math once memory.py is implemented.
        try:
            from .memory import RelationshipLedger

            ledger = RelationshipLedger(self.store, self.validator.config)
            currents = ledger.currents(
                npc_id=npc_key, player_id=str(self.validator.config.player_id), turn=self.turn
            )
            value = _as_number(currents.get(category))
            if value is not None:
                return value
        except (ImportError, NotImplementedError, AttributeError, TypeError, ValueError, KeyError):
            pass
        # Fallback: disposition anchor + raw ledger sum (undecayed upper bound).
        base = _as_number(npc_row.get("disposition_base")) or 0.0
        total = 0.0
        if self.store is not None:
            for row in self.store.find(
                "relationship_ledger",
                {"npc_id": npc_key, "player_id": str(self.validator.config.player_id)},
            ):
                total += _as_number(row.get("delta")) or 0.0
        return base + total

    def set_relationship(self, npc_key: str, category: str, value: float) -> None:
        self.relationships.setdefault(npc_key, {})[category] = value

    # -- leads --------------------------------------------------------------
    def lead(self, target: str) -> Lead | None:
        number = _target_number(target)
        if number is None:
            return None
        if number not in self.leads:
            row = self.store.find_one("leads", {"id": number}) if self.store is not None else None
            if row is None:
                return None
            self.leads[number] = from_row(Lead, row)
        return self.leads[number]

    # -- facts --------------------------------------------------------------
    def all_facts(self) -> list[WorldFact]:
        if self.facts is None:
            self.facts = [
                from_row(WorldFact, row)
                for row in (self.store.find("world_facts", order_by="id") if self.store else [])
            ]
        return self.facts

    def conflicts(self, statement: str) -> list[tuple[int | None, str, str]]:
        """-> [(fact_id | None for batch-only facts, existing statement, reason)]."""
        found: list[tuple[int | None, str, str]] = []
        policy = self.validator.policy
        for fact in self.all_facts():
            reason = conflict_reason(
                statement, fact.statement, pinned=bool(fact.pinned),
                sim=self.validator.sim, policy=policy,
            )
            if reason:
                found.append((fact.id, fact.statement, reason))
        for batch_statement, batch_pinned in self.batch_facts:
            reason = conflict_reason(
                statement, batch_statement, pinned=batch_pinned,
                sim=self.validator.sim, policy=policy,
            )
            if reason:
                found.append((None, batch_statement, f"{reason} (same turn)"))
        return found

    def duplicate_of(self, statement: str) -> WorldFact | None:
        """Existing fact this statement near-duplicates (reinforce, not insert)."""
        threshold = self.validator.policy.reinforce_similarity
        best: tuple[float, WorldFact] | None = None
        for fact in self.all_facts():
            score = self.validator.sim.score(statement, fact.statement)
            if score >= threshold and (best is None or score > best[0]):
                best = (score, fact)
        return best[1] if best else None

    def facts_committed_this_turn(self) -> int:
        if self.facts_this_turn is None:
            self.facts_this_turn = (
                self.store.count("world_facts", {"established_turn": self.turn})
                if self.store is not None
                else 0
            )
        return self.facts_this_turn


# --------------------------------------------------------------------------- #
# Validator
# --------------------------------------------------------------------------- #

class Validator:
    """The only writer of model-proposed state (spec §6)."""

    def __init__(
        self,
        store: Any,
        config: EngineConfig | None = None,
        *,
        sim: Similarity | None = None,
        stat_bounds: tuple[float, float] | None = None,
        policy: ConflictPolicy | None = None,
    ) -> None:
        self.store = store
        self.config = config or EngineConfig()
        self.sim = sim or LexicalSimilarity()
        self.stat_bounds = tuple(stat_bounds) if stat_bounds else DEFAULT_STAT_BOUNDS
        self.policy = policy or DEFAULT_CONFLICT_POLICY
        self._handlers = {
            "mood": self._mood,
            "relationship": self._relationship,
            "fact": self._fact,
            "lead_transition": self._lead_transition,
            "inventory_add": self._inventory_add,
            "inventory_remove": self._inventory_remove,
            "currency": self._currency,
            "hp": self._hp,
            "stat": self._stat,
            "status_add": self._status_add,
            "status_remove": self._status_remove,
        }

    # -- validate -----------------------------------------------------------
    def validate(self, deltas: list[Delta], *, turn: int) -> list[Verdict]:
        """Check every proposed delta; NOTHING is written here."""
        context = _TurnContext(self, turn)
        verdicts: list[Verdict] = []
        for raw in deltas:
            delta = _as_delta(raw)
            if delta is None:
                verdicts.append(_reject(Delta(), f"not a Delta: {raw!r}"))
                continue
            verdicts.append(self._validate_one(delta, context))
        return verdicts

    def _validate_one(self, delta: Delta, context: _TurnContext) -> Verdict:
        if delta.kind not in DELTA_KINDS:
            return _reject(delta, f"unknown delta kind {delta.kind!r}; known kinds: {sorted(DELTA_KINDS)}")
        missing = DELTA_REQUIRED[delta.kind] - set(delta.data or {})
        if missing:
            return _reject(
                delta,
                f"{delta.kind} delta is missing required key(s): {', '.join(sorted(missing))}",
            )
        return self._handlers[delta.kind](delta, context)

    # -- per-kind validation ------------------------------------------------
    def _mood(self, delta: Delta, context: _TurnContext) -> Verdict:
        target = str(delta.target or "").strip()
        if not target:
            return _reject(delta, "mood delta needs a target npc_id")
        npc_row, npc_key = context.resolve_npc(target)
        if npc_row is None:
            return _reject(delta, f"unknown NPC {target!r}: mood changes require a known npc row")
        if not npc_row.get("alive", 1):
            return _reject(delta, f"NPC {npc_key} is dead; a dead NPC cannot receive mood changes")
        valence = _as_number(delta.data.get("valence_delta"))
        arousal = _as_number(delta.data.get("arousal_delta"))
        if valence is None or arousal is None:
            return _reject(delta, "valence_delta and arousal_delta must both be numbers")
        current_v, current_a, _base_v, _base_a = context.mood_state(npc_key, npc_row)
        applied_v = _clamp(valence, MOOD_FLOOR, MOOD_CEILING)
        applied_a = _clamp(arousal, MOOD_FLOOR, MOOD_CEILING)
        new_v = _clamp(current_v + applied_v, MOOD_FLOOR, MOOD_CEILING)
        new_a = _clamp(current_a + applied_a, MOOD_FLOOR, MOOD_CEILING)
        applied_v, applied_a = _round6(new_v - current_v), _round6(new_a - current_a)
        context.set_mood(npc_key, new_v, new_a)
        note = (
            f"{npc_key}: valence {current_v:+.3f} -> {new_v:+.3f} (applied {applied_v:+.3f}), "
            f"arousal {current_a:+.3f} -> {new_a:+.3f} (applied {applied_a:+.3f})"
        )
        if applied_v != valence or applied_a != arousal:
            return _clamped(
                delta,
                {"valence_delta": applied_v, "arousal_delta": applied_a},
                note + "; clamped to the per-event bound and the [-1, 1] mood range",
            )
        return _accept(delta, note + "; within the [-1, 1] mood range")

    def _relationship(self, delta: Delta, context: _TurnContext) -> Verdict:
        target = str(delta.target or "").strip()
        if not target:
            return _reject(delta, "relationship delta needs a target npc_id")
        npc_row, npc_key = context.resolve_npc(target)
        if npc_row is None:
            return _reject(delta, f"unknown NPC {target!r}: relationship changes require a known npc row")
        if not npc_row.get("alive", 1):
            return _reject(delta, f"NPC {npc_key} is dead; a dead NPC cannot receive relationship changes")
        category = str(delta.data.get("category") or "").strip().lower()
        known = sorted(category_.value for category_ in RelationshipCategory)
        if category not in known:
            return _reject(delta, f"unknown relationship category {category!r}; known: {known}")
        value = _as_number(delta.data.get("delta"))
        if value is None:
            return _reject(delta, "relationship delta must be a number (the CHANGE, not the value)")
        bound = _as_number(self.config.memory.relationship_event_bound) or 0.0
        current = context.relationship_current(npc_key, category, npc_row)
        applied = _round6(_clamp(value, -bound, bound))
        reasons = []
        if applied != value:
            reasons.append(f"per-event bound is ±{bound:g}")
        if applied > 0:
            capped = min(applied, max(0.0, RELATIONSHIP_CEILING - current))
        else:
            capped = max(applied, min(0.0, RELATIONSHIP_FLOOR - current))
        capped = _round6(capped)
        if capped != applied:
            reasons.append(f"{npc_key} {category} is already at {current:.2f} of ±100")
        applied = capped
        new_value = _round6(current + applied)
        context.set_relationship(npc_key, category, new_value)
        note = (
            f"{npc_key} {category}: {current:.2f} -> {new_value:.2f} (applied {applied:+.2f} "
            f"of proposed {value:+.2f}); reason {delta.data.get('reason') or '(none given)'}"
        )
        if reasons:
            return _clamped(delta, applied, note + "; clamped: " + "; ".join(reasons))
        return _accept(delta, note + "; within the per-event bound")

    def _fact(self, delta: Delta, context: _TurnContext) -> Verdict:
        statement = str(delta.data.get("statement") or "").strip()
        if not statement:
            return _reject(delta, "fact delta needs a non-empty statement")
        source = str(delta.data.get("source") or "").strip()
        if source:
            source_row, _source_key = context.resolve_npc(source)
            if source_row is not None and not source_row.get("alive", 1):
                return _reject(
                    delta,
                    f"fact source {source!r} is a dead NPC; dead NPCs cannot source new facts",
                )
        revival = self._revival_by_fiat(statement, context)
        if revival:
            return _reject(delta, revival)
        conflicts = context.conflicts(statement)
        if conflicts:
            rendered = "; ".join(
                f"#{fact_id} ({reason})" if fact_id is not None else f"new fact from this turn ({reason})"
                for fact_id, _statement, reason in conflicts
            )
            return _reject(delta, f"contradicts existing canon: {rendered}")
        duplicate = context.duplicate_of(statement)
        if duplicate is not None:
            return _accept(
                delta,
                f"near-duplicate of fact #{duplicate.id}; commit will reinforce it instead of inserting",
            )
        used = context.facts_committed_this_turn() + context.fact_inserts
        limit = int(self.config.memory.fact_rate_limit_per_turn)
        if used >= limit:
            return _reject(
                delta,
                f"fact rate limit reached ({limit} new facts per turn; "
                f"{context.facts_committed_this_turn()} already committed this turn)",
            )
        context.fact_inserts += 1
        context.batch_facts.append((statement, bool(delta.data.get("pinned", False))))
        return _accept(
            delta,
            f"new fact accepted ({context.fact_inserts}/{limit} this turn), "
            f"pinned={bool(delta.data.get('pinned', False))}",
        )

    def _revival_by_fiat(self, statement: str, context: _TurnContext) -> str | None:
        tokens = set(_tokenize(statement))
        if not (tokens & _REVIVE_WORDS):
            return None
        for npc in context.dead_npcs():
            name_tokens = set(_tokenize(npc.name))
            if name_tokens and name_tokens <= tokens:
                return (
                    f"revival by fiat: {npc.name!r} is dead (alive = 0) and the statement "
                    f"claims they are alive; death is permanent without a resurrection mechanic"
                )
        return None

    def _lead_transition(self, delta: Delta, context: _TurnContext) -> Verdict:
        target = str(delta.target or "").strip()
        if not target:
            return _reject(delta, "lead_transition delta needs a target lead_id")
        new_stage = str(delta.data.get("new_stage") or "").strip().lower()
        if new_stage not in LEAD_TRANSITIONS:
            return _reject(
                delta,
                f"unknown lead stage {new_stage!r}; known stages: {sorted(LEAD_TRANSITIONS)}",
            )
        lead = context.lead(target)
        if lead is None:
            return _reject(delta, f"unknown lead {target!r}: transitions require an existing lead row")
        current = str(lead.stage or "").strip().lower()
        if current not in LEAD_TRANSITIONS:
            return _reject(delta, f"lead #{lead.id} has an unknown current stage {lead.stage!r}")
        if new_stage == current:
            return _reject(delta, f"lead #{lead.id} is already {current!r}; nothing to transition")
        if new_stage not in LEAD_TRANSITIONS[current]:
            allowed = sorted(LEAD_TRANSITIONS[current]) or ["(terminal)"]
            return _reject(
                delta,
                f"illegal lead transition {current!r} -> {new_stage!r} for lead #{lead.id}; "
                f"allowed from {current!r}: {allowed}",
            )
        lead.stage = new_stage
        trigger = str(delta.data.get("justification") or "").strip()
        return _accept(
            delta,
            f"lead #{lead.id} {current!r} -> {new_stage!r}"
            + (f"; justification: {trigger}" if trigger else ""),
        )

    def _currency(self, delta: Delta, context: _TurnContext) -> Verdict:
        amount = _as_int(delta.data.get("amount"))
        if amount is None:
            return _reject(delta, "currency amount must be an integer (negative spends)")
        character, why = context.character(delta.target)
        if character is None:
            return _reject(delta, f"currency cannot be checked: {why}")
        key = CURRENCY_KEY if CURRENCY_KEY in character.stats else next(
            (alias for alias in CURRENCY_ALIASES if alias in character.stats), CURRENCY_KEY
        )
        balance = _as_int(character.stats.get(key)) or 0
        if balance + amount < 0:
            return _reject(
                delta,
                f"conservation: cannot apply {amount:+d} to a balance of {balance} (character "
                f"#{character.id}, {key!r}); the spend is refused, not partially applied",
            )
        character.stats[key] = balance + amount
        return _accept(
            delta,
            f"character #{character.id} {key}: {balance} -> {balance + amount} (applied {amount:+d})",
        )

    def _inventory_add(self, delta: Delta, context: _TurnContext) -> Verdict:
        item_id = str(delta.target or "").strip()
        if not item_id:
            return _reject(delta, "inventory_add delta needs a target item_id")
        qty = _as_int(delta.data.get("qty"))
        if qty is None or qty <= 0:
            return _reject(delta, f"inventory_add qty must be a positive integer (got {delta.data.get('qty')!r})")
        character, why = context.character(delta.data.get("character_id") or "")
        if character is None:
            return _reject(delta, f"inventory cannot be checked: {why}")
        flags = delta.data.get("flags") if isinstance(delta.data.get("flags"), Mapping) else None
        for entry in character.inventory:
            if str(entry.get("item_id")) == item_id:
                held = _as_int(entry.get("qty")) or 0
                entry["qty"] = held + qty
                if flags:
                    entry["flags"] = dict(flags)
                return _accept(
                    delta, f"character #{character.id} {item_id}: {held} -> {held + qty} (added {qty})"
                )
        character.inventory.append({"item_id": item_id, "qty": qty, "flags": dict(flags or {})})
        return _accept(delta, f"character #{character.id} gained {qty} x {item_id} (new entry)")

    def _inventory_remove(self, delta: Delta, context: _TurnContext) -> Verdict:
        item_id = str(delta.target or "").strip()
        if not item_id:
            return _reject(delta, "inventory_remove delta needs a target item_id")
        qty = _as_int(delta.data.get("qty"))
        if qty is None or qty <= 0:
            return _reject(
                delta, f"inventory_remove qty must be a positive integer (got {delta.data.get('qty')!r})"
            )
        character, why = context.character(delta.data.get("character_id") or "")
        if character is None:
            return _reject(delta, f"inventory cannot be checked: {why}")
        for index, entry in enumerate(character.inventory):
            if str(entry.get("item_id")) != item_id:
                continue
            held = _as_int(entry.get("qty")) or 0
            if held < qty:
                return _reject(
                    delta,
                    f"conservation: character #{character.id} holds {held} x {item_id}, "
                    f"cannot remove {qty}; removals are refused, never partial",
                )
            remaining = held - qty
            if remaining == 0:
                character.inventory.pop(index)
                return _accept(
                    delta, f"character #{character.id} {item_id}: {held} -> 0 (entry removed)"
                )
            entry["qty"] = remaining
            return _accept(
                delta, f"character #{character.id} {item_id}: {held} -> {remaining} (removed {qty})"
            )
        return _reject(
            delta, f"conservation: character #{character.id} holds no {item_id!r}; removal refused"
        )

    def _hp(self, delta: Delta, context: _TurnContext) -> Verdict:
        change = _as_number(delta.data.get("delta"))
        if change is None:
            return _reject(delta, "hp delta must be a number")
        character, why = context.character(delta.target)
        if character is None:
            return _reject(delta, f"hp cannot be checked: {why}")
        if HP_KEY not in character.stats:
            return _reject(
                delta,
                f"character #{character.id} has no {HP_KEY!r} stat; refusing to invent a health pool",
            )
        current = _as_number(character.stats.get(HP_KEY)) or 0.0
        maximum = _as_number(character.stats.get(MAX_HP_KEY))
        ceiling = float("inf") if maximum is None else max(0.0, maximum)
        new_value = _clamp(current + change, 0.0, ceiling)
        applied = _round6(new_value - current)
        character.stats[HP_KEY] = new_value
        note = f"character #{character.id} hp: {current:g} -> {new_value:g} (applied {applied:+g})"
        if applied != change:
            limit = f"0..{maximum:g}" if maximum is not None else "floor 0"
            return _clamped(delta, applied, note + f"; clamped to the hp range {limit}")
        return _accept(delta, note + "; within the hp range")

    def _stat(self, delta: Delta, context: _TurnContext) -> Verdict:
        stat = str(delta.data.get("stat") or "").strip()
        if not stat:
            return _reject(delta, "stat delta needs a non-empty stat name")
        change = _as_number(delta.data.get("delta"))
        if change is None:
            return _reject(delta, "stat delta must be a number")
        character, why = context.character(delta.target)
        if character is None:
            return _reject(delta, f"stat cannot be checked: {why}")
        low, high = self.stat_bounds
        current = _as_number(character.stats.get(stat)) or 0.0
        new_value = _clamp(current + change, low, high)
        applied = _round6(new_value - current)
        character.stats[stat] = new_value
        note = (
            f"character #{character.id} {stat}: {current:g} -> {new_value:g} (applied {applied:+g})"
        )
        if applied != change:
            return _clamped(delta, applied, note + f"; clamped to the stat range [{low:g}, {high:g}]")
        return _accept(delta, note + f"; within the stat range [{low:g}, {high:g}]")

    def _status_add(self, delta: Delta, context: _TurnContext) -> Verdict:
        status = str(delta.data.get("status") or "").strip()
        if not status:
            return _reject(delta, "status_add needs a non-empty status name")
        character, why = context.character(delta.target)
        if character is None:
            return _reject(delta, f"status cannot be checked: {why}")
        if status in character.status_effects:
            return _accept(delta, f"character #{character.id} already has {status!r}; no change")
        character.status_effects.append(status)
        return _accept(delta, f"character #{character.id} gained status {status!r}")

    def _status_remove(self, delta: Delta, context: _TurnContext) -> Verdict:
        status = str(delta.data.get("status") or "").strip()
        if not status:
            return _reject(delta, "status_remove needs a non-empty status name")
        character, why = context.character(delta.target)
        if character is None:
            return _reject(delta, f"status cannot be checked: {why}")
        if status not in character.status_effects:
            return _reject(
                delta, f"character #{character.id} does not have {status!r}; nothing to remove"
            )
        character.status_effects.remove(status)
        return _accept(delta, f"character #{character.id} lost status {status!r}")

    # -- commit -------------------------------------------------------------
    def commit(self, verdicts: list[Verdict], *, turn: int) -> CommitReport:
        """Apply accepted+clamped verdicts; rejected deltas are never written."""
        if self.store is None:
            raise RuntimeError("Validator.commit requires a store")
        report = CommitReport()
        transaction = getattr(self.store, "transaction", None)
        manager: Any = transaction() if callable(transaction) else contextlib.nullcontext()
        with manager:
            for verdict in verdicts:
                if verdict.kind == VerdictKind.REJECTED.value:
                    report.rejected.append(verdict)
                    continue
                if verdict.kind not in (VerdictKind.ACCEPTED.value, VerdictKind.CLAMPED.value):
                    report.rejected.append(
                        Verdict(
                            kind=VerdictKind.REJECTED.value, delta=verdict.delta,
                            note=f"unknown verdict kind {verdict.kind!r}",
                        )
                    )
                    continue
                delta = verdict.delta
                if delta is None:
                    report.rejected.append(
                        Verdict(kind=VerdictKind.REJECTED.value, note="verdict carries no delta")
                    )
                    continue
                replacement = self._apply(delta, verdict, turn=turn)
                if replacement is not None and replacement.kind == VerdictKind.REJECTED.value:
                    report.rejected.append(replacement)
                elif replacement is not None:
                    report.accepted.append(replacement)
                elif verdict.kind == VerdictKind.CLAMPED.value:
                    report.clamped.append(verdict)
                else:
                    report.accepted.append(verdict)
        return report

    def _effective_data(self, delta: Delta, verdict: Verdict) -> dict:
        data = dict(delta.data or {})
        if verdict.kind != VerdictKind.CLAMPED.value or verdict.clamped_to is None:
            return data
        if delta.kind == "mood" and isinstance(verdict.clamped_to, Mapping):
            data["valence_delta"] = verdict.clamped_to.get("valence_delta", data.get("valence_delta"))
            data["arousal_delta"] = verdict.clamped_to.get("arousal_delta", data.get("arousal_delta"))
        elif delta.kind in _EFFECT_KEY:
            data[_EFFECT_KEY[delta.kind]] = verdict.clamped_to
        return data

    def _apply(self, delta: Delta, verdict: Verdict, *, turn: int) -> Verdict | None:
        """Write one accepted/clamped delta.

        Returns ``None`` when the delta was applied, else a REPLACEMENT verdict
        for the report: ``rejected`` (refused at commit) or ``accepted`` (a
        near-duplicate fact that reinforced instead of inserting).
        """
        data = self._effective_data(delta, verdict)
        handler = getattr(self, f"_apply_{delta.kind}", None)
        if handler is None:  # kinds without a commit surface (none today)
            return None
        refusal = handler(delta, data, turn=turn)
        if refusal is not None and not isinstance(refusal, Verdict):
            return _reject(delta, str(refusal))
        return refusal

    def _npc_key(self, target: Any) -> str:
        """Canonical ``npc:<id>`` key for a target (falls back to the raw text)."""
        text = str(target or "").strip()
        number = _target_number(text)
        row = None
        if self.store is not None:
            row = (
                self.store.find_one("npcs", {"id": number})
                if number is not None
                else self.store.find_one("npcs", {"name": text})
            )
        return _NPC_KEY_FORMAT.format(id=row["id"]) if row is not None else text

    def _apply_mood(self, delta: Delta, data: Mapping[str, Any], *, turn: int) -> Verdict | None:
        npc_key = self._npc_key(delta.target)
        existing = self.store.find_one("moods", {"npc_id": npc_key})
        if existing is not None:
            state = from_row(MoodState, existing)
            baseline_valence, baseline_arousal = state.baseline_valence, state.baseline_arousal
            current_v, current_a = state.valence, state.arousal
        else:
            npc_row = (
                self.store.find_one("npcs", {"id": _target_number(delta.target)})
                if _target_number(delta.target) is not None
                else self.store.find_one("npcs", {"name": str(delta.target)})
            )
            # rows from the store are raw columns: decode JSON via from_row
            personality = from_row(NPC, npc_row).personality if npc_row is not None else {}
            baseline_valence = _as_number(personality.get("baseline_valence")) or 0.0
            baseline_arousal = _as_number(personality.get("baseline_arousal")) or 0.0
            current_v, current_a = baseline_valence, baseline_arousal
        valence = _as_number(data.get("valence_delta")) or 0.0
        arousal = _as_number(data.get("arousal_delta")) or 0.0
        self.store.upsert(
            "moods",
            to_row(MoodState(
                npc_id=npc_key,
                valence=_clamp(current_v + valence, MOOD_FLOOR, MOOD_CEILING),
                arousal=_clamp(current_a + arousal, MOOD_FLOOR, MOOD_CEILING),
                baseline_valence=baseline_valence,
                baseline_arousal=baseline_arousal,
                last_updated_turn=turn,
            )),
            conflict="npc_id",
        )
        return None

    def _apply_relationship(self, delta: Delta, data: Mapping[str, Any], *, turn: int) -> Verdict | None:
        value = _as_number(data.get("delta")) or 0.0
        decay_class = str(data.get("decay_class") or "").strip().lower()
        if decay_class not in DECAY_CLASSES:
            # Decision 1a (2026-09-14): no valid tag -> derive from the APPLIED
            # (post-clamp) magnitude, the same rule memory.append_delta uses.
            # Explicit tags still win; durable/fast are reachable in play again.
            from .memory import RelationshipLedger

            decay_class = RelationshipLedger.default_decay_class(value)
        self.store.insert("relationship_ledger", {
            "npc_id": self._npc_key(delta.target),
            "player_id": str(self.config.player_id),
            "turn": turn,
            "delta": value,
            "reason": str(data.get("reason") or delta.reason or ""),
            "category": str(data.get("category") or "").strip().lower(),
            "decay_class": decay_class,
        })
        return None

    def _apply_fact(self, delta: Delta, data: Mapping[str, Any], *, turn: int) -> Verdict | None:
        statement = str(data.get("statement") or "").strip()
        context = _TurnContext(self, turn)
        conflicts = context.conflicts(statement)
        if conflicts:
            rendered = "; ".join(
                f"#{fact_id} ({reason})" if fact_id is not None else f"new fact from this turn ({reason})"
                for fact_id, _statement, reason in conflicts
            )
            return _reject(delta, f"commit-time contradiction check: {rendered}")
        duplicate = context.duplicate_of(statement)
        if duplicate is not None:
            return _accept(
                delta, f"reinforced existing fact #{duplicate.id} (no duplicate inserted)"
            )
        tags = data.get("tags")
        self.store.insert("world_facts", to_row(WorldFact(
            statement=statement,
            pinned=bool(data.get("pinned", False)),
            established_turn=turn,
            source=str(data.get("source") or delta.reason or ""),
            tags=list(tags) if isinstance(tags, (list, tuple)) else [],
            contradicts=[],
        )))
        return None

    def _apply_lead_transition(self, delta: Delta, data: Mapping[str, Any], *, turn: int) -> Verdict | None:
        lead_id = _target_number(delta.target)
        row = self.store.find_one("leads", {"id": lead_id}) if lead_id is not None else None
        if row is None:
            return _reject(delta, f"lead {delta.target!r} disappeared before commit")
        lead = from_row(Lead, row)
        lead.stage = str(data.get("new_stage") or "").strip().lower()
        lead.stage_history = [
            *lead.stage_history,
            {
                "stage": lead.stage,
                "turn": turn,
                "trigger": str(data.get("justification") or delta.reason or ""),
            },
        ]
        self.store.update("leads", lead.id, to_row(lead))
        return None

    def _apply_currency(self, delta: Delta, data: Mapping[str, Any], *, turn: int) -> Verdict | None:
        character = self._reload_character(delta.target)
        if character is None:
            return _reject(delta, f"currency target {delta.target!r} is not a character at commit time")
        key = CURRENCY_KEY if CURRENCY_KEY in character.stats else next(
            (alias for alias in CURRENCY_ALIASES if alias in character.stats), CURRENCY_KEY
        )
        balance = _as_int(character.stats.get(key)) or 0
        character.stats[key] = balance + (_as_int(data.get("amount")) or 0)
        self._write_character(character)
        return None

    def _apply_hp(self, delta: Delta, data: Mapping[str, Any], *, turn: int) -> Verdict | None:
        character = self._reload_character(delta.target)
        if character is None:
            return _reject(delta, f"hp target {delta.target!r} is not a character at commit time")
        if HP_KEY not in character.stats:
            return _reject(delta, f"character #{character.id} has no {HP_KEY!r} stat")
        current = _as_number(character.stats.get(HP_KEY)) or 0.0
        maximum = _as_number(character.stats.get(MAX_HP_KEY))
        ceiling = float("inf") if maximum is None else max(0.0, maximum)
        character.stats[HP_KEY] = _clamp(
            current + (_as_number(data.get("delta")) or 0.0), 0.0, ceiling
        )
        self._write_character(character)
        return None

    def _apply_stat(self, delta: Delta, data: Mapping[str, Any], *, turn: int) -> Verdict | None:
        character = self._reload_character(delta.target)
        if character is None:
            return _reject(delta, f"stat target {delta.target!r} is not a character at commit time")
        stat = str(data.get("stat") or "").strip()
        low, high = self.stat_bounds
        current = _as_number(character.stats.get(stat)) or 0.0
        character.stats[stat] = _clamp(current + (_as_number(data.get("delta")) or 0.0), low, high)
        self._write_character(character)
        return None

    def _apply_inventory_add(self, delta: Delta, data: Mapping[str, Any], *, turn: int) -> Verdict | None:
        character = self._reload_character(data.get("character_id") or "")
        if character is None:
            return _reject(delta, "inventory_add target is not a character at commit time")
        item_id = str(delta.target or "").strip()
        qty = _as_int(data.get("qty")) or 0
        for entry in character.inventory:
            if str(entry.get("item_id")) == item_id:
                entry["qty"] = (_as_int(entry.get("qty")) or 0) + qty
                flags = data.get("flags")
                if isinstance(flags, Mapping) and flags:
                    entry["flags"] = dict(flags)
                self._write_character(character)
                return None
        flags = data.get("flags")
        character.inventory.append({
            "item_id": item_id, "qty": qty,
            "flags": dict(flags) if isinstance(flags, Mapping) else {},
        })
        self._write_character(character)
        return None

    def _apply_inventory_remove(self, delta: Delta, data: Mapping[str, Any], *, turn: int) -> Verdict | None:
        character = self._reload_character(data.get("character_id") or "")
        if character is None:
            return _reject(delta, "inventory_remove target is not a character at commit time")
        item_id = str(delta.target or "").strip()
        qty = _as_int(data.get("qty")) or 0
        for index, entry in enumerate(character.inventory):
            if str(entry.get("item_id")) != item_id:
                continue
            remaining = (_as_int(entry.get("qty")) or 0) - qty
            if remaining <= 0:
                character.inventory.pop(index)
            else:
                entry["qty"] = remaining
            self._write_character(character)
            return None
        return _reject(delta, f"character holds no {item_id!r} at commit time")

    def _apply_status_add(self, delta: Delta, data: Mapping[str, Any], *, turn: int) -> Verdict | None:
        character = self._reload_character(delta.target)
        if character is None:
            return _reject(delta, f"status target {delta.target!r} is not a character at commit time")
        status = str(data.get("status") or "").strip()
        if status not in character.status_effects:
            character.status_effects.append(status)
        self._write_character(character)
        return None

    def _apply_status_remove(self, delta: Delta, data: Mapping[str, Any], *, turn: int) -> Verdict | None:
        character = self._reload_character(delta.target)
        if character is None:
            return _reject(delta, f"status target {delta.target!r} is not a character at commit time")
        status = str(data.get("status") or "").strip()
        if status not in character.status_effects:
            return _reject(delta, f"character #{character.id} does not have {status!r} at commit time")
        character.status_effects.remove(status)
        self._write_character(character)
        return None

    def _reload_character(self, target: Any) -> Character | None:
        """Fresh character row (commit must never reuse validate-time copies)."""
        if self.store is None:
            return None
        rows = self.store.find("characters", order_by="id")
        row, _why = _pick_character_row(rows, str(target or ""), str(self.config.player_id))
        return from_row(Character, row) if row is not None else None

    def _write_character(self, character: Character) -> None:
        self.store.update("characters", character.id, to_row(character))

    # -- contradiction surface ---------------------------------------------
    def check_contradiction(self, statement: str, *, exclude_fact_id: int | None = None) -> list[int]:
        """Fact ids that conflict with ``statement`` (empty = no conflict)."""
        return [fact_id for fact_id, _text, _reason in self.contradiction_details(
            statement, exclude_fact_id=exclude_fact_id
        )]

    def contradiction_details(self, statement: str, *, exclude_fact_id: int | None = None) -> list[tuple[int, str, str]]:
        """``[(fact_id, existing statement, reason)]`` — the WHY behind check_contradiction."""
        if self.store is None:
            return []
        details: list[tuple[int, str, str]] = []
        for row in self.store.find("world_facts", order_by="id"):
            fact = from_row(WorldFact, row)
            if exclude_fact_id is not None and fact.id == exclude_fact_id:
                continue
            reason = conflict_reason(
                statement, fact.statement, pinned=bool(fact.pinned),
                sim=self.sim, policy=self.policy,
            )
            if reason:
                details.append((fact.id, fact.statement, reason))
        return details
