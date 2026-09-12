"""ContentPrefs directive strings + narrator prompt wiring."""
from __future__ import annotations

from app.modules.narrator.prefs import ContentPrefs
from app.modules.narrator.prompts import PromptContext, assemble_prompt


def test_defaults_standard_nsfw_off():
    prefs = ContentPrefs()
    assert prefs.nsfw is False
    directive = prefs.describe_for_prompt()
    assert "no explicit sexual content" in directive.lower()
    for axis in ("violence", "horror", "romance", "language"):
        assert axis in directive.lower()


def test_nsfw_on_is_uncensored_master_switch():
    directive = ContentPrefs(nsfw=True).describe_for_prompt()
    lowered = directive.lower()
    # Explicit opt-in lifts the limits outright...
    assert "uncensored" in lowered
    assert "explicit sexual content" in lowered
    assert "no fade" in lowered or "nothing fades to black" in lowered
    # ...the per-axis caps are superseded while it is on.
    assert "violence:" not in lowered
    assert "romance:" not in lowered
    # ...and must not claim explicit content is forbidden when opted in.
    assert "no explicit sexual content" not in lowered
    # One hard exclusion always stands.
    assert "minor" in lowered


def test_nsfw_on_ignores_off_axes():
    """Uncensored mode supersedes restrictive per-axis values."""
    directive = ContentPrefs(nsfw=True, romance="off", violence="off").describe_for_prompt()
    lowered = directive.lower()
    assert "uncensored" in lowered
    assert "no romantic content" not in lowered
    assert "no graphic violence" not in lowered


def test_levels_render_in_directive():
    directive = ContentPrefs(violence="off", language="reduced").describe_for_prompt()
    lowered = directive.lower()
    assert "no graphic violence" in lowered
    assert "mild language only" in lowered


def test_prompt_assembly_includes_boundaries_by_default():
    bundle = assemble_prompt(PromptContext(player_action="hi"))
    assert "no explicit sexual content" in bundle.user.lower()


def test_prompt_assembly_honors_explicit_prefs():
    prefs = ContentPrefs(nsfw=True, horror="off")
    bundle = assemble_prompt(PromptContext(player_action="hi"), prefs=prefs)
    lowered = bundle.user.lower()
    assert "uncensored mode" in lowered
    assert "minor" in lowered


def test_legacy_axis_vocabulary_normalizes():
    """The shipped frontend sent low/clean/mild; those must not fail parsing —
    a single mismatched value used to silently drop the entire prefs payload."""
    prefs = ContentPrefs.model_validate(
        {"violence": "low", "horror": "low", "romance": "off", "language": "clean"}
    )
    assert prefs.violence == "reduced"
    assert prefs.horror == "reduced"
    assert prefs.language == "reduced"


def test_header_payload_from_frontend_keeps_nsfw():
    from app.modules.play.router import _parse_prefs

    raw = '{"violence":"low","horror":"low","romance":"off","language":"clean","nsfw":true}'
    parsed = _parse_prefs(raw)
    assert parsed is not None
    assert parsed.nsfw is True
    assert "uncensored mode" in parsed.describe_for_prompt().lower()


def test_header_payload_garbage_falls_back_to_defaults():
    from app.modules.play.router import _parse_prefs

    assert _parse_prefs(None) is None
    assert _parse_prefs("not json") is None
    # Unknown enum values still parse to defaults rather than killing the payload? No:
    # a genuinely unknown level is a client bug; pydantic rejects it and the router
    # falls back to defaults (None) rather than guessing.
    assert _parse_prefs('{"violence":"nonsense"}') is None
