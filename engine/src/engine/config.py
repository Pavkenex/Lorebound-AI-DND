"""Engine configuration — defaults come from docs/REBUILD_SPEC.md (§4, §3);
tunable per launch. Pure data; no logic."""
from __future__ import annotations

from dataclasses import dataclass, field


@dataclass
class BudgetConfig:
    """Prompt-budget allocation (spec §4). Percentages of the model context window."""
    # Reserve for the completion.
    output_reserve_pct: int = 20
    # Static system/ruleset block (cacheable), as pct of total window.
    system_pct: int = 15
    # Split of the remaining budget across the three groups (spec's 40/30/30).
    group_scene_pct: int = 40  # scene state + mechanical outcome + pinned facts
    group_memory_pct: int = 30  # NPC memory + leads
    group_continuity_pct: int = 30  # chronicle tail + saga digest
    # Used when the provider/model declares no context window.
    default_context_window: int = 32000
    # Never let the static block exceed this share of total (hard ceiling safeguard).
    system_max_pct: int = 25


@dataclass
class SalienceWeights:
    """Salience scoring defaults (spec §3.1). score = w1*recency + w2*|sentiment|
    + w3*scene_relevance + w4*log1p(reinforced) - w5*decay(turns, decay_rate)."""
    w_recency: float = 0.35
    w_sentiment: float = 0.25
    w_relevance: float = 0.25
    w_reinforced: float = 0.10
    w_decay: float = 1.0
    # recency = exp(-turns_since_established / recency_half_life_turns)
    recency_half_life_turns: float = 20.0
    # default decay rates per MemoryType (factual ≈ 0: facts don't fade)
    decay_rates: dict = field(default_factory=lambda: {
        "factual": 0.0,
        "promise": 0.02,
        "secret_shared": 0.05,
        "grievance": 0.15,
        "kindness": 0.15,
        "observed": 0.15,
    })


@dataclass
class MemoryConfig:
    npc_top_k: int = 6  # entries retrieved per NPC present in scene
    chronicle_window: int = 12  # structured entries kept in the injected tail
    chronicle_verbatim: int = 3  # most-recent turns keeping verbatim text
    session_summary_turns: int = 30  # session summary cadence (~20-40, spec §3.3)
    mood_half_life_turns: float = 6.0
    relationship_decay_per_turn: dict = field(default_factory=lambda: {
        "durable": 0.0, "slow": 0.01, "fast": 0.08,
    })
    fact_rate_limit_per_turn: int = 3  # validator: max NEW facts per turn
    relationship_event_bound: float = 40.0  # per-event |delta| clamp
    min_pin_relevance: float = 0.10  # pinned facts inject above this scene relevance


@dataclass
class EngineConfig:
    db_path: str = "engine_state.db"
    player_id: str = "player"
    budget: BudgetConfig = field(default_factory=BudgetConfig)
    salience: SalienceWeights = field(default_factory=SalienceWeights)
    memory: MemoryConfig = field(default_factory=MemoryConfig)
    # Single-player campaign per DB (spec assumption).
