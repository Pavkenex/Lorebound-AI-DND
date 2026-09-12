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


def test_nsfw_on_permits_mature_but_forbids_disallowed():
    directive = ContentPrefs(nsfw=True).describe_for_prompt()
    lowered = directive.lower()
    assert "mature" in lowered
    assert "underage" in lowered
    assert "non-consensual" in lowered
    # Must not claim explicit content is forbidden outright when opted in.
    assert "no explicit sexual content" not in lowered


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
    assert "no horror" in lowered
    assert "underage" in lowered
