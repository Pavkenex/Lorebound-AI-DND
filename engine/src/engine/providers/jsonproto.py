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

Parsing policy: strict ``json.loads`` first, then a tolerant extraction of the
outermost JSON *object* (code fences, preamble, trailing prose). Formatting
noise is tolerated; genuinely broken JSON is not repaired here — the caller
regenerates (bounded) instead of guessing at state-changing data.
"""
from __future__ import annotations

import json

from ..models import DELTA_KINDS, DELTA_REQUIRED, Delta, ProposalSet, ToolCall

PROPOSE_DELTAS_TOOL_NAME = "propose_state_deltas"

_NARRATION_DESCRIPTION = (
    "2-4 short paragraphs of narration in second person, present tense. "
    "Honor the resolved mechanical outcome and pinned facts; never state dice "
    "results as numbers."
)
_DIALOGUE_DESCRIPTION = (
    "Lines spoken on screen this turn: {npc_id, name, text}. Empty list when "
    "nobody speaks."
)
_DELTAS_DESCRIPTION = (
    "Proposed state changes for this turn (validated by the engine before they "
    "are applied, so propose only what the turn justifies). Empty list when "
    "nothing about the world state changed."
)
_TARGET_DESCRIPTION = (
    'Target id: npc id for mood/relationship, lead id for lead_transition, '
    'item id for inventory_*, character id for hp/stat/status_*; "" when the '
    "kind has no target."
)


def _payload_hint(kind: str) -> str:
    keys = sorted(DELTA_REQUIRED[kind])
    return f"{kind}: data keys {', '.join(keys)}"


def _delta_payload_lines() -> list[str]:
    return [_payload_hint(kind) for kind in sorted(DELTA_REQUIRED)]


def delta_tool_schema() -> list[dict]:
    """Normalized tool definitions for the delta proposals + narration envelope.

    One ``propose_state_deltas`` call carries the whole Pass B envelope
    (narration + npc_dialogue + deltas) so the native and degraded paths share
    a single codec. Delta ``kind`` is constrained to the frozen DELTA_KINDS;
    each kind's required payload keys are spelled out in the ``data`` hint.
    """
    return [
        {
            "type": "function",
            "function": {
                "name": PROPOSE_DELTAS_TOOL_NAME,
                "description": (
                    "Call exactly once per turn: narrate the turn and propose "
                    "state deltas. Deltas are proposals — the engine validates "
                    "and commits them; unknown kinds are dropped."
                ),
                "parameters": {
                    "type": "object",
                    "properties": {
                        "narration": {"type": "string", "description": _NARRATION_DESCRIPTION},
                        "npc_dialogue": {
                            "type": "array",
                            "description": _DIALOGUE_DESCRIPTION,
                            "items": {
                                "type": "object",
                                "properties": {
                                    "npc_id": {"type": "string"},
                                    "name": {"type": "string"},
                                    "text": {"type": "string"},
                                },
                                "required": ["npc_id", "text"],
                            },
                        },
                        "deltas": {
                            "type": "array",
                            "description": _DELTAS_DESCRIPTION,
                            "items": {
                                "type": "object",
                                "properties": {
                                    "kind": {"type": "string", "enum": sorted(DELTA_KINDS)},
                                    "target": {"type": "string", "description": _TARGET_DESCRIPTION},
                                    "data": {
                                        "type": "object",
                                        "description": "Payload per kind: " + "; ".join(
                                            _delta_payload_lines()
                                        ),
                                    },
                                    "reason": {
                                        "type": "string",
                                        "description": "One line: why this change follows from the turn.",
                                    },
                                },
                                "required": ["kind", "data"],
                            },
                        },
                    },
                    "required": ["narration", "deltas"],
                },
            },
        }
    ]


def _note(notes: list[str] | None, message: str) -> None:
    if notes is not None:
        notes.append(message)


def _dialogue_entries(raw: object) -> list[dict]:
    entries: list[dict] = []
    if not isinstance(raw, list):
        return entries
    for item in raw:
        if not isinstance(item, dict):
            continue
        entry = {
            "npc_id": str(item.get("npc_id") or ""),
            "name": str(item.get("name") or ""),
            "text": str(item.get("text") or ""),
        }
        entry.update({k: v for k, v in item.items() if k not in entry})
        entries.append(entry)
    return entries


def _delta_from_item(item: object, *, notes: list[str] | None) -> Delta | None:
    if not isinstance(item, dict):
        _note(notes, f"dropped non-object delta proposal: {item!r}")
        return None
    kind = str(item.get("kind") or "")
    if kind not in DELTA_KINDS:
        _note(notes, f"dropped unknown delta kind {kind!r}")
        return None
    target = item.get("target")
    data = item.get("data")
    return Delta(
        kind=kind,
        target="" if target is None else str(target),
        data=dict(data) if isinstance(data, dict) else {},
        reason=str(item.get("reason") or ""),
    )


def proposals_from_payload(
    payload: dict,
    *,
    narration: str = "",
    notes: list[str] | None = None,
) -> ProposalSet:
    """Decoded envelope dict -> ``ProposalSet``.

    Tolerant on shape: missing/ill-typed fields degrade to empty values, delta
    items with unknown kinds (or non-object items) are dropped and described in
    ``notes`` when the caller passes a list. ``narration`` is the fallback used
    when the payload does not carry one (models that narrate in text and only
    put deltas in the object).
    """
    if not isinstance(payload, dict):
        _note(notes, f"dropped non-object payload: {payload!r}")
        return ProposalSet(narration=narration)
    text = payload.get("narration")
    deltas: list[Delta] = []
    raw_deltas = payload.get("deltas")
    if raw_deltas is not None and not isinstance(raw_deltas, list):
        _note(notes, "ignored deltas: not a list")
        raw_deltas = []
    for item in raw_deltas or []:
        delta = _delta_from_item(item, notes=notes)
        if delta is not None:
            deltas.append(delta)
    return ProposalSet(
        narration=text if isinstance(text, str) and text else narration,
        npc_dialogue=_dialogue_entries(payload.get("npc_dialogue")),
        deltas=deltas,
    )


def proposals_from_tool_calls(
    tool_calls: list[ToolCall],
    *,
    narration: str = "",
    notes: list[str] | None = None,
) -> ProposalSet:
    """Tool calls -> ``ProposalSet`` (deltas; narration when the model sent it).

    Reads the first ``propose_state_deltas`` call (the envelope: narration /
    npc_dialogue / deltas). Unknown delta kinds are dropped and noted in
    ``notes``. ``narration`` is the fallback for models that return narration as
    message text instead of inside the call.
    """
    payload: dict | None = None
    for call in tool_calls or []:
        if getattr(call, "name", "") != PROPOSE_DELTAS_TOOL_NAME:
            continue
        arguments = getattr(call, "arguments", None)
        if isinstance(arguments, str):  # tolerantly decode string arguments too
            arguments = parse_tolerant(arguments)
        if isinstance(arguments, dict):
            payload = arguments
            break
    if payload is None:
        _note(notes, f"no {PROPOSE_DELTAS_TOOL_NAME} tool call found")
        return ProposalSet(narration=narration)
    return proposals_from_payload(payload, narration=narration, notes=notes)


def build_json_protocol_prompt(base_prompt: str) -> str:
    """Append the exact JSON envelope instructions for degraded models.

    The envelope is the same one the ``propose_state_deltas`` tool carries —
    stable key order and stable kind listing so tests and models see the same
    contract every time.
    """
    block = "\n".join(
        [
            "OUTPUT CONTRACT (strict — no tool calling available):",
            "Reply with ONE JSON object and NOTHING else. No markdown fences, no",
            "preamble, no commentary before or after. Exactly this shape:",
            "",
            '{"narration": "<2-4 short paragraphs; second person, present tense>",'
            ' "npc_dialogue": [{"npc_id": "<npc id>", "name": "<name>", "text":'
            ' "<spoken line>"}], "deltas": [{"kind": "<kind>", "target":'
            ' "<npc_id|lead_id|item_id|character id|\\"\\">", "data": {<payload>},'
            ' "reason": "<one line: why this follows from the turn>"}]}',
            "",
            '- "narration" is required; "npc_dialogue" and "deltas" are [] when empty.',
            "- Deltas are PROPOSALS: the engine validates them before any state",
            "  changes; unknown kinds are dropped.",
            "- Allowed kinds and their data keys:",
            *[f"  {line}" for line in _delta_payload_lines()],
            "- Output the JSON object only. Any other text is discarded.",
        ]
    )
    base = (base_prompt or "").rstrip()
    return f"{base}\n\n{block}" if base else block


def _try_json_object(text: str) -> dict | None:
    try:
        value = json.loads(text)
    except ValueError:
        return None
    return value if isinstance(value, dict) else None


def _fenced_blocks(text: str) -> list[str]:
    blocks: list[str] = []
    remainder = text
    while True:
        start = remainder.find("```")
        if start == -1:
            return blocks
        end = remainder.find("```", start + 3)
        if end == -1:
            return blocks
        block = remainder[start + 3 : end]
        newline = block.find("\n")
        if newline != -1 and block[:newline].strip().lower() in {"json", "json5", "javascript"}:
            block = block[newline + 1 :]
        blocks.append(block)
        remainder = remainder[end + 3 :]


def _extract_object(text: str) -> dict | None:
    """First balanced ``{...}`` span that decodes to a JSON object."""
    for start, char in enumerate(text):
        if char != "{":
            continue
        depth = 0
        in_string = False
        escaped = False
        for index in range(start, len(text)):
            current = text[index]
            if in_string:
                if escaped:
                    escaped = False
                elif current == "\\":
                    escaped = True
                elif current == '"':
                    in_string = False
                continue
            if current == '"':
                in_string = True
            elif current == "{":
                depth += 1
            elif current == "}":
                depth -= 1
                if depth == 0:
                    parsed = _try_json_object(text[start : index + 1])
                    if parsed is not None:
                        return parsed
                    break
    return None


def parse_tolerant(text: str) -> dict | None:
    """Extract the outermost JSON object from arbitrary model text.

    Handles exact JSON, ```json fenced blocks, preamble ("Here is my reply:"),
    and trailing prose. Returns ``None`` when nothing decodes to a JSON object —
    broken JSON is NOT repaired (state-changing data is never guessed at; the
    caller regenerates instead).
    """
    if not text or not isinstance(text, str):
        return None
    candidate = _try_json_object(text.strip())
    if candidate is not None:
        return candidate
    for block in _fenced_blocks(text):
        candidate = _try_json_object(block.strip())
        if candidate is not None:
            return candidate
    return _extract_object(text)


def should_regenerate(
    parsed: dict | None,
    *,
    attempts: int,
    max_attempts: int,
) -> bool:
    """Bounded regenerate-on-parse-failure: ``attempts`` calls already made.

    True when the last reply failed to parse and the budget still allows
    another round trip. Callers append ``regenerate_note(reason)`` to the
    message list before re-calling the model.
    """
    return parsed is None and attempts < max(1, int(max_attempts))


def regenerate_note(parse_error: str) -> str:
    """Correction note injected on a parse-failure regeneration attempt."""
    detail = (parse_error or "").strip() or "the previous reply was not valid JSON"
    return (
        f"Your previous reply was unusable: {detail}.\n"
        "Reply AGAIN with ONLY the JSON object — no markdown fences, no "
        "commentary before or after. Required shape: "
        '{"narration": "<text>", "npc_dialogue": [...], "deltas": [...]}'
    )
