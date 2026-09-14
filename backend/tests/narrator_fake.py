"""Test double for the narrator (P14): writes prose, records its prompts.

The play path narrates *every* beat through the provider, so a test that wants
to inspect what the engine promised the narrator — or prove that the words the
player reads are the model's and not a constant — needs a provider that

- records every prompt it is handed (``prompts``, ``narrator_prompts``),
- answers narrator calls with the beat's own event id and facts echoed back
  inside a marked narration (``MARK``), and
- answers every other role (interpreter, extractor) exactly like the stub, so
  the pipeline paths behave as they always did.
"""
from __future__ import annotations

import json
import re
from typing import Any

from app.modules.ai.providers import ProviderResult, _default_canned

#: Bumped per call: two calls never produce the same narration text, which is
#: what "every diminishing reply is written fresh" means at the double's level.
MARK = "[[model#"

_BEAT_BLOCK = re.compile(r"^\[Beat\]\n(?P<body>.*?)(?=\n\[|\Z)", re.DOTALL | re.MULTILINE)
_BEAT_HEADER = re.compile(r"^event: (?P<event>\S+)", re.MULTILINE)
_FACT_LINE = re.compile(r"^- (?P<fact>.*)$", re.MULTILINE)
_SPEAKERS_LINE = re.compile(r"^speakers[^\n]*\): (?P<names>[^\n]+)$", re.MULTILINE)


class RecordingNarrator:
    """Deterministic stand-in for a connected model on the play path (P14)."""

    model_name = "recording-test-narrator"

    def __init__(self, canned: dict[str, str] | None = None) -> None:
        self.canned = dict(canned or {})
        self.prompts: list[str] = []
        self.roles: list[str] = []
        self.calls: list[dict[str, Any]] = []

    # -- the provider protocol ------------------------------------------------
    def generate(self, prompt: str, *, role: str = "narrator",
                 max_tokens: int = 600, **kwargs: Any) -> ProviderResult:
        self.prompts.append(prompt)
        self.roles.append(role)
        self.calls.append(
            {"role": role, "prompt_chars": len(prompt), "max_tokens": max_tokens}
        )
        if role != "narrator" or role in self.canned:
            text = self.canned.get(role) or _default_canned(role, prompt)
        else:
            text = self._narrator_text(prompt, len(self.prompts))
        return ProviderResult(
            text=text,
            model=self.model_name,
            prompt_tokens=len(prompt) // 4,
            completion_tokens=len(text) // 4,
            metadata={"role": role, "recording": True},
        )

    # -- introspection for assertions ----------------------------------------
    @property
    def narrator_prompts(self) -> list[str]:
        return [p for p, r in zip(self.prompts, self.roles) if r == "narrator"]

    @property
    def narrator_calls(self) -> int:
        return self.roles.count("narrator")

    @staticmethod
    def beat_block(prompt: str) -> str:
        """The prompt's [Beat] block alone (fact lines live there)."""
        match = _BEAT_BLOCK.search(prompt)
        return match.group("body") if match else ""

    @classmethod
    def beat_of(cls, prompt: str) -> str:
        match = _BEAT_HEADER.search(cls.beat_block(prompt))
        return match.group("event") if match else ""

    @classmethod
    def facts_of(cls, prompt: str) -> list[str]:
        return [m.group("fact").strip() for m in _FACT_LINE.finditer(cls.beat_block(prompt))]

    @classmethod
    def speakers_of(cls, prompt: str) -> list[str]:
        match = _SPEAKERS_LINE.search(cls.beat_block(prompt))
        if not match:
            return []
        return [n.strip() for n in match.group("names").split(",") if n.strip()]

    # -- the prose -----------------------------------------------------------
    def _narrator_text(self, prompt: str, call: int) -> str:
        event = self.beat_of(prompt) or "free-text"
        facts = self.facts_of(prompt)
        speakers = self.speakers_of(prompt)
        narration = (
            f"{MARK}{call}]] {event} — "
            + (" / ".join(facts) if facts else "no facts were handed over")
            + f" ({len(facts)} facts)"
        )
        dialogue = [{"npc": name, "line": f"{MARK}{call}]] line for {name}"} for name in speakers]
        return json.dumps({"narration": narration, "npc_dialogue": dialogue})
