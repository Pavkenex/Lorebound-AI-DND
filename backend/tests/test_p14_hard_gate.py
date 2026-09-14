"""P14 hard gate: no authored story prose may exist on the play path.

Owner ruling (2026-09-14, verbatim): "There shouldnt be any scripted
answers. Every narration/dialogue should be ai." Everything the player
reads as STORY — scene narration, NPC dialogue, opening/prologue prose,
return greetings, micro-scenes, repeat responses, diminishing replies —
is written by the MODEL per turn. INTERFACE text stays code: the state
machine, dice/check mechanics and their dice-voice prompts, button
labels, scene labels, short action echoes ("You look closer.").

This module is the static half of the gate (the dynamic half lives in
the behaviour tests that require every beat to be a model call). It
scans the play-path packages for the two shapes authored prose used to
take, and fails the build if either returns:

  1. module-level story-prose constants (>= MIN_NAMED_LEN chars)
  2. inline string literals >= MIN_INLINE_LEN chars inside beat/engine
     handlers (a prose block sneaking back into a handler)

Length floors sit far above any legitimate interface string so the
scan never needs to enumerate every short label: action echoes and
feed markers are a handful of chars, while prose blocks are hundreds.
The floors are deliberately permissive on purpose — a 200-char
"interface" string would already read as prose to the player, and no
such string exists on the play path today (checked at conversion).
"""

from __future__ import annotations

import ast
from pathlib import Path

import pytest

# --- scan surface: every package a player's turn can reach ----------------
PLAY_PATH = Path(__file__).resolve().parents[1] / "app" / "modules"
SCAN_DIRS = (
    PLAY_PATH / "play",
    PLAY_PATH / "story",
    PLAY_PATH / "actions",
)
# narrator/* is the narrator's own interface (prompt templates), not story
# prose; engine/ and the legacy ai modules are out of scope for this card.

MIN_NAMED_LEN = 200      # module-level constant: prose if longer
MIN_INLINE_LEN = 320     # handler literal: prose if longer

# Interface strings that are module-level and long enough to trip the scan.
# Entries: ("package", "name") — keep this list as short as possible.
NAMED_ALLOWLIST = {
    ("play", "BEAT_SCHEMA"),          # the narrator's output contract
    ("actions", "_ATHLETICS_FRAG"),   # a regex fragment
}

_PROSE_TELLS = (
    "the player", "they walk", "she says", "he says", "you look",
    "the inn", "the road", "rain", "night air", "torchlight",
)


def _docstring_nodes(tree: ast.Module) -> set[ast.Constant]:
    """The Constant nodes that are docstrings (not story prose)."""
    docs: set[ast.Constant] = set()
    for node in ast.walk(tree):
        if isinstance(node, (ast.Module, ast.ClassDef, ast.FunctionDef)) \
                and ast.get_docstring(node) is not None \
                and node.body and isinstance(node.body[0], ast.Expr) \
                and isinstance(node.body[0].value, ast.Constant):
            docs.add(node.body[0].value)
    return docs


def _candidates() -> list[tuple[str, int, str, str]]:
    """Yield (package, lineno, name_or_inline, source_snippet)."""
    found: list[tuple[str, int, str, str]] = []
    for pkg in SCAN_DIRS:
        if not pkg.is_dir():
            continue
        for path in sorted(pkg.rglob("*.py")):
            text = path.read_text(encoding="utf-8")
            try:
                tree = ast.parse(text)
            except SyntaxError:
                continue
            docs = _docstring_nodes(tree)
            named_values: set[ast.AST] = set()
            source_lines = text.splitlines()
            for node in ast.walk(tree):
                if isinstance(node, ast.Assign):
                    for tgt in node.targets:
                        if (
                            isinstance(tgt, ast.Name)
                            and isinstance(node.value, ast.Constant)
                            and isinstance(node.value.value, str)
                            and len(node.value.value) >= MIN_NAMED_LEN
                        ):
                            named_values.add(node.value)
                            found.append((pkg.name, node.lineno, tgt.id,
                                          node.value.value))
                elif (
                    isinstance(node, ast.Constant)
                    and isinstance(node.value, str)
                    and node not in docs
                    and node not in named_values
                    and len(node.value) >= MIN_INLINE_LEN
                    and not _is_regex(node, source_lines)
                ):
                    found.append((pkg.name, node.lineno, "<literal>",
                                  node.value))
    return found


def _is_regex(node: ast.Constant, source_lines: list[str]) -> bool:
    """True when the literal's source form is a raw regex string (r"...")."""
    try:
        seg = source_lines[node.lineno - 1][node.col_offset:]
    except IndexError:
        return False
    return seg.lstrip().startswith(("r\"", "r'", "r\"\"\"", "r'''"))


@pytest.fixture(scope="module")
def prose_candidates() -> list[tuple[str, int, str, str]]:
    return _candidates()


def test_no_story_prose_constants_anywhere_on_the_play_path(
    prose_candidates,
) -> None:
    """P14 hard gate: no named prose constant may return to the play path."""
    offenders = [
        (pkg, line, name)
        for (pkg, line, name, _src) in prose_candidates
        if name != "<literal>" and (pkg, name) not in NAMED_ALLOWLIST
    ]
    assert not offenders, (
        "Authored story-prose constants returned to the play path (P14 "
        "ruling): " + ", ".join(f"{pkg}:{line} {name}"
                               for pkg, line, name in offenders)
    )


def test_no_inline_story_prose_literals_in_handlers(prose_candidates) -> None:
    """P14 hard gate: prose may not hide as an inline handler literal."""
    offenders = [
        (pkg, line)
        for (pkg, line, name, _src) in prose_candidates
        if name == "<literal>"
    ]
    assert not offenders, (
        "Inline story-prose literals returned to the play path (P14 "
        "ruling): " + ", ".join(f"{pkg}:{line}" for pkg, line in offenders)
    )


def test_gate_catches_a_reintroduced_prose_constant() -> None:
    """Mutation probe: the gate must fail when prose sneaks back in.

    This is the gate's self-test — a gate nobody has seen fail is
    decoration. It injects the exact shape the inventory found (a
    module-level prose constant) into a scratch module under the play
    path and proves the scan reports it.
    """
    scratch = PLAY_PATH / "play" / "_p14_gate_probe.py"
    scratch.write_text(
        '"""Scratch probe for the P14 gate self-test (never imported)."""\n'
        'PROBE_WELCOME_LINE = ("Come in from the rain, the hearth is lit and "\n'
        '                        "Marla is already pouring. She says the "\n'
        '                        "travellers came through two days ago and "\n'
        '                        "left north at dawn. The night air smells of "\n'
        '                        "woodsmoke and wet wool, and the road outside "\n'
        '                        "is a ribbon of mud under a half moon.")\n',
        encoding="utf-8",
    )
    try:
        cands = _candidates()
        hits = [
            (pkg, name)
            for (pkg, _line, name, _src) in cands
            if name == "PROBE_WELCOME_LINE"
        ]
        assert hits, "gate failed to see the reintroduced prose constant"
    finally:
        scratch.unlink(missing_ok=True)


def test_gate_notices_named_prose_matching_nothing_short() -> None:
    """A sub-floor constant is interface by construction, and the scan
    must agree (no false positive on short interface strings)."""
    scratch = PLAY_PATH / "play" / "_p14_gate_probe_short.py"
    scratch.write_text(
        '"""Scratch probe: a short action echo must pass the gate."""\n'
        'ACK_LOOK_CLOSER = "You look closer."\n',
        encoding="utf-8",
    )
    try:
        cands = _candidates()
        hits = [
            (pkg, name)
            for (pkg, _line, name, _src) in cands
            if name == "ACK_LOOK_CLOSER"
        ]
        assert not hits, "short interface echo flagged as story prose"
    finally:
        scratch.unlink(missing_ok=True)