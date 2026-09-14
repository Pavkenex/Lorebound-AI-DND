"""Frozen cross-module data contracts for the rebuilt engine.

Everything that crosses module boundaries — state entities, narrator replies,
mechanical outcomes, provider wire shapes — is defined HERE. Module
implementations import from this file and must not redefine these shapes
locally. Contract changes are allowed only when required; call them out
explicitly in your completion notes (see engine/ARCHITECTURE.md).
"""
from __future__ import annotations

import json
from collections.abc import Mapping
from dataclasses import dataclass, field, fields
from enum import StrEnum
from typing import Any

# --------------------------------------------------------------------------- #
# Enums
# --------------------------------------------------------------------------- #

class LeadStage(StrEnum):
    UNHEARD = "unheard"
    RUMORED = "rumored"
    ACCEPTED = "accepted"
    IN_PROGRESS = "in_progress"
    COMPLICATED = "complicated"
    RESOLVED = "resolved"
    FAILED = "failed"
    ABANDONED = "abandoned"


class MemoryType(StrEnum):
    FACTUAL = "factual"
    PROMISE = "promise"
    GRIEVANCE = "grievance"
    KINDNESS = "kindness"
    SECRET_SHARED = "secret_shared"
    OBSERVED = "observed"


class RelationshipCategory(StrEnum):
    TRUST = "trust"
    AFFECTION = "affection"
    RESPECT = "respect"
    FEAR = "fear"
    DEBT = "debt"


class OutcomeBand(StrEnum):
    CRITICAL_FAILURE = "critical_failure"
    FAILURE = "failure"
    SUCCESS_AT_COST = "success_at_cost"
    SUCCESS = "success"
    CRITICAL = "critical"


class VerdictKind(StrEnum):
    ACCEPTED = "accepted"
    CLAMPED = "clamped"
    REJECTED = "rejected"


class SagaLevel(StrEnum):
    SESSION = "session"
    ARC = "arc"
    CAMPAIGN = "campaign"


# Delta kinds — payload table lives in engine/ARCHITECTURE.md ("Frozen contracts" #3).
DELTA_KINDS: frozenset[str] = frozenset({
    "mood", "relationship", "fact", "lead_transition",
    "inventory_add", "inventory_remove", "currency", "hp", "stat",
    "status_add", "status_remove",
})

# Required data keys per delta kind (validator enforces; extras allowed).
DELTA_REQUIRED: dict[str, frozenset[str]] = {
    "mood": frozenset({"valence_delta", "arousal_delta"}),
    "relationship": frozenset({"category", "delta"}),
    "fact": frozenset({"statement"}),
    "lead_transition": frozenset({"new_stage"}),
    "inventory_add": frozenset({"qty"}),
    "inventory_remove": frozenset({"qty"}),
    "currency": frozenset({"amount"}),
    "hp": frozenset({"delta"}),
    "stat": frozenset({"stat", "delta"}),
    "status_add": frozenset({"status"}),
    "status_remove": frozenset({"status"}),
}


# --------------------------------------------------------------------------- #
# Entities (map 1:1 to schema.sql tables; dict keys == column names)
# --------------------------------------------------------------------------- #

@dataclass
class World:
    id: int = 0
    seed: str = ""
    day: int = 1
    hour: int = 8
    minute: int = 0
    active_scene_id: str = ""
    created_at: int = 0


@dataclass
class Location:
    id: int = 0
    name: str = ""
    description_static: str = ""
    connections: list = field(default_factory=list)
    flags: dict = field(default_factory=dict)


@dataclass
class Character:
    """The player character."""
    id: int = 0
    name: str = ""
    stats: dict = field(default_factory=dict)
    inventory: list = field(default_factory=list)  # [{item_id, qty, flags}]
    location_id: str = ""
    status_effects: list = field(default_factory=list)
    known_facts: list = field(default_factory=list)  # fact ids
    journal: list = field(default_factory=list)


@dataclass
class NPC:
    id: int = 0
    name: str = ""
    personality: dict = field(default_factory=dict)  # traits/tone/speech_pattern/baseline_valence/baseline_arousal
    disposition_base: float = 0.0
    location_id: str = ""
    alive: bool = True
    schedule: dict = field(default_factory=dict)


@dataclass
class Lead:
    id: int = 0
    title: str = ""
    stage: str = LeadStage.UNHEARD.value
    stage_history: list = field(default_factory=list)  # [{stage, turn, trigger}]
    known_by: list = field(default_factory=list)
    related_npc_ids: list = field(default_factory=list)
    related_fact_ids: list = field(default_factory=list)


@dataclass
class WorldFact:
    id: int = 0
    statement: str = ""
    pinned: bool = False
    established_turn: int = 0
    source: str = ""  # npc_id | narrator | player | system
    tags: list = field(default_factory=list)
    contradicts: list = field(default_factory=list)  # fact ids


@dataclass
class TurnLog:
    id: int = 0
    turn: int = 0
    actor: str = ""
    raw_action: str = ""
    mechanical_resolution: dict = field(default_factory=dict)
    narration_text: str = ""
    state_deltas_applied: list = field(default_factory=list)
    created_at: int = 0


@dataclass
class NPCMemoryEntry:
    id: int = 0
    npc_id: str = ""
    turn_established: int = 0
    statement: str = ""
    type: str = MemoryType.OBSERVED.value
    sentiment: float = 0.0
    decay_rate: float = 0.0
    reinforced_count: int = 0
    last_referenced_turn: int = 0


@dataclass
class ChronicleEntry:
    id: int = 0
    turn_id: int = 0
    actor: str = ""
    action_summary: str = ""
    mechanical_result: str = ""
    consequence_oneliner: str = ""
    verbatim_text: str | None = None


@dataclass
class RelationshipDelta:
    id: int = 0
    npc_id: str = ""
    player_id: str = ""
    turn: int = 0
    delta: float = 0.0  # the CHANGE, not the value
    reason: str = ""
    category: str = RelationshipCategory.TRUST.value
    decay_class: str = "slow"  # durable | slow | fast


@dataclass
class MoodState:
    id: int = 0
    npc_id: str = ""
    valence: float = 0.0
    arousal: float = 0.0
    baseline_valence: float = 0.0
    baseline_arousal: float = 0.0
    last_updated_turn: int = 0


@dataclass
class SagaRow:
    id: int = 0
    level: str = SagaLevel.SESSION.value  # session | arc | campaign
    scope_id: str = ""  # e.g. "session-1", "arc-1", "campaign"
    text: str = ""
    created_turn: int = 0
    references: list = field(default_factory=list)  # fact ids / lower-level scope ids


@dataclass
class TelemetryRow:
    id: int = 0
    turn: int = 0
    provider: str = ""
    model: str = ""
    prompt_tokens: int = 0
    completion_tokens: int = 0
    budget_alloc: dict = field(default_factory=dict)
    dropped: list = field(default_factory=list)
    notes: dict = field(default_factory=dict)


# --------------------------------------------------------------------------- #
# Turn pipeline contracts
# --------------------------------------------------------------------------- #

@dataclass
class Delta:
    """A proposed state change. Model proposes → validator disposes."""
    kind: str = ""
    target: str = ""
    data: dict = field(default_factory=dict)
    reason: str = ""


@dataclass
class ProposalSet:
    """The Pass B envelope: narration + dialogue + proposed deltas."""
    narration: str = ""
    npc_dialogue: list = field(default_factory=list)  # [{npc_id, name, text}]
    deltas: list = field(default_factory=list)  # list[Delta]


@dataclass
class Verdict:
    kind: str = VerdictKind.ACCEPTED.value
    delta: Delta | None = None
    note: str = ""
    clamped_to: Any = None


@dataclass
class CommitReport:
    accepted: list = field(default_factory=list)  # list[Verdict]
    clamped: list = field(default_factory=list)
    rejected: list = field(default_factory=list)

    @property
    def applied(self) -> list:
        return [*self.accepted, *self.clamped]


@dataclass
class DiceResult:
    expression: str = ""
    rolls: list = field(default_factory=list)  # kept dice
    dropped: list = field(default_factory=list)  # advantage/disadvantage or drop-lowest
    modifier: int = 0
    total: int = 0


@dataclass
class CheckRequest:
    skill: str = ""
    dc: int = 10
    why: str = ""
    attribute: str = ""
    advantage: bool = False
    disadvantage: bool = False


@dataclass
class CheckResult:
    request: CheckRequest | None = None
    roll: int = 0  # kept natural d20
    modifier: int = 0
    total: int = 0
    band: str = OutcomeBand.FAILURE.value
    verdict_line: str = ""


@dataclass
class MechanicalOutcome:
    """Pass A output — the resolved mechanical facts the narration must honor."""
    turn: int = 0
    kind: str = "none"  # none | check | attack | contest | …
    label: str = ""
    check: CheckResult | None = None
    effects: list = field(default_factory=list)  # list[Delta] suggested by mechanics
    verdict_line: str = ""
    notes: list = field(default_factory=list)


@dataclass
class Intent:
    kind: str = "exploration"  # dialogue | action | exploration | meta
    text: str = ""
    skill: str | None = None
    target: str | None = None


@dataclass
class AssembledPrompt:
    text: str = ""  # final user-message text (dynamic sections)
    system: str = ""  # static/cacheable block (system + ruleset)
    sections: list = field(default_factory=list)  # ordered [(name, text)]
    accounting: dict = field(default_factory=dict)  # tokens per section, budget, dropped


@dataclass
class TurnResult:
    turn: int = 0
    narration: str = ""
    npc_dialogue: list = field(default_factory=list)
    mechanics: MechanicalOutcome | None = None
    report: CommitReport | None = None
    system_lines: list = field(default_factory=list)
    telemetry: dict = field(default_factory=dict)


# --------------------------------------------------------------------------- #
# Provider wire shapes (normalized across vendors — spec §7)
# --------------------------------------------------------------------------- #

@dataclass
class ToolCall:
    id: str = ""
    name: str = ""
    arguments: dict = field(default_factory=dict)


@dataclass
class ChatMessage:
    role: str = "user"  # system | user | assistant | tool
    content: str = ""
    tool_calls: list = field(default_factory=list)  # list[ToolCall]
    tool_call_id: str = ""


@dataclass
class ChatRequest:
    model: str = ""
    messages: list = field(default_factory=list)  # list[ChatMessage]
    tools: list | None = None  # normalized (OpenAI-style) tool schemas
    max_tokens: int = 800
    temperature: float = 0.8
    stream: bool = False


@dataclass
class ChatResponse:
    text: str = ""
    tool_calls: list = field(default_factory=list)  # list[ToolCall]
    usage: dict = field(default_factory=dict)
    provider: str = ""
    model: str = ""
    raw: dict | None = None


@dataclass
class ProviderCaps:
    native_tools: bool = False
    streaming: bool = False
    json_mode: bool = False
    context_window: int | None = None
    probed_at: int = 0


@dataclass
class ProviderConfig:
    name: str = "openai"  # openai | anthropic | gemini | local
    model: str = ""
    base_url: str = ""
    api_mode: str = "openai"  # wire family: openai | anthropic | gemini
    context_window: int | None = None
    cost_tier: str = "mid"  # cheap | mid | high
    timeout_s: float = 60.0
    max_retries: int = 2
    extra: dict = field(default_factory=dict)


# --------------------------------------------------------------------------- #
# Row (de)serialization helpers — dict keys == column names
# --------------------------------------------------------------------------- #

JSON_FIELDS: dict[type, frozenset[str]] = {
    Location: frozenset({"connections", "flags"}),
    Character: frozenset({"stats", "inventory", "status_effects", "known_facts", "journal"}),
    NPC: frozenset({"personality", "schedule"}),
    Lead: frozenset({"stage_history", "known_by", "related_npc_ids", "related_fact_ids"}),
    WorldFact: frozenset({"tags", "contradicts"}),
    TurnLog: frozenset({"mechanical_resolution", "state_deltas_applied"}),
    SagaRow: frozenset({"references"}),
    TelemetryRow: frozenset({"budget_alloc", "dropped", "notes"}),
}


def to_row(obj: Any) -> dict:
    """Dataclass -> row dict for ``Store.insert``. Excludes ``id`` (DB assigns).

    JSON fields are ``json.dumps``-encoded; booleans become 0/1 for SQLite.
    """
    json_fields = JSON_FIELDS.get(type(obj), frozenset())
    out: dict = {}
    for f in fields(obj):
        if f.name == "id":
            continue
        value = getattr(obj, f.name)
        if f.name in json_fields:
            value = json.dumps(value)
        elif isinstance(value, bool):
            value = int(value)
        out[f.name] = value
    return out


def from_row(cls: type, row: Mapping[str, Any]) -> Any:
    """Row dict -> dataclass. Unknown columns are ignored; JSON fields decoded;
    int 0/1 normalize back to bool for bool-defaulted fields."""
    names = {f.name for f in fields(cls)}
    json_fields = JSON_FIELDS.get(cls, frozenset())
    kwargs: dict = {}
    for f in fields(cls):
        if f.name not in row:
            continue
        value = row[f.name]
        if f.name in json_fields and isinstance(value, str):
            try:
                value = json.loads(value)
            except (TypeError, ValueError):
                pass
        elif isinstance(f.default, bool) and isinstance(value, int):
            value = bool(value)
        kwargs[f.name] = value
    _ = names
    return cls(**kwargs)
