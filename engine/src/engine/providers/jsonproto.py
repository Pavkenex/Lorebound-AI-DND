"""Degraded JSON-in-text protocol + the canonical Pass B codec (spec §7).

Two jobs:

1. **Canonical Pass B schema/codec** used by BOTH the native tool path and the
   degraded path: ``delta_tool_schema()`` returns the normalized (OpenAI-style)
   tool definitions for ``propose_state_deltas``; ``proposals_from_tool_calls``
   converts parsed tool calls into ``ProposalSet``-shaped parts (narration
   stays separate).

2. **Strict JSON-in-text fallback** for models without reliable tool calling:
   ``build_json_protocol_prompt`` appends exact-format instructions;
   ``parse_tolerant(text)`` extracts the JSON object from prose/fences;
   ``should_regenerate``/``regenerate_note`` drive the bounded
   regenerate-on-parse-failure loop (spec §7: quality floor — the game must
   not break because a model formats poorly).

The delta payload schema is frozen in engine/ARCHITECTURE.md ("Frozen
contracts" #3); DELTA_KINDS/DELTA_REQUIRED live in ``models``.
"""
from __future__ import annotations

from ..models import Delta, ProposalSet, ToolCall

PROPOSE_DELTAS_TOOL_NAME = "propose_state_deltas"


def delta_tool_schema() -> list[dict]:
    """Normalized tool definitions for the delta proposals + narration envelope."""
    raise NotImplementedError("R4 card implements delta_tool_schema")


def proposals_from_tool_calls(tool_calls: list[ToolCall]) -> ProposalSet:
    """Tool calls -> ProposalSet(deltas=...) — narration/npc_dialogue filled by
    the pipeline from text analysis when the model returns them separately."""
    raise NotImplementedError("R4 card implements proposals_from_tool_calls")


def build_json_protocol_prompt(base_prompt: str) -> str:
    """Append the exact JSON envelope instructions for degraded models."""
    raise NotImplementedError("R4 card implements build_json_protocol_prompt")


def parse_tolerant(text: str) -> dict | None:
    """Extract the outermost JSON object from arbitrary model text
    (fences, preamble, trailing prose). Returns None when nothing parses."""
    raise NotImplementedError("R4 card implements parse_tolerant")


def regenerate_note(parse_error: str) -> str:
    """Correction note injected on a parse-failure regeneration attempt."""
    raise NotImplementedError("R4 card implements regenerate_note")


_ = (Delta, PROPOSE_DELTAS_TOOL_NAME)
