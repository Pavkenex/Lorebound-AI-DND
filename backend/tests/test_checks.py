"""Check engine + outcome model + social policy (t_4d8fddef, t_7a355b22, t_2e94122b)."""
import random

import pytest

from app.modules.rules.checks import (
    DC_BANDS,
    Outcome,
    apply_social_policy,
    complication_for,
    dc_for_band,
    roll_check,
    rp_dc_shift,
    should_roll_social,
)
from app.modules.rules.checks import CheckRequest as CR


def req(**kw):
    base = {"skill": "Stealth", "dc": 13, "difficulty": "Moderate"}
    base.update(kw)
    return CR(**base)


def test_dc_bands_match_spec():
    assert DC_BANDS == {"Easy": 8, "Routine": 10, "Moderate": 13, "Difficult": 16,
                        "Hard": 19, "Extreme": 23, "Legendary": 27}
    assert dc_for_band("Trivial") is None
    with pytest.raises(ValueError):
        dc_for_band("Impossible")


def test_resolution_math():
    r = roll_check(req(attribute_mod=2, skill_mod=3, situational_mod=1, dc=13), roll=10)
    assert (r.roll, r.total, r.margin) == (10, 16, 3)
    assert r.outcome == Outcome.Success


def test_trivial_auto_no_dice_not_surfaced():
    r = roll_check(req(difficulty="Trivial"), roll=1)
    assert r.trivial_auto and r.roll == 0 and r.outcome == Outcome.Success
    assert r.surfaced is False


def test_hidden_checks_silent():
    r = roll_check(req(hidden=True), roll=20)
    assert r.hidden and r.surfaced is False
    assert r.outcome == Outcome.Exceptional  # still resolved


def test_meaningful_rolls_surfaced():
    assert roll_check(req(), roll=12).surfaced is True


def test_five_outcomes_reachable():
    assert roll_check(req(dc=10), roll=1).outcome == Outcome.CriticalFailure
    assert roll_check(req(dc=10), roll=5).outcome == Outcome.Failure  # 5<10
    cost = roll_check(req(dc=10), roll=10)  # margin 0 -> cost band
    assert cost.outcome == Outcome.SuccessWithCost
    assert cost.cost and "complication" in cost.cost.lower() or cost.cost
    assert "real" in cost.cost.lower() or "price" in cost.cost.lower() or "setback" in cost.cost.lower()
    assert roll_check(req(dc=10), roll=15).outcome == Outcome.Success
    assert roll_check(req(dc=10), roll=20).outcome == Outcome.Exceptional
    assert roll_check(req(dc=30), roll=19).outcome == Outcome.CriticalFailure  # margin -11


def test_cost_only_for_cost_outcome():
    assert complication_for(Outcome.Success, "Stealth") is None
    assert complication_for(Outcome.SuccessWithCost, "Stealth") is not None


def test_deterministic_rng():
    a = roll_check(req(), rng=random.Random(7))
    b = roll_check(req(), rng=random.Random(7))
    assert a.roll == b.roll


def test_bad_roll_rejected():
    with pytest.raises(ValueError):
        roll_check(req(), roll=21)


# --- Social policy (t_2e94122b) ---
def soc(**kw):
    base = {"skill": "Persuasion", "social": True}
    base.update(kw)
    return CR(**base)


def test_social_no_roll_when_friendly_certain():
    assert should_roll_social(soc(npc_resistant=False, outcome_matters=True,
                                  uncertain=False, manipulation_attempt=False)) is False


def test_social_roll_triggers():
    assert should_roll_social(soc(npc_resistant=True))
    assert should_roll_social(soc(manipulation_attempt=True))
    assert should_roll_social(soc(outcome_matters=True, uncertain=True))


def test_rp_shifts_dc_never_replaces_skill():
    assert rp_dc_shift(1.0) == -2 and rp_dc_shift(-1.0) == 2 and rp_dc_shift(0.0) == 0
    adj, _ = apply_social_policy(soc(dc=13, rp_quality=1.0))
    assert adj.dc == 11  # shifted, but check still resolves via roll
    res = roll_check(adj, roll=10)
    assert res.total == 10  # modifiers untouched; RP only moved the DC
