"""t_5f738da1: fixture flows — talk/inspect/steal/fight/discover-lead/leave/return."""

import pytest

try:
    from app.content.fixture import (
        ARIC,
        LANTERN_INN,
        MARLA,
        MISSING_TRAVELERS_LEAD,
        SCRIPTED_FLOWS,
        FixtureWorld,
        new_fixture_world,
    )
    from app.content.skills import SKILL_KEYS
except Exception as exc:  # other streams still landing
    pytest.skip(f"content stream not landed: {exc}", allow_module_level=True)


def test_fixture_entities_present():
    assert ARIC["name"] == "Aric"
    assert LANTERN_INN["name"] == "Lantern Inn"
    assert MARLA["name"] == "Marla"
    assert MISSING_TRAVELERS_LEAD["id"] == "missing-travelers"
    assert set(SKILL_KEYS) == {
        "persuasion", "intimidation", "investigation", "stealth", "swordsmanship",
    }
    assert set(SCRIPTED_FLOWS) == {
        "talk", "inspect", "steal", "fight", "discover-lead", "leave", "return",
    }


def test_all_seven_flows_run_in_order():
    w: FixtureWorld = new_fixture_world()
    assert "travelers" in w.talk_to_marla("travelers").text
    assert w.inspect_room("common-room").state_changes["inspected"] == "common-room"
    assert w.steal("storeroom-strongbox").state_changes["stolen"] == "storeroom-strongbox"
    assert w.start_fight("drunk-mercenary").state_changes["fought"] == "true"
    assert w.discover_lead().state_changes["stage"] == "rumored"
    assert w.leave_inn().state_changes["location"] == "northern-road"
    back = w.return_to_inn()
    assert back.state_changes["location"] == "lantern-inn"
    assert w.snapshot()["visits"] == 2


def test_marla_remembers_on_return():
    w = new_fixture_world()
    w.talk_to_marla("travelers")
    w.steal("storeroom-strongbox")
    w.start_fight("drunk-mercenary")
    w.discover_lead()
    w.leave_inn()
    greeting = w.return_to_inn().text
    assert "Marla" in greeting and "remembers" in greeting
    assert "missing silver" in greeting  # the theft
    assert "brawl" in greeting  # the fight
    assert "travelers" in greeting  # the talk + lead


def test_marla_clean_visit_has_no_false_memories():
    w = new_fixture_world()
    w.leave_inn()
    greeting = w.return_to_inn().text
    assert "missing silver" not in greeting
    assert "brawl" not in greeting
