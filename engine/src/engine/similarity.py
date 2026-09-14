"""Similarity utilities for salience scoring and contradiction scan (spec §3.1).

``LexicalSimilarity`` is the dependency-free default; ``EmbeddingSimilarity``
wraps any ``embed(text) -> vector`` callable (e.g. a provider embedding
endpoint) and is only used when one is supplied. Both are deterministic.
"""
from __future__ import annotations

import math
import re
from collections.abc import Callable, Sequence
from typing import Protocol

_WORD_RE = re.compile(r"[a-z0-9']+")
_STOPWORDS = frozenset(
    "a an and are as at be by for from in is it of on or that the to was were with you your i he she they them his her their we our".split()
)


def _tokens(text: str) -> list[str]:
    return [t for t in _WORD_RE.findall(text.lower()) if t not in _STOPWORDS and len(t) > 1]


class Similarity(Protocol):
    def score(self, a: str, b: str) -> float:
        """Similarity of two texts in [0, 1]."""
        ...


class LexicalSimilarity:
    """Deterministic binary-token cosine in [0,1]. No dependencies."""

    def score(self, a: str, b: str) -> float:
        ta, tb = set(_tokens(a)), set(_tokens(b))
        if not ta or not tb:
            return 0.0
        inter = len(ta & tb)
        if inter == 0:
            return 0.0
        return inter / math.sqrt(len(ta) * len(tb))


class EmbeddingSimilarity:
    """Cosine similarity over vectors from a supplied ``embed`` callable."""

    def __init__(self, embed: Callable[[str], Sequence[float]]) -> None:
        self._embed = embed

    def score(self, a: str, b: str) -> float:
        va, vb = self._embed(a), self._embed(b)
        dot = sum(x * y for x, y in zip(va, vb, strict=False))
        na = math.sqrt(sum(x * x for x in va))
        nb = math.sqrt(sum(y * y for y in vb))
        if na == 0 or nb == 0:
            return 0.0
        return max(-1.0, min(1.0, dot / (na * nb)))


def best_context_score(text: str, contexts: Sequence[str], sim: Similarity) -> float:
    """Highest similarity of ``text`` against a list of context strings."""
    best = 0.0
    for ctx in contexts:
        if not ctx:
            continue
        best = max(best, sim.score(text, ctx))
    return best
