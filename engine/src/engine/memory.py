"""Memory subsystem — six stores + world facts (spec §3).

Contract (R3 card). All persistence goes through ``Store``'s generic row API;
scoring/decay logic is deterministic and lives here. Salience is recomputed
lazily at retrieval time (spec §3.1), never stored as truth.

Store classes:
- NPCMemoryStore  — per-NPC entries + salience retrieval (top-K per NPC present)
- Chronicle       — structured short-term tail (~12) + verbatim last 2-3 turns
- RelationshipLedger — append-only deltas + decayed current values + reasons
- MoodTracker     — transient valence/arousal, half-life decay (spec §3.7)
- SagaDigest      — hierarchical session -> arc -> campaign, archived, injectable
- FactStore       — world facts: pinned, auto-pin rules, contradiction scan

Auto-pin (spec §3.6): promises, deaths, betrayals, and explicit
player-established facts get ``pinned=True`` via ``auto_pin_worthy``.

Conventions this module pins (callers depend on them; all are deterministic):

* **NPC keys.** Every component stores and looks up NPCs under the canonical
  ``"npc:<id>"`` key (same convention as ``validate.py``), accepting ``"<id>"``,
  ``"npc:<id>"``/``"npc-<id>"`` or an exact NPC name on the way in. Two
  spellings of one NPC can never produce two rows.
* **Salience** (spec §3.1): ``w1 * exp(-age / half_life) + w2 * |sentiment| +
  w3 * best_context_score(statement, scene) + w4 * log1p(reinforced_count) -
  w5 * (1 - exp(-decay_rate * age))`` with ``age = turn - turn_established``.
  The decay penalty is the *lost fraction* of an entry's weight, so a decay
  rate of 0 (``factual``) costs nothing: "a fact doesn't fade just because
  it's old" (spec §3.1) while emotional entries decay unless reinforced.
* **Retrieval**: top-K **per NPC** (never a global top-N across NPCs — spec
  §3.1's fix for "everything gets dumped every turn"). K defaults to
  ``config.memory.npc_top_k``; ties break by most recent establishment, then id.
* **Reinforce semantics** (NPC memory): ``reinforce`` bumps
  ``reinforced_count`` on every entry whose statement is a near-match
  (lexical similarity >= ``NEAR_MATCH_SIMILARITY``), which raises salience
  through the ``w4 * log1p`` term; ``retrieve`` sets ``last_referenced_turn``
  on the entries it returns (audit/decay bookkeeping — scoring only reads
  ``turn_established``). ``add`` itself always inserts: confirmation is the
  explicit ``reinforce`` call, not an implicit dedupe.
* **Verbatim window** (spec §3.2): only the newest
  ``config.memory.chronicle_verbatim`` chronicle entries keep
  ``verbatim_text``; older rows are nulled (structure is retained forever).
* **Relationship decay classes**: ``append_delta`` derives the class from the
  magnitude when the caller does not pass one — ``|delta| >= 25`` is
  ``durable`` (betrayals don't fade), ``10 <= |delta| < 25`` is ``slow``,
  everything smaller is ``fast``. Decay is exponential with the per-class
  per-turn rate from ``config.memory.relationship_decay_per_turn``:
  ``delta * exp(-rate * age)``. Currents are returned per category (each
  meter = the anchor + that category's decayed sum, so ``validate``'s ±100
  clamp sees the NPC's baseline) plus a ``total`` (the anchor counted once
  plus every category's decayed sum); the ledger rows themselves are never
  rewritten (audit trail, spec §3.5).
* **Mood** (spec §3.7): transient values decay toward the NPC's *personality*
  baseline with a half-life (``config.memory.mood_half_life_turns``); a mood
  spike moves mood only — it never appends to the relationship ledger.
  ``ensure`` seeds the baseline from the NPC's personality when the caller
  does not override it.
* **Saga**: every level is derived from the *primary sources* (chronicle
  entries + pinned facts), never from re-summarizing an existing summary —
  repeated summarization is lossy and compounding (spec §3.3). Higher levels
  exist to *bound injected size*, not to reword lower ones. Pinned facts are
  quoted verbatim and referenced by id at every level, exempt from the
  summarizer. The archive is append-only: nothing is ever discarded.
* **World facts** (spec §3.6): ``add`` applies ``auto_pin_worthy`` when the
  caller passes ``pinned=None`` and reinforces (returns the existing id
  instead of inserting) when the statement near-duplicates an existing fact —
  the same semantics as ``Validator._apply_fact``. ``contradiction_scan``
  reuses the validator's conflict heuristics so both sides always agree.
"""
from __future__ import annotations

import math
import re
from typing import Any

from .config import EngineConfig
from .models import (
    NPC,
    ChronicleEntry,
    MemoryType,
    MoodState,
    NPCMemoryEntry,
    RelationshipCategory,
    SagaLevel,
    SagaRow,
    WorldFact,
    from_row,
    to_row,
)
from .similarity import LexicalSimilarity, Similarity, best_context_score

# --------------------------------------------------------------------------- #
# Thresholds and cadence (module constants — no config knobs exist for these)
# --------------------------------------------------------------------------- #

# NPC-memory reinforce threshold; kept equal to validate.REINFORCE_SIMILARITY
# (pinned by a test) so "near-match" means the same thing in both modules.
NEAR_MATCH_SIMILARITY = 0.85
# World-fact near-duplicate threshold; matches validate.REINFORCE_SIMILARITY.
FACT_REINFORCE_SIMILARITY = 0.85
# Ledger decay-class derivation from |delta| (documented above).
DURABLE_DELTA_MIN = 25.0
SLOW_DELTA_MIN = 10.0
DECAY_CLASSES = frozenset({"durable", "slow", "fast"})
RELATIONSHIP_CATEGORIES = frozenset(category.value for category in RelationshipCategory)
MEMORY_TYPES = frozenset(memory_type.value for memory_type in MemoryType)
DEFAULT_DECAY_RATE = 0.15  # unknown/plain entry types decay like observations

# Saga hierarchy: an arc rolls up this many completed sessions (spec §3.3's
# "arc boundaries" can also be forced with ``close_arc``).
SESSIONS_PER_ARC = 3
# Event lines kept per level (middle-clipped when a span overflows).
SESSION_EVENT_CAP = 24
ARC_EVENT_CAP = 12
CAMPAIGN_EVENT_CAP = 8
# Scene-relevance floors for pulling arc/session detail into a prompt.
SAGA_ARC_DETAIL_FLOOR = 0.08
SAGA_SESSION_DETAIL_FLOOR = 0.12

MOOD_FLOOR, MOOD_CEILING = -1.0, 1.0
NPC_KEY_FORMAT = "npc:{id}"
LEDGER_DESCRIBE_LIMIT = 3  # reasons shown by RelationshipLedger.describe

# --------------------------------------------------------------------------- #
# Auto-pin heuristics (spec §3.6: promises, deaths, betrayals, player canon)
# --------------------------------------------------------------------------- #

_PROMISE_PHRASES = (
    r"\bpromis(?:e|es|ed|ing)\b",
    r"\bvow(?:s|ed|ing)?\b",
    r"\boath(?:s)?\b",
    r"\bswore\b",
    r"\bsworn\b",
    r"\bswear\b",
    r"\bpledg(?:e|es|ed|ing)\b",
    r"\b(?:my|his|her|their|your|the player's) word\b",
    r"\bword of honor\b",
)
_DEATH_PHRASES = (
    r"\bdi(?:e|es|ed)\b",
    r"\bdead\b",
    r"\bdeath(?:s)?\b",
    r"\bkill(?:s|ed|ing)?\b",
    r"\bmurd(?:er|ers|ered|ering)\b",
    r"\bslain\b",
    r"\bslay\b",
    r"\bslew\b",
    r"\bexecuted\b",
    r"\bcorpse(?:s)?\b",
    r"\bperish(?:ed|es)?\b",
    r"\bassassinat(?:e|ed|es|ion)\b",
)
_BETRAYAL_PHRASES = (
    r"\bbetray(?:s|ed|al|als|ing)?\b",
    r"\bbackstab(?:s|bed|bing)?\b",
    r"\bdouble[-_\s]?cross(?:ed|es|ing)?\b",
    r"\bturned on\b",
    r"\bsold (?:him|her|them|us|me) out\b",
)
_PLAYER_ESTABLISHED_PHRASES = (
    r"\bthe player (?:declared|stated|insisted|established|vowed|promised|swore)\b",
    r"\bplayer[-_\s]?stated\b",
)
_PROMISE_RE = re.compile("|".join(_PROMISE_PHRASES), re.IGNORECASE)
_DEATH_RE = re.compile("|".join(_DEATH_PHRASES), re.IGNORECASE)
_BETRAYAL_RE = re.compile("|".join(_BETRAYAL_PHRASES), re.IGNORECASE)
_PLAYER_ESTABLISHED_RE = re.compile("|".join(_PLAYER_ESTABLISHED_PHRASES), re.IGNORECASE)
# ``kind`` values that themselves establish player canon / a pinned promise.
_PLAYER_KINDS = frozenset({
    "player", "player_stated", "player-stated", "player_established",
    "player-established", "player_declared", "player-declared",
})
_PINNING_KINDS = frozenset({MemoryType.PROMISE.value})

_NPC_NUMBER_RE = re.compile(r"^(?:npc[:_-]?)?(\d+)$", re.IGNORECASE)


# --------------------------------------------------------------------------- #
# Shared helpers
# --------------------------------------------------------------------------- #

def _clamp(value: float, low: float, high: float) -> float:
    return max(low, min(high, value))


def _round6(value: float) -> float:
    return round(float(value), 6)


def _npc_number(npc_id: Any) -> int | None:
    """Numeric NPC id from ``"3"`` / ``"npc:3"`` / ``"npc-3"`` (else None)."""
    match = _NPC_NUMBER_RE.match(str(npc_id or "").strip())
    return int(match.group(1)) if match else None


def _npc_key(store: Any, npc_id: Any) -> str:
    """Canonical ``"npc:<id>"`` key; falls back to the input for unknown names."""
    text = str(npc_id or "").strip()
    number = _npc_number(text)
    if number is None and text and store is not None:
        row = store.find_one("npcs", {"name": text})
        number = int(row["id"]) if row is not None else None
    return NPC_KEY_FORMAT.format(id=number) if number is not None else text


def _npc_row(store: Any, npc_key: str) -> dict | None:
    number = _npc_number(npc_key)
    return store.find_one("npcs", {"id": number}) if number is not None else None


def _clip(items: list[str], cap: int) -> list[str]:
    """Middle-clip ``items`` to ``cap`` entries (head + marker + tail).

    Deterministic and content-preserving at both ends, so a long span keeps
    its opening and its resolution; the marker records how much was elided.
    """
    if cap <= 0:
        return []
    if len(items) <= cap:
        return list(items)
    head = (cap + 1) // 2
    tail = cap - head
    omitted = len(items) - cap
    return [*items[:head], f"... {omitted} more ...", *items[len(items) - tail:]]


def auto_pin_worthy(statement: str, *, kind: str | None = None) -> bool:
    """Promises, deaths, betrayals, explicit player-establishments -> True (§3.6).

    ``kind`` is the fact's origin/kind — ``"player"`` (or its variants) means
    the player explicitly established the fact (source ``player`` on a ``fact``
    delta), and a ``promise`` memory kind pins outright. Otherwise the
    statement itself is scanned for the load-bearing patterns a human GM would
    never forget: promises/vows/oaths, deaths/kills, betrayals. Deterministic,
    model-free; over-pinning is cheap (pinned facts are short and the cost of
    missing one is a continuity break the player will notice — spec §3.6).
    """
    text = str(statement or "")
    if not text.strip():
        return False
    kind_value = str(kind or "").strip().lower().replace(" ", "_")
    if kind_value in _PLAYER_KINDS or kind_value in _PINNING_KINDS:
        return True
    return any(
        pattern.search(text)
        for pattern in (_PROMISE_RE, _DEATH_RE, _BETRAYAL_RE, _PLAYER_ESTABLISHED_RE)
    )


# --------------------------------------------------------------------------- #
# 3.1 NPC memory
# --------------------------------------------------------------------------- #

class NPCMemoryStore:
    def __init__(self, store: Any, config: EngineConfig | None = None,
                 sim: Similarity | None = None) -> None:
        self.store = store
        self.config = config or EngineConfig()
        self.sim = sim or LexicalSimilarity()

    def add(self, *, npc_id: str, turn: int, statement: str,
            type: str = MemoryType.OBSERVED.value, sentiment: float = 0.0,
            decay_rate: float | None = None) -> int:
        """Record one thing this NPC experienced (returns the new row id).

        ``decay_rate`` defaults from ``config.salience.decay_rates`` by ``type``
        (``factual`` ~ 0: facts don't fade; grievance/kindness/observed decay);
        ``sentiment`` is clamped to [-1, 1]. ``add`` always inserts — confirming
        an existing memory again is ``reinforce``.
        """
        npc_key = _npc_key(self.store, npc_id)
        memory_type = str(type or "").strip().lower()
        if memory_type not in MEMORY_TYPES:
            raise ValueError(
                f"unknown memory type {type!r}; known: {sorted(MEMORY_TYPES)}"
            )
        text = str(statement or "").strip()
        if not text:
            raise ValueError("NPC memory entries need a non-empty statement")
        rate = (
            self.decay_rate_for(memory_type)
            if decay_rate is None
            else max(0.0, float(decay_rate))
        )
        entry = NPCMemoryEntry(
            npc_id=npc_key,
            turn_established=int(turn),
            statement=text,
            type=memory_type,
            sentiment=_clamp(float(sentiment), -1.0, 1.0),
            decay_rate=rate,
            reinforced_count=0,
            last_referenced_turn=0,
        )
        return int(self.store.insert("npc_memory", to_row(entry)))

    def score(self, entry: NPCMemoryEntry, *, scene_context: list[str], turn: int) -> float:
        """Spec §3.1 formula: w1*recency + w2*|sentiment| + w3*relevance
        + w4*log1p(reinforced) - w5*decay(turns_since_established, decay_rate).

        recency = ``exp(-age / recency_half_life_turns)``;
        relevance = ``best_context_score(statement, scene_context)``;
        decay = ``1 - exp(-decay_rate * age)`` — the *lost* fraction, so
        ``decay_rate == 0`` (factual) costs nothing. Recomputed lazily at
        retrieval time; never stored as truth (spec §3.1).
        """
        weights = self.config.salience
        age = max(0, int(turn) - int(entry.turn_established))
        half_life = float(weights.recency_half_life_turns)
        recency = math.exp(-age / half_life) if half_life > 0 else (1.0 if age == 0 else 0.0)
        relevance = best_context_score(entry.statement, scene_context, self.sim)
        reinforced = float(weights.w_reinforced) * math.log1p(max(0, int(entry.reinforced_count)))
        decay = 1.0 - math.exp(-max(0.0, float(entry.decay_rate)) * age)
        score = (
            float(weights.w_recency) * recency
            + float(weights.w_sentiment) * abs(float(entry.sentiment))
            + float(weights.w_relevance) * relevance
            + reinforced
            - float(weights.w_decay) * decay
        )
        return _round6(score)

    def retrieve(self, *, npc_id: str, scene_context: list[str], turn: int,
                 k: int | None = None) -> list[tuple[NPCMemoryEntry, float]]:
        """Top-K entries for THIS npc by salience; updates last_referenced_turn
        for returned entries. K defaults to config.memory.npc_top_k.

        Scoping is per NPC (spec §3.1): entries of other NPCs are never
        eligible, no matter how salient. Ties break by most recent
        establishment, then row id, so retrieval is fully deterministic.
        ``k <= 0`` returns nothing and touches nothing.
        """
        limit = int(self.config.memory.npc_top_k) if k is None else int(k)
        if limit <= 0:
            return []
        npc_key = _npc_key(self.store, npc_id)
        entries = [
            from_row(NPCMemoryEntry, row)
            for row in self.store.find("npc_memory", {"npc_id": npc_key})
        ]
        scored = [
            (entry, self.score(entry, scene_context=scene_context, turn=turn))
            for entry in entries
        ]
        scored.sort(key=lambda pair: (-pair[1], -pair[0].turn_established, pair[0].id))
        top = scored[:limit]
        for entry, _score in top:
            if entry.last_referenced_turn != turn:
                self.store.update("npc_memory", entry.id, {"last_referenced_turn": int(turn)})
                entry.last_referenced_turn = int(turn)
        return top

    def reinforce(self, *, npc_id: str, statement: str, turn: int) -> int:
        """Bump reinforced_count for existing entries matching ``statement``
        (returns how many were reinforced). Used when a fact is confirmed again.

        A "match" is lexical similarity >= ``NEAR_MATCH_SIMILARITY`` against the
        statement (rephrasings below that threshold are new observations); every
        match is bumped by one and has ``last_referenced_turn`` refreshed.
        Reinforcement raises future salience through the w4*log1p term.
        """
        npc_key = _npc_key(self.store, npc_id)
        text = str(statement or "").strip()
        count = 0
        for row in self.store.find("npc_memory", {"npc_id": npc_key}):
            entry = from_row(NPCMemoryEntry, row)
            if self.sim.score(text, entry.statement) < NEAR_MATCH_SIMILARITY:
                continue
            self.store.update("npc_memory", entry.id, {
                "reinforced_count": int(entry.reinforced_count) + 1,
                "last_referenced_turn": int(turn),
            })
            count += 1
        return count

    def decay_rate_for(self, type: str) -> float:
        return float(self.config.salience.decay_rates.get(
            str(type or "").strip().lower(), DEFAULT_DECAY_RATE))


# --------------------------------------------------------------------------- #
# 3.2 Chronicle tail
# --------------------------------------------------------------------------- #

class Chronicle:
    def __init__(self, store: Any, config: EngineConfig | None = None) -> None:
        self.store = store
        self.config = config or EngineConfig()

    def append(self, *, turn_id: int, actor: str, action_summary: str,
               mechanical_result: str = "", consequence_oneliner: str = "",
               verbatim_text: str | None = None) -> int:
        """Append one structured entry; enforces the verbatim window.

        Storage is unbounded (structure is cheap and is the saga's primary
        source); only the *injected* window is bounded, via ``tail``. After the
        insert, verbatim text older than the newest
        ``config.memory.chronicle_verbatim`` entries is nulled (spec §3.2).
        """
        entry = ChronicleEntry(
            turn_id=int(turn_id),
            actor=str(actor or ""),
            action_summary=str(action_summary or ""),
            mechanical_result=str(mechanical_result or ""),
            consequence_oneliner=str(consequence_oneliner or ""),
            verbatim_text=verbatim_text,
        )
        row_id = int(self.store.insert("chronicle", to_row(entry)))
        self.enforce_verbatim_window()
        return row_id

    def tail(self, *, n: int | None = None) -> list[ChronicleEntry]:
        """Newest-last chronological window (default config.memory.chronicle_window).

        Returns structured entries; ``verbatim_text`` is non-null only for the
        most recent ``config.memory.chronicle_verbatim`` entries (older rows are
        nulled in storage, so the tail carries no stale prose).
        """
        window = int(self.config.memory.chronicle_window) if n is None else int(n)
        if window <= 0:
            return []
        rows = self._ordered()
        return [from_row(ChronicleEntry, row) for row in rows[max(0, len(rows) - window):]]

    def enforce_verbatim_window(self) -> None:
        """Null out verbatim_text older than the last N entries (spec §3.2).

        N = ``config.memory.chronicle_verbatim``; the window is counted in
        entries (one per turn in the normal flow). Rows are never deleted.
        """
        keep_n = max(0, int(self.config.memory.chronicle_verbatim))
        rows = self._ordered()
        keep = {row["id"] for row in (rows[-keep_n:] if keep_n else [])}
        for row in rows:
            if row["id"] in keep or row.get("verbatim_text") is None:
                continue
            self.store.update("chronicle", row["id"], {"verbatim_text": None})

    def _ordered(self) -> list[dict]:
        """All chronicle rows, oldest-first by (turn_id, id), raw columns."""
        return sorted(
            self.store.find("chronicle"),
            key=lambda row: (int(row["turn_id"]), int(row["id"])),
        )


# --------------------------------------------------------------------------- #
# 3.5 Relationships — append-only ledger
# --------------------------------------------------------------------------- #

class RelationshipLedger:
    def __init__(self, store: Any, config: EngineConfig | None = None) -> None:
        self.store = store
        self.config = config or EngineConfig()

    def append_delta(self, *, npc_id: str, player_id: str, turn: int, delta: float,
                     reason: str, category: str, decay_class: str | None = None) -> int:
        """Append-only; decay_class defaults from the delta's magnitude.

        Default rule (documented): ``|delta| >= 25`` -> ``durable`` (betrayals
        and great debts don't fade), ``10 <= |delta| < 25`` -> ``slow``, below
        -> ``fast`` (minor gratitude/irritation fades quickly). The delta is
        clamped to ``config.memory.relationship_event_bound`` (the same
        per-event bound the validator enforces) and stored as the CHANGE, never
        as a value; the row is never rewritten afterwards.
        """
        npc_key = _npc_key(self.store, npc_id)
        category_value = str(category or "").strip().lower()
        if category_value not in RELATIONSHIP_CATEGORIES:
            raise ValueError(
                f"unknown relationship category {category!r}; "
                f"known: {sorted(RELATIONSHIP_CATEGORIES)}"
            )
        bound = float(self.config.memory.relationship_event_bound)
        value = _clamp(float(delta), -bound, bound)
        klass = str(decay_class or "").strip().lower() or self.default_decay_class(value)
        if klass not in DECAY_CLASSES:
            raise ValueError(
                f"unknown decay class {decay_class!r}; known: {sorted(DECAY_CLASSES)}"
            )
        return int(self.store.insert("relationship_ledger", {
            "npc_id": npc_key,
            "player_id": str(player_id or ""),
            "turn": int(turn),
            "delta": value,
            "reason": str(reason or ""),
            "category": category_value,
            "decay_class": klass,
        }))

    @staticmethod
    def default_decay_class(delta: float) -> str:
        """Magnitude rule from the docstring: >=25 durable, >=10 slow, else fast."""
        magnitude = abs(float(delta))
        if magnitude >= DURABLE_DELTA_MIN:
            return "durable"
        if magnitude >= SLOW_DELTA_MIN:
            return "slow"
        return "fast"

    def currents(self, *, npc_id: str, player_id: str, turn: int) -> dict[str, float]:
        """Per-category + "total" current meters (spec §3.5).

        Per-category meter = the NPC's ``disposition_base`` anchor (its stance
        before any ledger entry — an untouched category still carries it) plus
        that category's decayed deltas; ``validate``'s ±100 clamp reads these
        per-category values, so the anchor has to be inside them.
        ``"total"`` is the overall meter: the anchor counted once plus every
        category's decayed sum.

        Each delta decays as ``delta * exp(-rate * age)`` with the rate of its
        own decay class (``config.memory.relationship_decay_per_turn``); the
        anchor is constant. History is never lost — only the *current* value
        decays. The result always carries every category (the bare anchor when
        untouched) plus ``"total"``.
        """
        npc_key = _npc_key(self.store, npc_id)
        anchor = self._base_anchor(npc_key)
        decayed: dict[str, float] = {category: 0.0 for category in RELATIONSHIP_CATEGORIES}
        for row in self._rows(npc_key, player_id):
            age = max(0, int(turn) - int(row["turn"]))
            value = float(row["delta"]) * self._decay_factor(row.get("decay_class"), age)
            category = str(row.get("category") or "")
            decayed[category] = decayed.get(category, 0.0) + value
        out = {key: _round6(anchor + value) for key, value in decayed.items()}
        out["total"] = _round6(anchor + sum(decayed.values()))
        return out

    def describe(self, *, npc_id: str, player_id: str, turn: int) -> str:
        """Reason-based one-line relationship description (audit trail, spec §3.5).

        Walks the ledger and reports the strongest reasons behind the current
        meters — "why is Marla at -40?" answered from the rows themselves, not
        inferred from a bare number: each part names a category's current meter
        and, in parentheses, the ledger reasons that moved it. The anchor is
        named in the header when it is not zero. Deterministic: same ledger +
        turn -> same line.
        """
        npc_key = _npc_key(self.store, npc_id)
        name = self._npc_name(npc_key)
        rows = self._rows(npc_key, player_id)
        anchor = self._base_anchor(npc_key)
        if not rows:
            return f"{name}: total {anchor:+.1f} — no ledger entries yet"
        current = self.currents(npc_id=npc_key, player_id=player_id, turn=int(turn))
        contributions: dict[str, list[tuple[str, float]]] = {}
        for row in rows:
            age = max(0, int(turn) - int(row["turn"]))
            value = _round6(float(row["delta"]) * self._decay_factor(row.get("decay_class"), age))
            category = str(row.get("category") or "")
            reason = str(row.get("reason") or "") or "no reason recorded"
            contributions.setdefault(category, []).append((reason, value))
        ranked = sorted(
            contributions.items(),
            key=lambda item: (-abs(sum(value for _reason, value in item[1])), item[0]),
        )[:LEDGER_DESCRIBE_LIMIT]
        parts = []
        for category, entries in ranked:
            contribution = _round6(sum(value for _reason, value in entries))
            reasons = "; ".join(reason for reason, _value in entries)
            parts.append(
                f"{category} {current.get(category, 0.0):+.1f} ({reasons}, {contribution:+.1f})"
            )
        header = f"{name}: total {current['total']:+.1f}"
        if anchor:
            header += f" (anchor {anchor:+.1f})"
        return f"{header} — {'; '.join(parts)}"

    def _rows(self, npc_key: str, player_id: str) -> list[dict]:
        return self.store.find(
            "relationship_ledger",
            {"npc_id": npc_key, "player_id": str(player_id or "")},
            order_by="id",
        )

    def _decay_factor(self, decay_class: Any, age: int) -> float:
        rates = self.config.memory.relationship_decay_per_turn
        rate = float(rates.get(str(decay_class or "durable"), 0.0))
        return math.exp(-rate * age)

    def _base_anchor(self, npc_key: str) -> float:
        row = _npc_row(self.store, npc_key)
        return float(row.get("disposition_base") or 0.0) if row is not None else 0.0

    def _npc_name(self, npc_key: str) -> str:
        row = _npc_row(self.store, npc_key)
        return str(row.get("name") or npc_key) if row is not None else npc_key


# --------------------------------------------------------------------------- #
# 3.7 Moods — transient, half-life decay toward the personality baseline
# --------------------------------------------------------------------------- #

class MoodTracker:
    def __init__(self, store: Any, config: EngineConfig | None = None) -> None:
        self.store = store
        self.config = config or EngineConfig()

    def ensure(self, *, npc_id: str, baseline_valence: float | None = None,
               baseline_arousal: float | None = None) -> None:
        """Make sure an NPC has a mood row; baselines default from personality.

        Callers may override the baselines; when they don't (``None``), they are
        read from the NPC's static ``personality`` (``baseline_valence`` /
        ``baseline_arousal`` — spec §3.7: personality never changes at runtime).
        A fresh row starts *at* its baseline; an existing row only has its
        baselines refreshed, never its current valence/arousal.
        """
        npc_key = _npc_key(self.store, npc_id)
        base_v, base_a = self._baselines(npc_key, baseline_valence, baseline_arousal)
        row = self.store.find_one("moods", {"npc_id": npc_key})
        if row is None:
            self.store.insert("moods", to_row(MoodState(
                npc_id=npc_key,
                valence=base_v,
                arousal=base_a,
                baseline_valence=base_v,
                baseline_arousal=base_a,
                last_updated_turn=0,
            )))
            return
        state = from_row(MoodState, row)
        if state.baseline_valence != base_v or state.baseline_arousal != base_a:
            self.store.update("moods", state.id, {
                "baseline_valence": base_v,
                "baseline_arousal": base_a,
            })

    def apply(self, *, npc_id: str, valence_delta: float, arousal_delta: float,
              turn: int, cause: str = "") -> None:
        """Decay to now, then apply the spike; clamps [-1, 1]; persists.

        The stored value decays toward the baseline over
        ``config.memory.mood_half_life_turns`` turns since
        ``last_updated_turn``, then the deltas are added and the result is
        clamped and persisted with ``last_updated_turn = turn``. A mood spike
        is transient by construction: it moves ``moods`` only and never appends
        to the relationship ledger (spec §3.7). ``cause`` is provenance for the
        caller's log — moods carry no free-text column (the reason belongs on
        the ledger/chronicle).
        """
        npc_key = _npc_key(self.store, npc_id)
        state = self._load(npc_key)
        valence, arousal = self._decayed(state, turn)
        valence = _clamp(valence + float(valence_delta), MOOD_FLOOR, MOOD_CEILING)
        arousal = _clamp(arousal + float(arousal_delta), MOOD_FLOOR, MOOD_CEILING)
        self.store.upsert("moods", to_row(MoodState(
            npc_id=npc_key,
            valence=_round6(valence),
            arousal=_round6(arousal),
            baseline_valence=state.baseline_valence,
            baseline_arousal=state.baseline_arousal,
            last_updated_turn=int(turn),
        )), conflict="npc_id")

    def current(self, *, npc_id: str, turn: int) -> MoodState:
        """Decayed view (computed, not persisted) of the NPC's mood state.

        Reading never writes: an NPC without a moods row reports its
        personality baselines at ``last_updated_turn=0`` and no row is created.
        """
        npc_key = _npc_key(self.store, npc_id)
        row = self.store.find_one("moods", {"npc_id": npc_key})
        if row is None:
            base_v, base_a = self._baselines(npc_key, None, None)
            return MoodState(
                npc_id=npc_key,
                valence=base_v,
                arousal=base_a,
                baseline_valence=base_v,
                baseline_arousal=base_a,
                last_updated_turn=0,
            )
        state = from_row(MoodState, row)
        valence, arousal = self._decayed(state, turn)
        return MoodState(
            id=state.id,
            npc_id=npc_key,
            valence=_round6(valence),
            arousal=_round6(arousal),
            baseline_valence=state.baseline_valence,
            baseline_arousal=state.baseline_arousal,
            last_updated_turn=state.last_updated_turn,
        )

    # -- internals ---------------------------------------------------------
    def _load(self, npc_key: str) -> MoodState:
        """Stored state, ``ensure``-ing a row first when none exists yet."""
        row = self.store.find_one("moods", {"npc_id": npc_key})
        if row is None:
            self.ensure(npc_id=npc_key)
            row = self.store.find_one("moods", {"npc_id": npc_key})
        return from_row(MoodState, row)

    def _baselines(self, npc_key: str, valence: float | None,
                   arousal: float | None) -> tuple[float, float]:
        from_personality: tuple[float, float] | None = None
        row = _npc_row(self.store, npc_key)
        if row is not None:
            personality = from_row(NPC, row).personality or {}
            from_personality = (
                float(personality.get("baseline_valence") or 0.0),
                float(personality.get("baseline_arousal") or 0.0),
            )
        fallback_v, fallback_a = from_personality or (0.0, 0.0)
        return (
            fallback_v if valence is None else float(valence),
            fallback_a if arousal is None else float(arousal),
        )

    def _decayed(self, state: MoodState, turn: int) -> tuple[float, float]:
        """Half-life decay toward the baselines as of ``turn``."""
        half_life = float(self.config.memory.mood_half_life_turns)
        age = max(0, int(turn) - int(state.last_updated_turn))
        if age == 0:
            factor = 1.0
        elif half_life <= 0:
            factor = 0.0
        else:
            factor = 0.5 ** (age / half_life)
        valence = state.baseline_valence + (state.valence - state.baseline_valence) * factor
        arousal = state.baseline_arousal + (state.arousal - state.baseline_arousal) * factor
        return valence, arousal


# --------------------------------------------------------------------------- #
# 3.3 Saga digest — hierarchical, archived, model-free by default
# --------------------------------------------------------------------------- #

class SagaDigest:
    def __init__(self, store: Any, config: EngineConfig | None = None,
                 summarizer: Any = None, sim: Similarity | None = None) -> None:
        """``summarizer`` is an injectable callable(texts) -> str; the default
        deterministic builder needs no model (spec §3.3 notes the pass SHOULD
        use a cheap model when available — that is wired by R7, not required).

        Every level's text is built from the primary sources (chronicle
        entries + pinned facts) for that level's turn span, so summary drift
        cannot compound; ``summarizer`` only ever sees the source lines, never
        a finished summary. Pinned facts are appended verbatim, by id, outside
        the summarizer — exempt by construction. ``sim`` scores scene
        relevance for ``injectable`` (lexical by default).
        """
        self.store = store
        self.config = config or EngineConfig()
        self.summarizer = summarizer
        self.sim = sim or LexicalSimilarity()

    def maybe_summarize(self, *, turn: int, force: bool = False) -> list[SagaRow]:
        """Session-level summary every ~config.memory.session_summary_turns turns;
        arc/campaign rollups when a lower level completes. Returns new rows.

        Sessions cover consecutive ``session_summary_turns`` windows; a window
        with no chronicle content folds into the next summary — quiet stretches
        don't mint empty rows, and coverage stays gapless (a summary's span
        starts where the previous summary ended). ``force`` closes the current
        partial window (e.g. the player stopped mid-session). Arcs roll up
        every ``SESSIONS_PER_ARC`` completed sessions, and a campaign digest is
        rebuilt whenever an arc completes. Already-summarized content is never
        summarized twice (the archive itself is the cursor).
        """
        current_turn = int(turn)
        window = max(1, int(self.config.memory.session_summary_turns))
        sessions = self.archive_of(SagaLevel.SESSION.value)
        cursor = sessions[-1].created_turn if sessions else 0
        span_start = cursor
        created: list[SagaRow] = []
        while cursor + window <= current_turn:
            cursor += window
            fresh = self._summarize_session(lo=span_start, hi=cursor)
            if fresh:
                created.extend(fresh)
                span_start = cursor
        if force and current_turn > cursor:
            fresh = self._summarize_session(lo=span_start, hi=current_turn)
            if fresh:
                created.extend(fresh)
                span_start = current_turn
        if created:
            created.extend(self._rollup())
        return created

    def close_arc(self, *, turn: int) -> list[SagaRow]:
        """Force an arc rollup at a narrative boundary (quest resolved, act break).

        Rolls up every session completed since the last arc and rebuilds the
        campaign digest; returns the new rows (spec §3.3's narrative-boundary
        trigger, alongside the automatic every-N-sessions cadence).
        """
        created = self._rollup(final=True)
        return created

    def level_text(self, level: str) -> str | None:
        """Latest text for a level (campaign | arc | session)."""
        rows = self.archive_of(level)
        return rows[-1].text if rows else None

    def archive_of(self, level: str) -> list[SagaRow]:
        """ALL rows at a level — archive, newest last (never discarded)."""
        level_value = self._check_level(level)
        return [
            from_row(SagaRow, row)
            for row in self.store.find("saga_levels", {"level": level_value}, order_by="id")
        ]

    def injectable(self, *, scene_context: list[str], turn: int) -> list[SagaRow]:
        """Campaign digest by default; arc/session detail only when the scene
        scores as referencing that specific content (spec §3.3).

        Always the newest campaign digest (when one exists). Arc and session
        rows join it only if a scene-context string scores at/above the
        detail floors — i.e. the player just referenced something specific
        ("that promise from the salt mines"). Ordered campaign -> arc ->
        session, matching the prompt priority in spec §4. ``turn`` is accepted
        for call-site symmetry; selection is scene-driven, not recency-driven.
        Deterministic.
        """
        out: list[SagaRow] = []
        campaign = self.archive_of(SagaLevel.CAMPAIGN.value)
        if campaign:
            out.append(campaign[-1])
        contexts = [text for text in (scene_context or []) if text]
        if contexts:
            for level, floor in (
                (SagaLevel.ARC.value, SAGA_ARC_DETAIL_FLOOR),
                (SagaLevel.SESSION.value, SAGA_SESSION_DETAIL_FLOOR),
            ):
                rows = self.archive_of(level)
                if not rows:
                    continue
                best = max(
                    rows,
                    key=lambda row: (
                        best_context_score(row.text, contexts, self.sim),
                        row.id,
                    ),
                )
                if best_context_score(best.text, contexts, self.sim) >= floor:
                    out.append(best)
        return out

    # -- internals ---------------------------------------------------------

    def _summarize_session(self, *, lo: int, hi: int) -> list[SagaRow]:
        entries = self._entries_between(lo, hi)
        pinned = self._pinned_between(lo, hi)
        if not entries and not pinned:
            return []
        number = len(self.archive_of(SagaLevel.SESSION.value)) + 1
        row = SagaRow(
            level=SagaLevel.SESSION.value,
            scope_id=f"session-{number}",
            text=self._compose(
                level_label=f"Session {number}", lo=lo, hi=hi,
                lines=[self._render_entry(entry) for entry in entries],
                cap=SESSION_EVENT_CAP, pinned=pinned, references=[],
            ),
            created_turn=hi,
            references=[fact.id for fact in pinned],
        )
        row.id = int(self.store.insert("saga_levels", to_row(row)))
        return [row]

    def _rollup(self, *, final: bool = False) -> list[SagaRow]:
        """Arc rollups (a full group of SESSIONS_PER_ARC completed sessions, or
        everything pending when ``final`` closes an arc at a narrative
        boundary), followed by a campaign rebuild when an arc completed."""
        created: list[SagaRow] = []
        sessions = self.archive_of(SagaLevel.SESSION.value)
        arcs = self.archive_of(SagaLevel.ARC.value)
        while True:
            consumed = len(arcs) * SESSIONS_PER_ARC
            pending = len(sessions) - consumed
            if pending < SESSIONS_PER_ARC and not (final and pending > 0):
                break
            number = len(arcs) + 1
            group = sessions[consumed:consumed + SESSIONS_PER_ARC]
            lo = sessions[consumed - 1].created_turn if consumed > 0 else 0
            hi = group[-1].created_turn
            pinned = self._pinned_between(lo, hi)
            scopes = [saga.scope_id for saga in group]
            row = SagaRow(
                level=SagaLevel.ARC.value,
                scope_id=f"arc-{number}",
                text=self._compose(
                    level_label=f"Arc {number}", lo=lo, hi=hi,
                    lines=[
                        self._render_entry(entry)
                        for entry in self._entries_between(lo, hi)
                    ],
                    cap=ARC_EVENT_CAP, pinned=pinned, references=scopes,
                ),
                created_turn=hi,
                references=[*scopes, *(fact.id for fact in pinned)],
            )
            row.id = int(self.store.insert("saga_levels", to_row(row)))
            created.append(row)
            arcs.append(row)
        if created:
            created.extend(self._rebuild_campaign(hi=created[-1].created_turn))
        return created

    def _rebuild_campaign(self, *, hi: int) -> list[SagaRow]:
        arcs = self.archive_of(SagaLevel.ARC.value)
        pinned = self._pinned_between(0, hi)
        row = SagaRow(
            level=SagaLevel.CAMPAIGN.value,
            scope_id=SagaLevel.CAMPAIGN.value,
            text=self._compose(
                level_label="Campaign", lo=0, hi=hi,
                lines=[
                    self._render_entry(entry) for entry in self._entries_between(0, hi)
                ],
                cap=CAMPAIGN_EVENT_CAP, pinned=pinned,
                references=[saga.scope_id for saga in arcs],
            ),
            created_turn=hi,
            references=[*(saga.scope_id for saga in arcs),
                        *(fact.id for fact in pinned)],
        )
        row.id = int(self.store.insert("saga_levels", to_row(row)))
        return [row]

    def _compose(self, *, level_label: str, lo: int, hi: int, lines: list[str],
                 cap: int, pinned: list[WorldFact], references: list[str]) -> str:
        header = f"{level_label} (turns {lo + 1}-{hi}): "
        source_lines = _clip(lines, cap)
        if self.summarizer is not None:
            summary = str(self.summarizer(list(source_lines)) or "").strip()
            body = summary or self._default_summary(source_lines)
        else:
            body = self._default_summary(source_lines)
        text = header + body
        if pinned:
            quoted = "; ".join(f"#{fact.id} {fact.statement}" for fact in pinned)
            text += f" | Pinned facts (exempt): {quoted}"
        if references:
            text += f" | rolls up: {', '.join(references)}"
        return text

    @staticmethod
    def _default_summary(lines: list[str]) -> str:
        return "; ".join(lines) if lines else "quiet stretch"

    @staticmethod
    def _render_entry(entry: ChronicleEntry) -> str:
        line = f"T{entry.turn_id} {entry.actor}: {entry.action_summary}".rstrip()
        if entry.mechanical_result:
            line += f" [{entry.mechanical_result}]"
        if entry.consequence_oneliner:
            line += f" -> {entry.consequence_oneliner}"
        return line

    def _entries_between(self, lo: int, hi: int) -> list[ChronicleEntry]:
        rows = self.store.find("chronicle")
        entries = [
            from_row(ChronicleEntry, row) for row in rows
            if lo < int(row["turn_id"]) <= hi
        ]
        entries.sort(key=lambda entry: (entry.turn_id, entry.id))
        return entries

    def _pinned_between(self, lo: int, hi: int) -> list[WorldFact]:
        facts = [
            from_row(WorldFact, row)
            for row in self.store.find("world_facts", {"pinned": 1}, order_by="id")
            if lo < int(row["established_turn"]) <= hi
        ]
        return facts

    @staticmethod
    def _check_level(level: str) -> str:
        value = str(level or "").strip().lower()
        known = {item.value for item in SagaLevel}
        if value not in known:
            raise ValueError(f"unknown saga level {level!r}; known: {sorted(known)}")
        return value

    @property
    def sim_score(self) -> Similarity:
        """Similarity used for detail selection (lexical; deterministic)."""
        return LexicalSimilarity()


# --------------------------------------------------------------------------- #
# 3.6 Pinned facts / world facts
# --------------------------------------------------------------------------- #

class FactStore:
    def __init__(self, store: Any, config: EngineConfig | None = None,
                 sim: Similarity | None = None) -> None:
        self.store = store
        self.config = config or EngineConfig()
        self.sim = sim or LexicalSimilarity()

    def add(self, *, statement: str, turn: int, source: str = "", tags: list | None = None,
            pinned: bool | None = None) -> int:
        """Insert a fact. ``pinned=None`` -> auto-pin decision via auto_pin_worthy.
        Near-duplicates reinforce instead of duplicating (see spec §3.6).

        Returns the new fact id, or the existing fact's id when the statement
        near-duplicates it (similarity >= ``FACT_REINFORCE_SIMILARITY``) — the
        same "reinforced, no duplicate inserted" semantics as
        ``Validator._apply_fact``. World facts carry no counter column, so
        reinforcement leaves the existing row untouched.
        """
        text = str(statement or "").strip()
        if not text:
            raise ValueError("world facts need a non-empty statement")
        origin = str(source or "")
        existing = self._duplicate_of(text)
        if existing is not None:
            return int(existing.id)
        decide = auto_pin_worthy(text, kind=origin) if pinned is None else bool(pinned)
        fact = WorldFact(
            statement=text,
            pinned=decide,
            established_turn=int(turn),
            source=origin,
            tags=[str(tag) for tag in (tags or [])],
            contradicts=[],
        )
        return int(self.store.insert("world_facts", to_row(fact)))

    def pinned_facts(self) -> list[WorldFact]:
        return [
            from_row(WorldFact, row)
            for row in self.store.find("world_facts", {"pinned": 1}, order_by="id")
        ]

    def retrieve(self, *, scene_context: list[str], turn: int,
                 limit: int | None = None) -> list[WorldFact]:
        """Pinned facts above min relevance ALWAYS eligible; others ranked.

        Pinned facts pass when scene relevance >= ``config.memory.min_pin_relevance``
        (with no scene context at all, every pinned fact passes — nothing to
        argue against, and the cost of missing one is high). Unpinned facts are
        ranked by scene relevance and only surface when the scene actually
        shares content with them; they are retrievable, never force-included
        (spec §3.6). ``limit`` truncates the ordered result — pinned first, so
        a short limit can never crowd out canon.
        """
        facts = [
            from_row(WorldFact, row) for row in self.store.find("world_facts", order_by="id")
        ]
        contexts = [text for text in (scene_context or []) if text]
        floor = float(self.config.memory.min_pin_relevance)

        def relevance(fact: WorldFact) -> float:
            return (
                best_context_score(fact.statement, contexts, self.sim)
                if contexts else 0.0
            )

        pinned = [
            fact for fact in facts
            if fact.pinned and (not contexts or relevance(fact) >= floor)
        ]
        others = [
            fact for fact in facts
            if not fact.pinned and contexts and relevance(fact) > 0.0
        ]
        pinned.sort(key=lambda fact: (-relevance(fact), -fact.established_turn, fact.id))
        others.sort(key=lambda fact: (-relevance(fact), -fact.established_turn, fact.id))
        ordered = [*pinned, *others]
        return ordered if limit is None else ordered[: int(limit)]

    def contradiction_scan(self, statement: str, *, exclude_fact_id: int | None = None) -> list[int]:
        """Ids of existing facts conflicting with ``statement`` (spec §3.6).

        Uses the validator's conflict heuristics (similarity + shared-subject
        overlap, then a conflict marker: numeric mismatch, negation flip or
        antonym pair) so ``FactStore`` and ``Validator.check_contradiction``
        can never disagree about what canon a statement breaks. Pinned facts
        are protected more aggressively (lower gates). ``exclude_fact_id``
        leaves the statement's own row out of the scan.
        """
        # local import: R2 owns the heuristics; avoids a module cycle
        from .validate import conflict_reason

        found: list[int] = []
        for row in self.store.find("world_facts", order_by="id"):
            fact = from_row(WorldFact, row)
            if exclude_fact_id is not None and fact.id == exclude_fact_id:
                continue
            if conflict_reason(
                statement, fact.statement, pinned=bool(fact.pinned), sim=self.sim,
            ):
                found.append(int(fact.id))
        return found

    def _duplicate_of(self, statement: str) -> WorldFact | None:
        best: tuple[float, WorldFact] | None = None
        for row in self.store.find("world_facts", order_by="id"):
            fact = from_row(WorldFact, row)
            score = self.sim.score(statement, fact.statement)
            if score >= FACT_REINFORCE_SIMILARITY and (best is None or score > best[0]):
                best = (score, fact)
        return best[1] if best else None


# --------------------------------------------------------------------------- #
# Bundle
# --------------------------------------------------------------------------- #

class MemoryBundle:
    """Wires every memory component to one store — the surface context.py and
    the pipeline consume. Tests may substitute a double implementing the same
    method surface (memory is built in parallel with context; see
    engine/ARCHITECTURE.md ownership)."""

    def __init__(self, *, npc_memory: NPCMemoryStore, chronicle: Chronicle,
                 ledger: RelationshipLedger, moods: MoodTracker,
                 saga: SagaDigest, facts: FactStore) -> None:
        self.npc_memory = npc_memory
        self.chronicle = chronicle
        self.ledger = ledger
        self.moods = moods
        self.saga = saga
        self.facts = facts

    @classmethod
    def build(cls, store: Any, config: EngineConfig | None = None,
              sim: Similarity | None = None, summarizer: Any = None) -> MemoryBundle:
        return cls(
            npc_memory=NPCMemoryStore(store, config, sim),
            chronicle=Chronicle(store, config),
            ledger=RelationshipLedger(store, config),
            moods=MoodTracker(store, config),
            saga=SagaDigest(store, config, summarizer, sim),
            facts=FactStore(store, config, sim),
        )
