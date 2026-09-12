"""Exploration: regions, location state, travel, discovery, escalation, map, leads."""
from app.modules.exploration.discovery import (
    HIDDEN,
    DiscoveryMethod,
    attempt_discovery,
)
from app.modules.exploration.escalation import STAGE_ORDER, EscalationStage, EscalationTrack
from app.modules.exploration.leads import LeadStatus, seed_caravan_graph
from app.modules.exploration.location_state import LocationState, is_open, narrator_brief
from app.modules.exploration.map_api import POSITIONS, get_map_data
from app.modules.exploration.regions import get_ravenford_valley
from app.modules.exploration.travel import TravelMode, camp, forage, hunt, plan_travel, track


# t_4222d180 — hierarchy + Ravenford Valley set
def test_ravenford_valley_has_required_set():
    region = get_ravenford_valley()
    by_type = {}
    for loc in region.locations:
        by_type.setdefault(loc.type, []).append(loc.id)
        assert loc.parent_region == region.id
        assert loc.npcs is not None and loc.features is not None
    assert "ravenford" in by_type["town"]
    assert len(by_type["village"]) >= 2
    assert by_type["forest"] and by_type["ruin"] and by_type["mine"]
    assert by_type["crossing"] and by_type["tower"]
    assert len(by_type.get("wild", [])) >= 2


# t_dcdf7a9c — mutable state, secrets, hours
def test_lantern_hours_06_to_02():
    region = get_ravenford_valley()
    lantern = region.by_id("lantern-inn")
    assert lantern.open_hours == (6, 26)
    assert is_open(lantern, 6) and is_open(lantern, 12) and is_open(lantern, 23)
    assert is_open(lantern, 1)
    assert not is_open(lantern, 3) and not is_open(lantern, 5)


def test_narrator_gets_structured_data_not_secrets():
    region = get_ravenford_valley()
    lantern = region.by_id("lantern-inn")
    state = LocationState("lantern-inn")
    brief = narrator_brief(lantern, state, hour=12)
    assert brief["open_now"] is True
    assert brief["known_secrets"] == []
    assert "smuggler" not in " ".join(brief["features"])
    state.reveal_secret("smuggler entrance behind the cellar casks")
    assert narrator_brief(lantern, state, hour=12)["known_secrets"] == [
        "smuggler entrance behind the cellar casks"
    ]
    state.set_flag("militia_alert", True)
    assert narrator_brief(lantern, state, hour=12)["flags"]["militia_alert"] is True


# t_f7541b7a — travel modes are genuine trade-offs
def test_cautious_is_tradeoff_not_worse():
    normal = plan_travel(10, TravelMode.NORMAL)
    cautious = plan_travel(10, TravelMode.CAUTIOUS)
    quick = plan_travel(10, TravelMode.QUICK)
    assert cautious.time_min > normal.time_min > quick.time_min
    assert cautious.encounter_chance < normal.encounter_chance
    assert cautious.discovery_bonus > normal.discovery_bonus
    assert quick.fatigue > normal.fatigue
    assert cautious.supply_use < normal.supply_use


def test_field_actions_have_costs_and_yields():
    assert forage().minutes == 45
    assert hunt().minutes == 120
    assert track().minutes == 30
    assert camp(sheltered=True).fatigue < camp(sheltered=False).fatigue


# t_64d3b2e0 — hidden locations; accidental major reachable
def test_deliberate_discovery_with_awareness():
    chapel = next(h for h in HIDDEN if h.id == "sunken-chapel")
    assert chapel.major
    # Both rolls injected: the accident branch (accidental_p=0.06) must not be
    # able to hijack a deliberate-discovery assertion on an unseeded draw.
    found = attempt_discovery(chapel, awareness=18, methods_used=[DiscoveryMethod.EXPLORE],
                              roll=0.99, accident_roll=0.99)
    assert found.found and not found.accidental


def test_wrong_door_accidental_discovery():
    chapel = next(h for h in HIDDEN if h.id == "sunken-chapel")
    stumble = attempt_discovery(chapel, awareness=1, methods_used=[],
                                accident_roll=0.0)
    assert stumble.found and stumble.accidental
    assert stumble.via == DiscoveryMethod.ACCIDENT
    careful_miss = attempt_discovery(chapel, awareness=1, methods_used=[],
                                     accident_roll=0.99)
    assert not careful_miss.found


# t_fdc4b7f9 — escalation timeline, intervenable
def test_escalation_timeline_order_and_intervention():
    assert [s.value for s in STAGE_ORDER] == [
        "rumour", "dangerous_trade", "rising_prices", "mercenaries_hired", "attack",
    ]
    track_ = EscalationTrack("bandits")
    assert track_.stage == EscalationStage.RUMOUR
    stages = []
    while (nxt := track_.advance()) is not None:
        stages.append(nxt)
    assert stages[-1] == EscalationStage.ATTACK
    assert "attacked" in track_.current_effect()

    track2 = EscalationTrack("bandits")
    track2.advance()
    track2.intervene("paid off the caravan guards")
    assert track2.resolved
    assert track2.advance() is None


# t_cedef501 — map API, inaccurate until confirmed
def test_map_accuracy_until_confirmed():
    region = get_ravenford_valley()
    known = {"ravenford", "ember-hollow"}
    data = get_map_data(region, known_ids=known, confirmed_ids={"ravenford"},
                        rumours=[{"text": "lights in the monastery"}],
                        incidents=[{"at": "silt-ford", "what": "wagon missing"}])
    by_id = {loc["id"]: loc for loc in data["locations"]}
    assert by_id["ravenford"]["accuracy"] == "confirmed"
    assert (by_id["ravenford"]["x"], by_id["ravenford"]["y"]) == POSITIONS["ravenford"]
    assert by_id["ember-hollow"]["accuracy"] == "approximate"
    assert (by_id["ember-hollow"]["x"], by_id["ember-hollow"]["y"]) != POSITIONS["ember-hollow"]
    assert data["undiscovered_areas"] == len(region.locations) - 2
    assert data["roads"] and data["rumours"] and data["incidents"]


# t_d5b6fce3 — lead graph updates on confirm/invalidate
def test_lead_graph_confirm_invalidate():
    g = seed_caravan_graph()
    viz = g.to_viz_data()
    assert len(viz["nodes"]) == 6 and len(viz["edges"]) == 5
    g.confirm_lead("destroyed-wagon")
    g.invalidate_lead("merchant-guild")
    statuses = {n["id"]: n["status"] for n in g.to_viz_data()["nodes"]}
    assert statuses["destroyed-wagon"] == LeadStatus.CONFIRMED.value
    assert statuses["merchant-guild"] == LeadStatus.INVALIDATED.value
    assert statuses["smugglers"] == LeadStatus.SUSPECTED.value
