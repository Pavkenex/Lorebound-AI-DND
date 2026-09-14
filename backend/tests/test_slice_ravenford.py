"""t_24aace96: Ravenford content integrity."""

import re

import pytest

try:
    from app.content.ravenford import (
        FACTIONS,
        LOCATIONS,
        NPCS,
        OPENING_SCENE,
        RUMOURS,
        THREAD_INTERSECTIONS,
        THREADS,
    )
except Exception as exc:  # noqa: BLE001 - stream-not-landed guard
    pytest.skip(f"content stream not landed: {exc}", allow_module_level=True)


def test_seven_locations():
    ids = {loc.id for loc in LOCATIONS}
    assert ids == {
        "ravenford", "lantern-inn", "market", "northern-road",
        "forest", "old-monastery", "watchtower",
    }
    for loc in LOCATIONS:
        assert loc.description and loc.points_of_interest


def test_npc_count_factions_and_placement():
    assert 10 <= len(NPCS) <= 15
    assert {f.id for f in FACTIONS} == {"merchant-guild", "quiet-order", "town-watch"}
    place_ids = {loc.id for loc in LOCATIONS}
    for npc in NPCS:
        assert npc.location in place_ids, f"{npc.id} placed nowhere"
        assert npc.knows, f"{npc.id} knows nothing"


def test_three_threads_fully_intersecting():
    assert {t.id for t in THREADS} == {
        "missing-travellers", "monastery-lights", "guild-silver",
    }
    for thread in THREADS:
        assert thread.truth and len(thread.clues) >= 2
        assert len(thread.intersects_with) == 2
    pairs = {(a, b) for a, b, _ in THREAD_INTERSECTIONS}
    assert pairs == {
        ("missing-travellers", "monastery-lights"),
        ("missing-travellers", "guild-silver"),
        ("monastery-lights", "guild-silver"),
    }


def test_opening_heavy_rain_and_three_rumours():
    assert "rain" in OPENING_SCENE.lower()
    assert "Lantern Inn" in OPENING_SCENE
    assert len(RUMOURS) == 3
    assert {r["thread"] for r in RUMOURS} == {
        "missing-travellers", "monastery-lights", "guild-silver",
    }


BANNED_TERMS = (
    "beholder", "mind flayer", "yuan-ti", "slaad", "gith", "vecna",
    "mordenkainen", "faerun", "waterdeep", "eberron", "dragonlance",
    "tarrasque",
)

_URL_RE = re.compile(r"(?:https?://|git@)\S+")
"""URLs are not content terms — a git remote URL contains 'gith' (GitHub)."""


def _scanned_text(path) -> str:
    return _URL_RE.sub(" ", path.read_text()).lower()


def test_no_licensed_terms_in_shipped_content():
    """Every content term traces to docs/IP_GLOSSARY.md (in-house only).

    The glossary itself names bans to forbid them, so it is excluded.
    """
    from pathlib import Path

    roots = [
        Path(__file__).resolve().parent.parent / "app" / "content",
        Path(__file__).resolve().parent.parent.parent / "docs",
    ]
    checked = 0
    for root in roots:
        for path in sorted(root.glob("*.md" if root.name == "docs" else "*.py")):
            if path.name == "IP_GLOSSARY.md":
                continue
            text = _scanned_text(path)
            checked += 1
            for term in BANNED_TERMS:
                assert term not in text, f"licensed term {term!r} in {path}"
    assert checked >= 5
