"""Budget-controller tests: exact percentage math, drop order, truncation.

The pressure scenarios are built the way a real turn hits them: the mechanical
outcome and the pinned facts are non-negotiable, the chronicle/digest and the
NPC memory are what gives way first.
"""
from __future__ import annotations

import json
from typing import Any

from test_context_doubles import (
    ChronicleDouble,
    FactDouble,
    MemoryDouble,
    NPCMemoryDouble,
    SagaDouble,
    chronicle_entry,
    npc_entry,
    npc_read,
    pinned_fact,
    saga_row,
    scene_dict,
)

from engine.config import EngineConfig
from engine.context import ContextAssembler
from engine.models import (
    CheckRequest,
    CheckResult,
    Delta,
    Intent,
    MechanicalOutcome,
)

NPC = "npc:1"
INPUT = "I search the yard for the missing shipment"


class _CapsStore:
    """Minimal store exposing only the cached provider-capability row."""

    def __init__(self, caps: object) -> None:
        self.caps = caps
        self.leads: list = []

    def find_one(self, table: str, where=None, *, order_by=None) -> dict | None:
        value = self.caps if isinstance(self.caps, str) else json.dumps(self.caps)
        return {"provider_key": "openai:test", "caps": value, "probed_at": 7}

    def find(self, table: str, where=None, **kwargs) -> list:
        return list(self.leads)


def _outcome() -> MechanicalOutcome:
    return MechanicalOutcome(
        turn=6, kind="check", label="searches the yard",
        check=CheckResult(
            request=CheckRequest(skill="perception", dc=12), roll=17,
            modifier=2, total=19, band="success",
        ),
        effects=[Delta(kind="hp", target="player", data={"delta": -3}, reason="grazed")],
        verdict_line="SUCCESS",
    )


def _memory(*, chronicle=(), saga=(), npc_entries=(), pins=()) -> MemoryDouble:
    return MemoryDouble(
        npc_memory=NPCMemoryDouble({NPC: list(npc_entries)} if npc_entries else {}),
        chronicle=ChronicleDouble(list(chronicle)),
        saga=SagaDouble(list(saga)),
        facts=FactDouble(list(pins)),
    )


_MECHANICAL: Any = object()  # sentinel: "use the standard outcome for this scenario"


def _assemble(window: int, *, memory: MemoryDouble, config: EngineConfig | None = None,
              store=None, ruleset: str = "Rules: narrate.", mechanical=_MECHANICAL,
              text: str = INPUT):
    return ContextAssembler(store, config or EngineConfig(), components=memory).assemble(
        turn=6,
        scene=scene_dict(ruleset=ruleset, npcs=[npc_read(npc_id=NPC, name="Marla")]),
        mechanical=_outcome() if mechanical is _MECHANICAL else mechanical,
        intent=Intent(kind="action", text=text),
        context_window=window,
    )


def _section(prompt, name: str) -> str:
    return next((body for section, body in prompt.sections if section == name), "")


def _dropped_sections(prompt) -> list[str]:
    return [entry["section"] for entry in prompt.accounting["dropped"]]


_PIN = pinned_fact(statement="The player promised to find the missing shipment")
_CHRONICLE = [
    chronicle_entry(turn_id=4, action_summary="the player searched the yard", actor="player"),
    chronicle_entry(turn_id=5, action_summary="the player found a locked crate", actor="player"),
]
_NPC_ENTRIES = [
    (npc_entry(statement="the player promised to find the shipment", entry_id=1), 0.9),
]


def _pressure(window: int, *, saga_rows=()) -> MemoryDouble:
    """Rich content with one entry per droppable section (> window room)."""
    return _memory(
        chronicle=_CHRONICLE,
        saga=saga_rows or [saga_row(text="The campaign opened in the yard.")],
        npc_entries=[
            *_NPC_ENTRIES,
            (npc_entry(statement="a stale grievance about the fence", entry_id=2), 0.4),
            (npc_entry(statement="an unimportant observation", entry_id=3), 0.1),
        ],
        pins=[_PIN],
    )


# --------------------------------------------------------------------------- #
# percentage math
# --------------------------------------------------------------------------- #

def test_default_window_percentages_are_exact() -> None:
    prompt = _assemble(32000, memory=_memory())
    accounting = prompt.accounting
    assert accounting["budget"]["window"] == 32000
    assert accounting["budget"]["output_reserve"] == 6400          # 20%
    assert accounting["budget"]["total"] == 25600                  # window - reserve
    assert accounting["allocations"] == {
        "system": 4800,      # 15% of the window
        "scene": 8320,       # 40% of the 20800 remainder
        "memory": 6240,      # 30%
        "continuity": 6240,  # 30%
    }
    assert accounting["budget"]["used"] <= accounting["budget"]["total"]
    assert accounting["budget"]["over_budget"] is False


def test_small_window_percentages_are_exact() -> None:
    prompt = _assemble(1000, memory=_memory())
    accounting = prompt.accounting
    assert accounting["budget"]["output_reserve"] == 200
    assert accounting["budget"]["total"] == 800
    assert accounting["allocations"] == {
        "system": 150, "scene": 260, "memory": 195, "continuity": 195,
    }


def test_custom_percentages_are_honored() -> None:
    config = EngineConfig()
    config.budget.output_reserve_pct = 10
    config.budget.system_pct = 10
    config.budget.group_scene_pct = 50
    config.budget.group_memory_pct = 25
    config.budget.group_continuity_pct = 25
    prompt = _assemble(1000, memory=_memory(), config=config)
    accounting = prompt.accounting
    assert accounting["budget"]["output_reserve"] == 100
    assert accounting["budget"]["total"] == 900
    # remainder after the 10% system block: 800 split 50/25/25
    assert accounting["allocations"] == {
        "system": 100, "scene": 400, "memory": 200, "continuity": 200,
    }


def test_window_precedence_param_then_provider_caps_then_config_default() -> None:
    config = EngineConfig()
    config.budget.default_context_window = 1000
    assert _assemble(2000, memory=_memory(), config=config).accounting["budget"]["window"] == 2000

    probed = _assemble(0, memory=_memory(), config=config,
                       store=_CapsStore({"context_window": 5000, "native_tools": True}))
    assert probed.accounting["budget"]["window"] == 5000
    assert probed.accounting["allocations"]["scene"] == 1300

    assert _assemble(0, memory=_memory(), config=config,
                     store=_CapsStore({"native_tools": True})).accounting["budget"]["window"] == 1000
    assert _assemble(0, memory=_memory(), config=config,
                     store=_CapsStore(None)).accounting["budget"]["window"] == 1000
    assert _assemble(0, memory=_memory(), config=config,
                     store=_CapsStore("not json")).accounting["budget"]["window"] == 1000
    assert _assemble(0, memory=_memory(), config=config,
                     store=None).accounting["budget"]["window"] == 1000


def test_system_allocation_respects_the_hard_ceiling() -> None:
    config = EngineConfig()
    config.budget.system_pct = 40
    prompt = _assemble(1000, memory=_memory(), config=config)
    assert prompt.accounting["allocations"]["system"] == 250  # system_max_pct 25%


def test_system_block_is_truncated_to_its_allocation() -> None:
    prompt = _assemble(1000, memory=_memory(), ruleset="House rule: roll high. " * 200)
    accounting = prompt.accounting
    system = next(entry for entry in accounting["dropped"] if entry["section"] == "system")
    assert system["reason"] == "system_ceiling"
    assert 0 < system["kept_tokens"] < system["dropped_tokens"]
    assert accounting["sections"]["system"] <= accounting["allocations"]["system"]
    assert prompt.system.endswith("…")
    assert accounting["budget"]["used"] <= accounting["budget"]["total"]


# --------------------------------------------------------------------------- #
# pressure: what survives, what goes first
# --------------------------------------------------------------------------- #

def test_extreme_pressure_keeps_the_outcome_and_the_pinned_facts() -> None:
    prompt = _assemble(120, memory=_pressure(120))
    names = [name for name, _ in prompt.sections]
    assert "mechanics" in names and "pinned_facts" in names
    mechanics = _section(prompt, "mechanics")
    assert "Outcome: check — searches the yard" in mechanics
    assert "Verdict: SUCCESS" in mechanics
    assert "Check: perception — roll 17 +2 = 19 vs DC 12 → success" in mechanics
    assert "- hp on player: delta=-3 (grazed)" in mechanics
    assert _PIN.statement in _section(prompt, "pinned_facts")
    # nothing can push the prompt over the hard budget here
    assert prompt.accounting["budget"]["used"] <= prompt.accounting["budget"]["total"]
    assert prompt.accounting["budget"]["over_budget"] is False


def test_drop_order_is_bottom_up() -> None:
    prompt = _assemble(120, memory=_pressure(120))
    assert _dropped_sections(prompt) == ["saga", "chronicle", "npc_memory", "scene"]
    assert {entry["reason"] for entry in prompt.accounting["dropped"]} == {"over_budget"}
    # the scene state is the last thing to go, and it did go
    assert _section(prompt, "scene") == ""
    assert "leads" not in _dropped_sections(prompt)  # nothing to drop — no leads


def test_overrun_of_the_scene_group_is_paid_from_the_lower_groups() -> None:
    prompt = _assemble(120, memory=_pressure(120))
    accounting = prompt.accounting
    allocations = accounting["allocations"]
    compensation = accounting["compensation"]
    nonneg = accounting["sections"]["mechanics"] + accounting["sections"]["pinned_facts"]
    assert nonneg > 31  # the pre-compensation scene group (40% of 78) is overrun
    assert allocations["scene"] == nonneg                 # grown to fit the non-negotiables
    assert compensation["continuity"] == 23               # continuity (23) emptied first
    assert allocations["continuity"] == 0
    assert compensation["memory"] == 23 - allocations["memory"]  # then the memory group
    assert accounting["budget"]["over_budget"] is False   # the lower groups had room


def test_over_budget_is_only_reachable_by_the_non_negotiables_alone() -> None:
    giant = pinned_fact(statement="The player promised to find the missing shipment. " * 120)
    prompt = _assemble(1000, memory=_memory(pins=[giant]))
    accounting = prompt.accounting
    assert accounting["budget"]["over_budget"] is True
    assert accounting["budget"]["remaining"] < 0
    assert accounting["allocations"]["memory"] == 0
    assert accounting["allocations"]["continuity"] == 0
    # the pinned fact is present in full — never truncated, never dropped
    assert giant.statement in _section(prompt, "pinned_facts")
    assert _section(prompt, "scene") == ""


def test_droppable_content_never_breaks_the_hard_budget() -> None:
    prompt = _assemble(400, memory=_pressure(
        400, saga_rows=[saga_row(text="The campaign digest. " * 200)],
    ))
    accounting = prompt.accounting
    assert accounting["budget"]["used"] <= accounting["budget"]["total"]
    assert accounting["budget"]["over_budget"] is False
    assert "mechanics" in [name for name, _ in prompt.sections]


def test_saga_is_truncated_while_the_chronicle_tail_survives() -> None:
    prompt = _assemble(200, memory=_pressure(200))
    names = [name for name, _ in prompt.sections]
    assert "chronicle" in names and "saga" in names
    saga = next(entry for entry in prompt.accounting["dropped"] if entry["section"] == "saga")
    assert saga["kept_tokens"] > 0 and saga["dropped_tokens"] > 0
    assert "chronicle" not in _dropped_sections(prompt)
    assert _section(prompt, "saga").endswith("…")
    assert _section(prompt, "chronicle").count("[turn ") == 2


def test_saga_detail_is_dropped_before_the_campaign_digest() -> None:
    campaign = saga_row(
        level="campaign", text="The campaign opened in the yard where a missing shipment waits.",
    )
    session = saga_row(
        level="session", scope_id="session-1",
        text="Session one: the player asked about the missing shipment near the gate.",
    )
    prompt = _assemble(150, memory=_memory(
        npc_entries=_NPC_ENTRIES, pins=[_PIN], saga=[campaign, session],
    ))
    body = _section(prompt, "saga")
    assert "[campaign]" in body
    assert "The campaign opened in" in body   # the campaign digest survives the squeeze
    assert "[session]" not in body            # the detail level is gone entirely
    assert session.text not in body
    dropped = next(entry for entry in prompt.accounting["dropped"] if entry["section"] == "saga")
    assert dropped["dropped_tokens"] > 0
    assert prompt.accounting["budget"]["used"] <= prompt.accounting["budget"]["total"]


def test_group_allocations_are_silos() -> None:
    huge = saga_row(text="The campaign digest. " * 200)
    prompt = _assemble(1000, memory=_memory(saga=[huge]))
    accounting = prompt.accounting
    assert accounting["allocations"] == {
        "system": 150, "scene": 260, "memory": 195, "continuity": 195,
    }
    assert accounting["sections"]["saga"] == 195  # capped by the continuity group
    assert accounting["sections"]["npc_memory"] == 0
    assert accounting["budget"]["used"] < accounting["budget"]["total"]


def test_lower_salience_npc_memory_is_dropped_first() -> None:
    entries = [
        (npc_entry(statement=f"memory number {index} about the yard and the shipment",
                   entry_id=index), score)
        for index, score in enumerate([0.9, 0.7, 0.5, 0.3, 0.1], start=1)
    ]
    prompt = _assemble(400, memory=_memory(npc_entries=entries))
    body = _section(prompt, "npc_memory")
    assert "memory number 1 about the yard and the shipment" in body
    assert "memory number 5 about the yard and the shipment" not in body
    dropped = next(
        entry for entry in prompt.accounting["dropped"] if entry["section"] == "npc_memory"
    )
    assert dropped["kept_tokens"] > 0 and dropped["dropped_tokens"] > 0


def test_absent_mechanical_outcome_still_reserves_its_place() -> None:
    prompt = _assemble(120, memory=_pressure(120), mechanical=None)
    assert "No mechanical resolution this turn." in _section(prompt, "mechanics")
    assert prompt.accounting["sections"]["mechanics"] > 0
