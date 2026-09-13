"""Narrator service: prose generation with ack + streaming (t_ad5b30b4).

Flow: assemble prompt from retrieved context -> exactly ONE provider call ->
parse NarratorOutput. The model returns prose/proposals only; state changes
go through the AuthorityEngine (never the model).

Completion parsing is deliberately forgiving: models routinely wrap the JSON
object in code fences or add a line of commentary. ``parse_narrator_payload``
recovers {narration, npc_dialogue, suggested_actions} from all of those, and
raw JSON scaffolding is never shown to the player.
"""
from __future__ import annotations

import ast
import json
import logging
import re
from collections.abc import Iterator
from dataclasses import dataclass, field
from difflib import SequenceMatcher
from typing import Any

from app.modules.ai.metering import MeterRegistry
from app.modules.ai.providers import Provider, get_provider
from app.modules.ai.roles import Role, build_role_prompt
from app.modules.narrator.prefs import ContentPrefs
from app.modules.narrator.prompts import PromptBundle, PromptContext, assemble_prompt
from app.modules.narrator.schemas import (
    DEFAULT_LENGTH,
    WORD_TARGETS,
    NarratorOutput,
    NpcDialogue,
    SuggestedActionOut,
    ack,
)

_JSON_FENCE_RE = re.compile(r"```(?:json|jsonc)?\s*(.*?)\s*```", re.DOTALL | re.IGNORECASE)
_NARRATION_FIELD_RE = re.compile(r'"narration"\s*:\s*"((?:[^"\\]|\\.)*)"', re.DOTALL)
_DIALOGUE_PAIR_RE = re.compile(
    r'"(?:speaker|npc)"\s*:\s*"((?:[^"\\]|\\.)*)"\s*,\s*"line"\s*:\s*"((?:[^"\\]|\\.)*)"'
)
_JSON_SCAFFOLD_HINTS = ('"narration"', '"npc_dialogue"', '"suggested_actions"',
                        '"proposed_events"', '"proposed_lead_changes"')
#: Never let scaffolding reach the chronicle; a clean retry note reads better.
TANGLED_NOTE = (
    "The chronicler's words came back tangled in the quill's own notes. "
    "The moment holds — say it again, and the tale will answer."
)
#: Narrator completion budget. A Standard reply (≤250 words of prose plus
#: dialogue, suggestions and proposals, all as JSON) needs ~900 tokens; the
#: old 600-token default cut replies mid-JSON and the salvage paths served
#: the fragment — the "mumbling". Headroom is cheaper than confusion.
NARRATOR_MAX_TOKENS = 1200

_log = logging.getLogger("lorebound.narrator")

#: The narrator sometimes quotes an NPC's closing line inside the prose AND
#: lists it in npc_dialogue, so the player reads the same sentence twice.
#: A line counts as echoed when this much of it (as one contiguous run of
#: words) already appears in the narration. Short lines are never touched.
_DIALOGUE_ECHO_RATIO = 0.85
_DIALOGUE_ECHO_MIN_WORDS = 6
_DIALOGUE_ECHO_MIN_RUN = 5


@dataclass
class NarratorPayload:
    """Structured pieces recovered from one narrator completion."""

    narration: str | None = None
    dialogue: list[dict[str, str]] = field(default_factory=list)
    suggestions: list[dict[str, str]] = field(default_factory=list)
    parsed: bool = False


def _try_loads(candidate: str) -> dict | None:
    """json.loads with a trailing-comma repair and a Python-literal fallback."""
    attempts = (candidate, re.sub(r",\s*([}\]])", r"\1", candidate))
    for attempt in attempts:
        try:
            value = json.loads(attempt)
        except (json.JSONDecodeError, ValueError):
            continue
        if isinstance(value, dict):
            return value
    try:
        value = ast.literal_eval(candidate)
    except (ValueError, SyntaxError):
        return None
    return value if isinstance(value, dict) else None


def _slice_balanced(text: str, start: int) -> str | None:
    """The balanced ``{...}`` substring from ``start``, string-aware."""
    depth = 0
    in_str = False
    escaped = False
    for i in range(start, len(text)):
        ch = text[i]
        if in_str:
            if escaped:
                escaped = False
            elif ch == "\\":
                escaped = True
            elif ch == '"':
                in_str = False
            continue
        if ch == '"':
            in_str = True
        elif ch == "{":
            depth += 1
        elif ch == "}":
            depth -= 1
            if depth == 0:
                return text[start:i + 1]
    return None


def _find_json_object(text: str) -> dict | None:
    """First parseable JSON object in ``text``, wherever it is hiding."""
    positions = [m.start() for m in re.finditer(r"\{", text)][:8]
    for pos in positions:
        sliced = _slice_balanced(text, pos)
        if sliced:
            data = _try_loads(sliced)
            if data is not None:
                return data
    return None


def _normalize_dialogue(raw: Any) -> list[dict[str, str]]:
    out: list[dict[str, str]] = []
    if not isinstance(raw, list):
        return out
    for entry in raw:
        if not isinstance(entry, dict):
            continue
        npc = entry.get("npc") or entry.get("speaker") or entry.get("name")
        line = entry.get("line") or entry.get("text")
        if npc and line:
            out.append({"npc": str(npc), "line": str(line)})
    return out


def _word_tokens(text: str) -> list[str]:
    """Case-folded word tokens used for echo comparison (keeps apostrophes)."""
    return re.findall(r"[a-z0-9']+", text.casefold())


def _longest_common_run(a: list[str], b: list[str]) -> int:
    """Length of the longest contiguous word run shared by both sequences."""
    match = SequenceMatcher(None, a, b, autojunk=False).find_longest_match(
        0, len(a), 0, len(b))
    return match.size


def _drop_echoed_dialogue(prose: str,
                          dialogue: list[dict[str, str]]) -> list[dict[str, str]]:
    """Drop dialogue entries whose line the narration already tells.

    Each spoken line must render exactly once: when the model closes the
    prose with a quote and also lists the same line in npc_dialogue, the
    player would otherwise read it twice (paragraph + speech block). Only
    near-whole-line echoes are removed; short lines and genuinely distinct
    dialogue always survive.
    """
    prose_tokens = _word_tokens(prose)
    kept: list[dict[str, str]] = []
    kept_tokens: list[list[str]] = []
    for entry in dialogue:
        tokens = _word_tokens(str(entry.get("line", "")))
        if len(tokens) >= _DIALOGUE_ECHO_MIN_WORDS:
            run = _longest_common_run(prose_tokens, tokens)
            if run >= _DIALOGUE_ECHO_MIN_RUN and run >= _DIALOGUE_ECHO_RATIO * len(tokens):
                continue  # already inside the narration — drop the echo
            if any(
                _longest_common_run(tokens, seen) >= _DIALOGUE_ECHO_RATIO * min(len(tokens), len(seen))
                for seen in kept_tokens
                if min(len(tokens), len(seen)) >= _DIALOGUE_ECHO_MIN_WORDS
            ):
                continue  # the model said the same thing twice; keep one copy
        kept.append(entry)
        kept_tokens.append(tokens)
    return kept


def _normalize_suggestions(raw: Any) -> list[dict[str, str]]:
    out: list[dict[str, str]] = []
    if not isinstance(raw, list):
        return out
    for entry in raw:
        if isinstance(entry, str) and entry.strip():
            label = entry.strip()
            out.append({"label": label, "command": label})
        elif isinstance(entry, dict):
            label = entry.get("label") or entry.get("text") or entry.get("action")
            command = entry.get("command") or label
            if label:
                out.append({"label": str(label), "command": str(command)})
    return out


def _unescape(raw: str) -> str:
    try:
        return json.loads(f'"{raw}"')
    except (json.JSONDecodeError, ValueError):
        return raw


def parse_narrator_payload(provider_text: str) -> NarratorPayload:
    """Recover the structured payload from a (possibly messy) completion.

    Tolerates markdown fences, commentary before or after the object, trailing
    commas, Python-style dicts, and a bare fragment that begins at the
    ``"narration"`` key. When the object cannot be parsed at all, the narration
    string (and any speaker/line pairs) are salvaged by pattern instead —
    raw JSON scaffolding is never handed back as prose.
    """
    text = (provider_text or "").strip()
    if not text:
        return NarratorPayload()

    candidates: list[str] = [m.group(1).strip() for m in _JSON_FENCE_RE.finditer(text)]
    candidates.append(text)

    data: dict | None = None
    for candidate in candidates:
        if not candidate:
            continue
        data = _try_loads(candidate)
        if data is None and candidate.lstrip().startswith('"narration"'):
            # Truncated fragment: close the object and try once more.
            fragment = candidate.lstrip()
            for attempt in (f"{{{fragment}}}", f"{{{fragment.rstrip().rstrip(',')}}}"):
                data = _try_loads(attempt)
                if data is not None:
                    break
        if data is None:
            data = _find_json_object(candidate)
        if data is not None:
            break

    narration: str | None = None
    dialogue: list[dict[str, str]] = []
    suggestions: list[dict[str, str]] = []
    if data is not None:
        raw_narration = data.get("narration")
        narration = str(raw_narration) if raw_narration else None
        dialogue = _normalize_dialogue(data.get("npc_dialogue") or data.get("dialogue"))
        suggestions = _normalize_suggestions(data.get("suggested_actions"))

    if not narration:
        # Broken-but-recognisable JSON: salvage the narration string, and any
        # speaker/line pairs, straight from the pattern.
        match = _NARRATION_FIELD_RE.search(text)
        if match is None:
            return NarratorPayload()
        narration = _unescape(match.group(1))
        if not dialogue:
            dialogue = [{"npc": _unescape(a), "line": _unescape(b)}
                        for a, b in _DIALOGUE_PAIR_RE.findall(text)]

    return NarratorPayload(narration=narration, dialogue=dialogue,
                           suggestions=suggestions, parsed=True)


def _target_words(length: str) -> tuple[int, int]:
    return WORD_TARGETS.get(length, WORD_TARGETS[DEFAULT_LENGTH])


def render_prose(provider_text: str, length: str = DEFAULT_LENGTH) -> str:
    """Best-effort prose extraction: prefer a JSON 'narration' field (however
    it is wrapped), else use the raw text trimmed to the length target. Raw
    JSON scaffolding is never returned as prose.
    """
    payload = parse_narrator_payload(provider_text)
    if payload.narration:
        return payload.narration
    text = provider_text or ""
    if any(hint in text for hint in _JSON_SCAFFOLD_HINTS):
        return TANGLED_NOTE
    words = text.split()
    lo, hi = _target_words(length)
    if len(words) > hi:
        return " ".join(words[:hi])
    if len(words) < lo:
        # Pad honestly: no invented facts, just close the beat.
        words += ["The", "moment", "holds,", "and", "what", "happens", "next", "is", "yours", "to", "decide."]
        words = words[:hi]
    return " ".join(words)


def narrate(ctx: PromptContext, provider: Provider | None = None,
            meter: MeterRegistry | None = None,
            campaign_id: str = "default",
            suggestions: list[dict[str, str]] | None = None,
            prefs: ContentPrefs | None = None) -> tuple[NarratorOutput, PromptBundle]:
    """Single narration call. Returns (output, prompt_bundle for tests)."""
    prov: Provider = provider or get_provider()
    bundle = assemble_prompt(ctx, role_system=build_role_prompt(Role.NARRATOR.value), prefs=prefs)
    result = prov.generate(f"{bundle.system}\n\n{bundle.user}", role=Role.NARRATOR.value,
                           max_tokens=NARRATOR_MAX_TOKENS)
    if meter is not None:
        meter.record(campaign_id, Role.NARRATOR.value,
                     result.prompt_tokens, result.completion_tokens)
    if result.completion_tokens >= NARRATOR_MAX_TOKENS:
        # Ran into the ceiling: the reply was probably cut mid-JSON and the
        # player got salvage. Logged (never shown) so the budget can be
        # re-tuned from evidence instead of complaints.
        bundle.ceiling_hit = True
        _log.warning("narrator ceiling hit (campaign=%s, model=%s, prompt_tokens=%s)",
                     campaign_id, result.model, result.prompt_tokens)
    payload = parse_narrator_payload(result.text)
    prose = payload.narration or render_prose(result.text, ctx.length)
    # A spoken line must never render twice (prose quote + dialogue echo).
    dialogue = _drop_echoed_dialogue(prose, payload.dialogue)
    # The model's own suggestions read the scene; scene buttons are the fallback.
    suggested = ([SuggestedActionOut(**s) for s in payload.suggestions]
                 or [SuggestedActionOut(**s) for s in (suggestions or [])])
    out = NarratorOutput(
        narration=prose,
        npc_dialogue=[NpcDialogue(**d) for d in dialogue],
        suggested_actions=suggested,
        length=ctx.length,
    )
    return out, bundle


def stream_narration(output: NarratorOutput, chunk_words: int = 20) -> Iterator[str]:
    """Stream prose in word chunks (streaming UI, §106)."""
    words = output.narration.split()
    for i in range(0, len(words), chunk_words):
        yield " ".join(words[i:i + chunk_words])


def acknowledge(action_summary: str) -> dict[str, Any]:
    return ack(action_summary)
