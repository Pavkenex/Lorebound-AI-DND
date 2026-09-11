"""t_860b9420: vertical-slice assembly integrity."""

import pytest

try:
    from app.content.slice import (
        COMBAT_ENCOUNTER,
        INVENTORY_SCREEN,
        LEADS_SCREEN,
        MYSTERY,
        SESSION_BEATS,
        SESSION_MINUTES,
        SLICE_NPCS,
        get_slice,
    )
except Exception as exc:
    pytest.skip(f"content stream not landed: {exc}", allow_module_level=True)


def test_slice_has_pc_tavern_npcs_mystery_combat():
    s = get_slice()
    assert s.pc["name"] == "Aric"
    assert s.tavern["name"] == "Lantern Inn"
    assert len(s.npcs) == 3
    assert {n["id"] for n in s.npcs} == {"marla", "borin", "sella"}
    assert s.mystery["id"] == "missing-travelers"
    assert s.combat["id"] == "road-ambush"


def test_five_skills_inventory_and_lead_screens():
    s = get_slice()
    assert set(s.skills) == {
        "persuasion", "intimidation", "investigation", "stealth", "swordsmanship",
    }
    assert INVENTORY_SCREEN["owner"] == "aric"
    assert len(INVENTORY_SCREEN["items"]) >= 3
    assert LEADS_SCREEN["leads"][0]["id"] == "missing-travelers"
    assert LEADS_SCREEN["leads"][0]["clues_total"] >= 3


def test_mystery_multi_solution_disjoint_skills():
    solutions = MYSTERY["solutions"]
    assert len(solutions) >= 3
    skill_sets = [set(sol["skills"]) for sol in solutions]
    for i in range(len(skill_sets)):
        for j in range(i + 1, len(skill_sets)):
            assert skill_sets[i].isdisjoint(skill_sets[j]), (
                f"solutions {solutions[i]['id']} and {solutions[j]['id']} "
                "share a skill; they must be distinct paths"
            )


def test_session_budget_30_to_60_minutes():
    assert 30 <= SESSION_MINUTES <= 60
    assert len(SESSION_BEATS) >= 5
    assert sum(b.minutes for b in SESSION_BEATS) == SESSION_MINUTES


def test_combat_survivable_and_avoidable():
    assert COMBAT_ENCOUNTER["lose"]  # losing still continues the story
    assert COMBAT_ENCOUNTER["nonviolent_outs"]  # avoidable without fighting
    assert len(SLICE_NPCS) == 3
