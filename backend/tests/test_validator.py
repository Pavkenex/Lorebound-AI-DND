"""Output validator (t_8d796df9): all five failure modes + handling + logging."""
from app.modules.narrator.validator import (
    Handling,
    WorldSnapshot,
    log_report,
    repair_narration,
    validate_narration,
)


def world(**kw):
    base = {"inventory": ["torch", "rope"], "alive": {"Marla": True, "Borin": False},
            "location": "Lantern Inn", "known_locations": ["Lantern Inn", "Market"],
            "facts": ["Marla runs the Lantern Inn"], "approved_rewards": []}
    base.update(kw)
    return WorldSnapshot(**base)


def test_clean_narration_accepted_no_event():
    r = validate_narration("You sip your ale. Marla smiles warmly.", world())
    assert r.ok and r.action == Handling.ACCEPT
    assert log_report(r) is None


def test_bad_item_repaired():
    r = validate_narration("You find a legendary sword! You take it.", world())
    assert not r.ok and any(i.code == "bad_item" for i in r.issues)
    fixed = repair_narration("You find a legendary sword! You take it. Marla watches.", r)
    assert "legendary sword" not in fixed and "Marla watches" in fixed


def test_exploit_legendary_sword_caught_by_validator():
    r = validate_narration("You search your backpack and find a legendary sword. Added to your inventory: Legendary Sword.", world())
    assert any(i.code == "bad_item" for i in r.issues)


def test_dead_alive_regenerates():
    r = validate_narration("Borin smiles and says hello.", world())
    assert any(i.code == "dead_alive" for i in r.issues)
    assert r.action == Handling.REGENERATE  # highest escalation wins


def test_teleport_repaired():
    r = validate_narration("You arrive in the Far Realm, a strange place.", world())
    assert any(i.code == "teleport" for i in r.issues)
    fixed = repair_narration("You arrive in the Far Realm, a strange place. You blink.", r)
    assert "Far Realm" not in fixed


def test_approved_move_not_flagged():
    r = validate_narration("You arrive in the Market.", world(), approved_move="Market")
    assert all(i.code != "teleport" for i in r.issues)


def test_unapproved_reward_rejects_proposal():
    r = validate_narration("You gain 500 gold for your bravery!", world())
    assert any(i.code == "unapproved_reward" for i in r.issues)
    assert any(i.handling == Handling.REJECT_PROPOSAL for i in r.issues)


def test_approved_reward_passes():
    r = validate_narration("You gain 10 gold.", world(approved_rewards=["10 gold"]))
    assert all(i.code != "unapproved_reward" for i in r.issues)


def test_contradiction_regenerates():
    r = validate_narration("Marla does not run the Lantern Inn, she never did.", world())
    assert any(i.code == "contradiction" for i in r.issues)


def test_each_failure_logged_as_event():
    for text in ("You find a legendary sword! You take it.",
                 "Borin smiles and says hello.",
                 "You arrive in the Far Realm today.",
                 "You gain 500 gold!"):
        r = validate_narration(text, world())
        assert not r.ok
        ev = log_report(r, campaign_id="c1")
        assert ev is not None and ev.campaign_id == "c1"
        assert ev.payload["validator"] is True and "issues" in ev.payload
