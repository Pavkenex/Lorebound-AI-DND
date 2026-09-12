"""Narrator service: prose generation with ack + streaming (t_ad5b30b4).

Flow: assemble prompt from retrieved context -> exactly ONE provider call ->
parse NarratorOutput. The model returns prose/proposals only; state changes
go through the AuthorityEngine (never the model).
"""
from __future__ import annotations

import json
from collections.abc import Iterator
from typing import Any

from app.modules.ai.metering import MeterRegistry
from app.modules.ai.providers import Provider, get_provider
from app.modules.ai.roles import Role, build_role_prompt
from app.modules.narrator.prompts import PromptBundle, PromptContext, assemble_prompt
from app.modules.narrator.schemas import (
    DEFAULT_LENGTH,
    WORD_TARGETS,
    NarratorOutput,
    SuggestedActionOut,
    ack,
)


def _target_words(length: str) -> tuple[int, int]:
    return WORD_TARGETS.get(length, WORD_TARGETS[DEFAULT_LENGTH])


def render_prose(provider_text: str, length: str = DEFAULT_LENGTH) -> str:
    """Best-effort prose extraction: prefer a JSON 'narration' field, else
    use the raw text trimmed to the length target."""
    try:
        data = json.loads(provider_text)
        if isinstance(data, dict) and data.get("narration"):
            return str(data["narration"])
    except (json.JSONDecodeError, ValueError):
        pass
    words = provider_text.split()
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
            suggestions: list[dict[str, str]] | None = None) -> tuple[NarratorOutput, PromptBundle]:
    """Single narration call. Returns (output, prompt_bundle for tests)."""
    prov: Provider = provider or get_provider()
    bundle = assemble_prompt(ctx, role_system=build_role_prompt(Role.NARRATOR.value))
    result = prov.generate(f"{bundle.system}\n\n{bundle.user}", role=Role.NARRATOR.value)
    if meter is not None:
        meter.record(campaign_id, Role.NARRATOR.value,
                     result.prompt_tokens, result.completion_tokens)
    prose = render_prose(result.text, ctx.length)
    out = NarratorOutput(
        narration=prose,
        suggested_actions=[SuggestedActionOut(**s) for s in (suggestions or [])],
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
