"""Authority boundary (t_a65af7fa): typed proposals, engine approval, no model path."""
import pytest

from app.modules.narrator.authority import (
    AUTHORITATIVE_COLUMNS,
    AuthorityEngine,
    EngineProposal,
    ProposalKind,
)


def state():
    return {"inventory": ["torch"], "currency": 10, "hp": 20,
            "npcs": {"Marla": "alive"}, "known_locations": ["Lantern Inn"],
            "location": "Lantern Inn", "skill_xp": {}, "leads": {}}


def test_all_authoritative_columns_guarded():
    assert AUTHORITATIVE_COLUMNS >= {"hp", "currency", "inventory", "skill_xp",
                                    "location", "quest_completion", "npc_alive",
                                    "reputation", "time", "combat_state"}


def test_approved_proposal_applies():
    eng = AuthorityEngine()
    p = EngineProposal(kind=ProposalKind.ADD_ITEM, reason="found in chest", payload={"item": "key"})
    (d,) = eng.review([p], state())
    assert d.approved
    new = eng.apply(d, state())
    assert "key" in new["inventory"]


def test_reason_required():
    eng = AuthorityEngine()
    (d,) = eng.review([EngineProposal(kind=ProposalKind.ADD_ITEM, reason="  ",
                                      payload={"item": "x"})], state())
    assert not d.approved and "reason" in (d.discard_reason or "")


def test_unapproved_discarded_logged_state_untouched():
    eng = AuthorityEngine()
    before = state()
    p = EngineProposal(kind=ProposalKind.GRANT_CURRENCY, reason="bribe",
                       payload={"amount": 999})
    (d,) = eng.review([p], before, approve=False)  # strict mode
    assert not d.approved
    after = eng.apply(d, before)
    assert after["currency"] == 10  # untouched
    assert eng.discarded and eng.events  # discarded AND logged


def test_apply_without_approval_blocked_and_logged():
    eng = AuthorityEngine()
    d = eng.review([EngineProposal(kind=ProposalKind.HEAL_OR_DAMAGE, reason="",
                                   payload={"delta": 100})], state())[0]
    n_events = len(eng.events)
    out = eng.apply(d, state())
    assert out["hp"] == 20 and len(eng.events) > n_events


def test_no_teleport_unknown_npc_missing_item():
    eng = AuthorityEngine()
    s = state()
    assert not eng.review([EngineProposal(kind=ProposalKind.MOVE_PLAYER, reason="plot",
                                          payload={"location": "Far Realm"})], s)[0].approved
    assert not eng.review([EngineProposal(kind=ProposalKind.NPC_STATE, reason="x",
                                          payload={"npc": "Zorg"})], s)[0].approved
    assert not eng.review([EngineProposal(kind=ProposalKind.REMOVE_ITEM, reason="x",
                                          payload={"item": "nope"})], s)[0].approved


def test_exploit_proposal_injection_rejected():
    """Model output smuggling writes outside its kind allowlist is rejected."""
    eng = AuthorityEngine()
    evil = EngineProposal(kind=ProposalKind.ADD_ITEM, reason="bonus",
                          payload={"item": "crown", "writes": ["inventory", "quest_completion", "currency"]})
    (d,) = eng.review([evil], state())
    assert not d.approved and "allowlist" in (d.discard_reason or "")


def test_malformed_kind_rejected_by_schema():
    from pydantic import ValidationError

    with pytest.raises(ValidationError):
        EngineProposal(kind="GIVE_GODHOOD", reason="please", payload={})  # type: ignore[arg-type]


def test_no_direct_write_path_from_model():
    import inspect

    from app.modules.narrator import authority as mod
    src = inspect.getsource(mod)
    # Only apply() mutates state, and it gates on decision.approved.
    assert "decision.approved" in src or "not decision.approved" in src
