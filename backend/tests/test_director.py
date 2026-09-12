"""Story Director pass & pacing (t_2921a87c)."""
from app.modules.exploration.director import (
    PACING_ORDER,
    DirectorProposal,
    PacingStage,
    engine_decide,
    evaluate_pass,
    next_pacing,
    validate_proposal,
)


def test_director_proposes_engine_decides():
    proposals = evaluate_pass({
        "unresolved_threads": ["missing caravan"],
        "ignored_threats": ["bandits"],
        "needs_recovery": True,
    })
    assert len(proposals) == 3
    assert all(p.approved is None for p in proposals)  # proposals are pending
    decision = engine_decide(proposals[0], True, "good nudge")
    assert decision.approved is True
    assert proposals[0].approved is True
    no = engine_decide(proposals[1], False, "too soon")
    assert no.approved is False


def test_no_conspiracy_from_tavern_talk():
    sneaky = DirectorProposal(
        id="x", suggestion="A drunk reveals the ancient conspiracy",
        rationale="drama", pacing=PacingStage.DISCOVERY,
        context="tavern_conversation", supporting_leads=0,
    )
    valid, _ = validate_proposal(sneaky)
    assert not valid
    # Even an approving engine cannot make it canonical.
    decision = engine_decide(sneaky, True)
    assert decision.approved is False
    assert "Vetoed" in decision.engine_note


def test_pacing_rhythm_full_cycle():
    assert [s for s in PACING_ORDER] == [
        PacingStage.DISCOVERY, PacingStage.INVESTIGATION, PacingStage.ESCALATION,
        PacingStage.DECISION, PacingStage.CONSEQUENCE, PacingStage.RECOVERY,
        PacingStage.NEW_DISCOVERY,
    ]
    assert next_pacing(PacingStage.RECOVERY) == PacingStage.NEW_DISCOVERY
    assert next_pacing(PacingStage.NEW_DISCOVERY) == PacingStage.DISCOVERY
