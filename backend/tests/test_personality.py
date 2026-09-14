"""Personality + social checks (systems slice 4): traits, DC math, leverage.

Covers docs/SYSTEMS_DESIGN.md §5 (authored trait profiles, soft spots, vows,
mood biases) and §6 (social DCs = base + approach + mood − relationship credit,
leverage shortcuts, pressure's own price, the Long odds window): trait scaling
of meter deltas, the mood rewritten at set time, the DC ``dc_why`` and
``long_odds`` fields on the pending check, coin that answers a need versus coin
that buys nothing, pressure consequences on the meter and in memory, and the
disposition line the narrator sees.
"""
from __future__ import annotations

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from app.core.database import Base, get_db
from app.main import app
from app.modules.actions.interpreter import parse
from app.modules.actions.pipeline import ActionInput, Pipeline
from app.modules.actions.suggest import SceneContext
from app.modules.ai.providers import ProviderResult
from app.modules.auth.models import User  # noqa: F401  (register metadata)
from app.modules.campaign import models as cm  # noqa: F401
from app.modules.campaign import npc as _npc  # noqa: F401
from app.modules.campaign import story as _story  # noqa: F401
from app.modules.campaign import world as _world  # noqa: F401
from app.modules.character import models as _char  # noqa: F401
from app.modules.inventory import models as _inv  # noqa: F401
from app.modules.memory.npc_memory import named_npc
from app.modules.npc.mood import MOOD_VOCAB, baseline_mood
from app.modules.npc.personality import (
    APPROACHES,
    PROFILES,
    SOFT_SPOT_VOCAB,
    TRAIT_AXES,
    SocialContext,
    biased_mood,
    delta_factor,
    profile_for,
    relationship_credit,
    scale_relationship_delta,
    social_adjustment,
)
from app.modules.play import models as pm  # noqa: F401
from app.modules.play.engine import ActEngine, route
from app.modules.play.models import PlayStateRow
from app.modules.play.session import PlaySession
from app.modules.play.state import PlayState
from app.modules.rules.checks import (
    CheckRequest,
    CheckSuspension,
    apply_social_policy,
    is_long_odds,
)

pytestmark = pytest.mark.usefixtures("stub_play_provider")

engine = create_engine("sqlite://", connect_args={"check_same_thread": False}, poolclass=StaticPool)
TestingSession = sessionmaker(bind=engine, autoflush=False, autocommit=False)
Base.metadata.create_all(bind=engine)


def _override():
    db = TestingSession()
    try:
        yield db
    finally:
        db.close()


@pytest.fixture()
def client():
    previous = app.dependency_overrides.get(get_db)
    app.dependency_overrides[get_db] = _override
    try:
        yield TestClient(app, raise_server_exceptions=False)
    finally:
        if previous is None:
            app.dependency_overrides.pop(get_db, None)
        else:
            app.dependency_overrides[get_db] = previous


def _setup(client: TestClient, email: str) -> tuple[dict, str]:
    r = client.post(
        "/auth/register",
        json={"email": email, "password": "password123", "display_name": email.split("@")[0]},
    )
    assert r.status_code == 201, r.text
    headers = {"Authorization": f"Bearer {r.json()['token']['access_token']}"}
    c = client.post("/campaigns", headers=headers, json={})
    assert c.status_code == 201, c.text
    # The chronicle opens on the prologue's road (§intro); these tests play
    # inside the tavern, so the player walks in first.
    _act(client, headers, "I head down to the inn and step inside")
    return headers, c.json()["id"]


def _call(client: TestClient, h: dict, text: str, **extra) -> dict:
    """One /act leg; the pending throw (if any) happens inside _act."""
    r = client.post("/act", headers=h, json={"text": text, **extra})
    assert r.status_code == 200, r.text
    return r.json()


def _act(client: TestClient, h: dict, text: str, *, seed: int | None = None,
         throw: int = 18) -> dict:
    """Resolve one action to completion: seeded, or the player's thrown 18."""
    data = _call(client, h, text, **({"seed_roll": seed} if seed is not None else {}))
    if data.get("pending_check"):
        data = _call(client, h, text, roll=throw, pending_token=data["pending_check"]["token"])
    return data


def _state(campaign_id: str) -> PlayState:
    db = TestingSession()
    try:
        return PlayState.from_json(db.get(PlayStateRow, campaign_id).state_json)
    finally:
        db.close()


def _system_lines(payload: dict, prefix: str) -> list[str]:
    return [line for line in payload.get("system", []) if line.startswith(prefix)]


def _to_market(client: TestClient, h: dict) -> None:
    """Hear the lead from Marla, then walk to the guild's stalls."""
    _act(client, h, "I ask Marla about the travelers")
    _act(client, h, "I walk to the market")


def _rich(client: TestClient, h: dict) -> None:
    """Lift the strongbox first (seed 15): 8 -> 22 guilders."""
    _act(client, h, "I steal from the storeroom strongbox", seed=15)


# ------------------------------------------------------------- unit: profiles


def test_every_roster_character_carries_an_authored_profile():
    assert sorted(PROFILES) == ["anselm", "borin", "marla", "sella", "tomm"]
    for slug, profile in PROFILES.items():
        assert profile.slug == slug and profile.name
        assert profile.disposition, f"{slug} has no disposition for the narrator"
        assert profile.soft_spots, f"{slug} answers to nothing"
        for axis, value in profile.traits.items():
            assert axis in TRAIT_AXES, f"{slug}: unknown axis {axis}"
            assert -1.0 <= value <= 1.0
        for spot in profile.soft_spots:
            assert spot in SOFT_SPOT_VOCAB, f"{slug}: unknown soft spot {spot}"
        for approach, mod in profile.approach_mods.items():
            assert approach in APPROACHES, f"{slug}: unknown approach {approach}"
            assert -5 <= mod <= 5
        for vow in profile.vows:
            assert vow.words
            for approach in vow.favors + vow.forbids:
                assert approach in APPROACHES
        for bias in profile.mood_biases:
            assert bias.from_mood in MOOD_VOCAB and bias.to_mood in MOOD_VOCAB
            assert bias.axis in TRAIT_AXES
        for word in profile.mood_mods:
            assert word in MOOD_VOCAB, f"{slug}: unknown mood {word}"
        if profile.price is not None:
            assert profile.price.cost > 0 and profile.price.words
        # The slice-3 baseline hook is filled: every character rests somewhere.
        assert baseline_mood(slug) in MOOD_VOCAB
    # Vows are per-character, never a universal gate.
    assert profile_for("borin").vows == ()
    assert profile_for("marla").vows and profile_for("anselm").vows


def test_unknown_characters_stay_neutral():
    guest = profile_for("hooded-carter")
    assert guest.soft_spots == () and guest.vows == () and guest.price is None
    assert scale_relationship_delta("hooded-carter", "gift", -5) == -5
    assert biased_mood("hooded-carter", "warm") == "warm"
    assert social_adjustment(SocialContext(slug="hooded-carter"), 13).shift == 0


# ------------------------------------------- unit: the slice-1..3 weights hold


def test_shipped_interaction_weights_keep_their_tuned_values():
    """New trait scaling must not quietly re-tune the beats already shipped."""
    assert scale_relationship_delta("marla", "theft", -25) == -25
    assert scale_relationship_delta("marla", "conversation", 5) == 5
    assert scale_relationship_delta("marla", "conversation", 10) == 10
    assert scale_relationship_delta("marla", "curiosity", 5) == 5
    assert scale_relationship_delta("marla", "suspicion", -10) == -10
    assert scale_relationship_delta("marla", "resolution", 30) == 30
    assert scale_relationship_delta("marla", "flirt", 3) == 3
    assert scale_relationship_delta("borin", "intimidation", -20) == -20
    assert scale_relationship_delta("borin", "talk", 5) == 5  # unknown kind: untouched


# --------------------------------------------------- unit: traits scale (§5)


def test_traits_scale_a_meter_move_by_the_kind():
    # Borin is greedy (over-weights a gift) and lonely (over-weights attention).
    assert scale_relationship_delta("borin", "gift", 5) == 10
    assert scale_relationship_delta("borin", "conversation", 5) == 8  # 1.5x
    # Marla's pride takes an insult double-and-a-bit; she answers to duty, not coin.
    assert scale_relationship_delta("marla", "insult", -5) == -8
    assert scale_relationship_delta("marla", "gift", 5) == 5
    # Anselm's contempt for coin halves a gift.
    assert scale_relationship_delta("anselm", "gift", 10) == 5
    assert delta_factor("borin", "leverage") == 2.0  # 1 + greed 0.8 + coin, capped
    # Direction never flips and a zero move stays zero.
    assert scale_relationship_delta("sella", "insult", -3) < 0
    assert scale_relationship_delta("borin", "gift", 0) == 0


def test_mood_bias_rewrites_the_event_mood():
    assert biased_mood("sella", "afraid") == "resentful"   # pride 0.7
    assert biased_mood("marla", "afraid") == "resentful"   # pride 0.6
    assert biased_mood("anselm", "afraid") == "anxious"    # faith 0.9
    assert biased_mood("tomm", "warm") == "suspicious"     # caution 0.8
    assert biased_mood("borin", "warm") == "curious"       # greed 0.8
    assert biased_mood("borin", "anxious") == "amused"     # caution -0.4
    # No bias in path: the event mood stands, case handled, unknown stays unknown.
    assert biased_mood("marla", "warm") == "warm"
    assert biased_mood("sella", "angry") == "angry"
    assert biased_mood("borin", "Warm") == "curious"
    assert biased_mood("marla", "") == "neutral"


# ---------------------------------------------------- unit: the social DC (§6)


def test_relationship_credit_is_ten_points_to_a_dc_and_capped():
    assert relationship_credit(0) == 0
    assert relationship_credit(25) == 3
    assert relationship_credit(-40) == -4
    assert relationship_credit(100) == 10 and relationship_credit(-100) == -10
    assert relationship_credit(400) == 10 and relationship_credit(-400) == -10


def test_social_adjustment_reports_every_term_it_moved():
    ctx = SocialContext(slug="sella", approach="pressure", mood="suspicious",
                        mood_intensity=1.0, attitude=-20)
    adj = social_adjustment(ctx, 16)
    assert (adj.base_dc, adj.approach_mod, adj.mood_mod, adj.credit) == (16, 1, 4, -2)
    assert adj.shift == 1 + 4 - (-2)
    assert adj.adjusted_dc == 23
    assert adj.why == ("a hard word (+1) · Sella Voss is suspicious (+4) · "
                       "Sella Voss's coldness stands against you (+2)")

    # Mood weight scales with how strong the mood is; a settled character is 0.
    faint = social_adjustment(SocialContext(slug="sella", approach="charm",
                                            mood="suspicious", mood_intensity=0.25), 13)
    assert faint.mood_mod == 1 and faint.adjusted_dc == 15  # 13 + charm 1 + mood 1
    settled = social_adjustment(SocialContext(slug="sella", approach="charm",
                                              mood="neutral", mood_intensity=0.0), 13)
    assert settled.mood_mod == 0 and settled.shift == 1

    # Warmth is worth a DC; an answer to a soft spot is where vows bite.
    warm = social_adjustment(SocialContext(slug="marla", approach="appeal",
                                           attitude=30), 13)
    assert "warmth counts for you (−3)" in warm.why
    vow = social_adjustment(SocialContext(slug="anselm", approach="pressure"), 13)
    assert vow.vow_mod == 5 and "a vow holds" in vow.why
    favour = social_adjustment(SocialContext(slug="anselm", approach="appeal"), 13)
    assert favour.vow_mod == -2
    # A vow rarely fires: it is conditional, and only on the approach it names.
    assert social_adjustment(SocialContext(slug="borin", approach="appeal"), 13).vow_mod == 0
    assert social_adjustment(SocialContext(slug="sella", approach="coin"), 16).vow_mod == -2
    # The DC never falls below 1, however warm it gets.
    assert social_adjustment(SocialContext(slug="marla", approach="appeal",
                                           attitude=100), 1).adjusted_dc == 1


def test_long_odds_is_the_natural_twenty_window():
    # Long odds == only a natural 20 passes: dc − total > the best normal face.
    assert is_long_odds(20, 1) is False   # 19 + 1 = 20 reaches it normally
    assert is_long_odds(20, 0) is True    # 20 − 0: nothing but the critical lands
    assert is_long_odds(22, 3) is False   # 19 + 3 = 22: the die's top face lands
    assert is_long_odds(23, 3) is True    # beyond a normal reach: critical only
    assert is_long_odds(28, 3) is True


def test_apply_social_policy_stacks_role_play_and_personality():
    req = CheckRequest(skill="Persuasion", dc=13, social=True, rp_quality=1.0)
    plain, needed = apply_social_policy(req)
    assert plain.dc == 11 and needed is False  # no stakes: no roll either way
    ctx = SocialContext(slug="tomm", approach="charm", mood="suspicious",
                        mood_intensity=1.0, attitude=-10)
    adj = social_adjustment(ctx, req.dc)
    both, _ = apply_social_policy(req, social=ctx)
    assert (adj.approach_mod, adj.mood_mod, adj.credit) == (-1, 3, -1)
    assert both.dc == plain.dc + adj.shift   # role-play −2 then the personality terms
    contested = req.model_copy(update={"npc_resistant": True})
    _, roll_needed = apply_social_policy(contested, social=ctx)
    assert roll_needed is True


# ------------------------------------------- unit: aliases + pipeline context


def test_name_aliases_cover_the_roster():
    assert named_npc("I pay the factor for the truth") == "sella"
    assert named_npc("I offer the peddler four guilders") == "tomm"
    assert named_npc("I lean on the mercenary") == "borin"
    assert named_npc("I greet Brother Anselm") == "anselm"
    assert named_npc("I ask the landlady for a room") == "marla"
    assert named_npc("I persuade the empty room to be quiet") is None
    # Only characters actually in the room can be named.
    assert named_npc("I pay the peddler", ["Marla Voss", "Borin"]) is None
    assert named_npc("I pay the peddler", ["Sella Voss", "Tomm Ash"]) == "tomm"


def test_pipeline_social_context_reads_the_live_scene():
    pipe = Pipeline()
    req = CheckRequest(skill="Persuasion", social=True)
    action = ActionInput(
        text="I try to convince the innkeeper to lend me her ledger",
        scene=SceneContext(
            npcs_present=["Marla Voss"],
            npc_moods={"Marla Voss": {"mood": "suspicious", "intensity": 0.8}},
            npc_attitudes={"Marla Voss": -40},
        ),
    )
    ctx = pipe._social_context(req, action, action.text)
    assert ctx is not None
    assert (ctx.slug, ctx.approach) == ("marla", "charm")
    assert (ctx.mood, ctx.mood_intensity, ctx.attitude) == ("suspicious", 0.8, -40)

    empty = ActionInput(text="I persuade the empty room to be quiet", scene=SceneContext())
    assert pipe._social_context(req, empty, empty.text) is None


def test_social_check_against_a_present_character_suspends_with_the_why():
    """The pipeline rolls a contested attempt, at the personality DC (§6)."""
    pipe = Pipeline()
    action = ActionInput(
        text="I try to persuade Marla to lend me the ledger",
        scene=SceneContext(npcs_present=["Marla Voss"], npc_attitudes={"Marla Voss": -30}),
        seed_roll=None,
    )
    with pytest.raises(CheckSuspension) as raised:  # no provider needed: it stops first
        pipe.orchestrate(action, suspend_on_check=True)
    spec = raised.value.spec
    assert spec["skill"] == "Persuasion"
    assert spec["dc_base"] == 13 and spec["dc"] == 15  # 13 + charm −1 + credit +3
    assert spec["dc_why"] and "Marla Voss's coldness" in spec["dc_why"]
    assert spec["long_odds"] is False


# ------------------------------------------------------------- routes + beats


def test_social_skill_hints_match_real_words():
    """'persuade' must reach the Persuasion hint (a bare `persua\\b` never did)."""
    for text, skill in (
        ("I try to persuade Marla to help", "Persuasion"),
        ("I persuade Sella to talk", "Persuasion"),
        ("I intimidate the carter", "Intimidation"),
        ("I threaten him quietly", "Intimidation"),
    ):
        assert [c.skill for c in parse(text).checks] == [skill], text


def test_offers_route_to_the_leverage_beat():
    assert route("I pay Sella ten guilders for the truth") == "offer"
    assert route("I offer Borin six guilders for the truth") == "offer"
    assert route("I offer the peddler four guilders") == "offer"
    # Not every mention of coin is an offer to a person.
    assert route("I steal from the storeroom strongbox") == "steal"


def test_coin_answers_a_need_and_skips_the_roll(client: TestClient):
    h, cid = _setup(client, "pers-pay-sella@example.com")
    _rich(client, h)
    assert _state(cid).silver == 22
    _to_market(client, h)

    paid = _act(client, h, "I pay Sella ten guilders for the truth")
    assert "pending_check" not in paid        # the price decides it: no die (§6)
    assert paid["mechanics"] is None
    st = _state(cid)
    assert st.silver == 12                    # 22 − 10
    assert st.lead_stage == "investigating"
    assert st.solution_path == "talk-it-out"  # money is a legitimate shortcut
    assert st.attitude_for("sella") == 10     # greed + the coin soft spot: 2x
    assert st.mood_of("sella")["mood"] == "warm"
    assert "took 10 guilders and told what coin buys" in [
        m["text"] for m in st.npc_memory_log
    ]
    assert _system_lines(paid, "❧") == [
        "❧ Coin answers — Sella's price met (−10 guilders).",
        "❧ The way in — The Honest Plea",   # money is a legitimate shortcut
    ]


def test_a_short_purse_still_rolls(client: TestClient):
    h, cid = _setup(client, "pers-broke@example.com")
    _to_market(client, h)                      # 8 guilders against a price of 10
    pending = _call(client, h, "I pay Sella ten guilders for the truth")
    spec = pending["pending_check"]
    # Difficult 16, −2 for coin on the bar, −2 because her vow backs a bargain.
    assert spec["dc_base"] == 16 and spec["dc"] == 12
    assert "coin on the bar (−2)" in spec["dc_why"]
    assert "a bargain struck is kept" in spec["dc_why"]
    assert spec["long_odds"] is False

    won = _act(client, h, "I pay Sella ten guilders for the truth")
    st = _state(cid)
    assert st.silver == 8                      # nothing was actually paid
    assert st.attitude_for("sella") == 0       # and nothing was banked for it
    assert st.solution_path == "talk-it-out"   # the words won it instead
    assert won["mechanics"]["outcome"] in ("Success", "Exceptional", "SuccessWithCost")
    assert won["mechanics"]["dc"] == 12


def test_coin_is_not_what_marla_wants(client: TestClient):
    h, cid = _setup(client, "pers-marla@example.com")
    pending = _call(client, h, "I offer Marla ten guilders for the truth")
    spec = pending["pending_check"]
    assert spec["dc"] == 15 and spec["dc_base"] == 13   # Moderate + coin lands wrong
    assert "coin on the bar (+2)" in spec["dc_why"]
    won = _act(client, h, "I offer Marla ten guilders for the truth")
    assert won["narration"]
    st = _state(cid)
    assert st.silver == 8                      # coin moved nothing at all
    assert st.attitude_for("marla") == 0
    # An offer that is not an ask never calls a check.
    quiet = _act(client, h, "I offer Marla twenty guilders for a room")
    assert "pending_check" not in quiet and _state(cid).silver == 8


def test_offer_before_the_story_buys_nothing(client: TestClient):
    h, cid = _setup(client, "pers-rail@example.com")
    _act(client, h, "I walk to the market")     # lead still unheard
    out = _act(client, h, "I pay Sella ten guilders for the truth")
    assert "pending_check" not in out
    st = _state(cid)
    assert st.silver == 8 and st.lead_stage == "unheard"   # the rail holds
    assert st.solution_path is None


def test_borin_takes_coin_and_wears_it_as_curiosity(client: TestClient):
    h, cid = _setup(client, "pers-borin@example.com")
    paid = _act(client, h, "I offer Borin six guilders for the truth")
    assert "pending_check" not in paid
    st = _state(cid)
    assert st.silver == 2                       # 8 − 6
    assert st.attitude_for("borin") == 10       # greedy and lonely: double
    assert st.mood_of("borin")["mood"] == "curious"  # warmth reads as a play
    assert _system_lines(paid, "❧") == [
        "❧ Coin answers — Borin's price met (−6 guilders).",
        "❧ Word bought — the carts turn north of the oak, nights, since Midwinter.",
    ]


def test_a_peddler_reads_bought_coin_as_suspicion(client: TestClient):
    h, cid = _setup(client, "pers-peddler@example.com")
    _to_market(client, h)
    paid = _act(client, h, "I offer the peddler four guilders for the truth")
    assert "pending_check" not in paid
    st = _state(cid)
    assert st.silver == 4 and st.attitude_for("tomm") == 9
    assert st.mood_of("tomm")["mood"] == "suspicious"   # caution 0.8 rewrites warmth


def test_paying_a_character_who_is_not_here_finds_no_taker(client: TestClient):
    h, cid = _setup(client, "pers-nobody@example.com")
    out = _act(client, h, "I offer the peddler four guilders for the truth")
    assert "pending_check" not in out
    assert _state(cid).silver == 8
    assert "no one here to take it" in out["narration"]


# ------------------------------------------------------- pressure's own price


def test_pressure_leaves_a_grudge_not_a_lesson(client: TestClient):
    h, cid = _setup(client, "pers-sella-int@example.com")
    _to_market(client, h)
    out = _act(client, h, "I intimidate Sella until she talks", seed=18)
    assert out["mechanics"]["outcome"] in ("Success", "Exceptional", "SuccessWithCost")
    st = _state(cid)
    assert st.solution_path == "lean-on-them"
    assert st.attitude_for("sella") == -15          # what leaning on her costs
    assert st.mood_of("sella")["mood"] == "resentful"   # fear curdles under pride
    assert "was made afraid, and did as she was told" in [
        m["text"] for m in st.npc_memory_log
    ]
    assert _system_lines(out, "❖") == ["❖ Sella −15 — Neutral (-15)"]


def test_a_failed_pressure_attempt_backfires(client: TestClient):
    h, cid = _setup(client, "pers-sella-fail@example.com")
    _to_market(client, h)
    out = _act(client, h, "I intimidate Sella until she talks", seed=8)
    assert out["mechanics"]["outcome"] == "Failure"
    st = _state(cid)
    assert st.solution_path is None
    assert st.attitude_for("sella") == -5
    assert st.mood_of("sella")["mood"] == "amused"
    assert "was threatened and did not bend" in [m["text"] for m in st.npc_memory_log]


def test_intimidation_meter_moves_keep_their_shipped_size(client: TestClient):
    h, _ = _setup(client, "pers-borin-int@example.com")
    out = _act(client, h, "I intimidate Borin", seed=18)
    assert _system_lines(out, "❖") == ["❖ Borin −20 — Wary (-20)"]


# ------------------------------------------------------------ the Long odds tag


def test_a_hopeless_social_check_is_tagged_long_odds():
    """Only a critical passes: the engine says so on the pending check (§6)."""
    st = PlayState()
    st.location = "market"
    st.lead_stage = "heard"
    st.silver = 8
    st.attitudes["sella"] = -100
    st.set_mood("sella", "suspicious", 1.0)
    engine_obj = ActEngine(PlaySession("pers-odds", st))
    payload, checkpoint = engine_obj.act(
        "I pay Sella ten guilders for the truth", suspend_on_check=True
    )
    assert checkpoint is None and payload["mechanics"] is None
    spec = payload["pending_check"]
    assert spec["dc_base"] == 16 and spec["dc"] == 26    # −2 coin, −2 vow, +4 mood, +10 cold
    assert spec["long_odds"] is True
    assert is_long_odds(spec["dc"], spec["total_mod"]) is True
    assert "Sella Voss's coldness stands against you (+10)" in spec["dc_why"]


# ------------------------------------------------- a warmer table, easier odds


def test_a_warm_relationship_lowers_the_dc_of_the_same_check():
    st = PlayState()
    st.location = "market"
    st.lead_stage = "heard"
    engine_obj = ActEngine(PlaySession("pers-warm", st))
    cold = engine_obj.act(
        "I persuade Sella the factor to confide in me", suspend_on_check=True
    )[0]
    assert cold["pending_check"]["dc"] == 17          # Difficult + easy charm
    assert cold["pending_check"]["dc_why"] == "easy charm (+1)"

    st.adjust_attitude("sella", 40, "fixture: a warm word")
    warm = engine_obj.act(
        "I persuade Sella the factor to confide in me", suspend_on_check=True
    )[0]
    assert warm["pending_check"]["dc"] == 13          # 17 − 4 for (warm) standing
    assert "warmth counts for you (−4)" in warm["pending_check"]["dc_why"]


# ------------------------------------------------------- narrator sees it (§5)


class _CapturingProvider:
    """Records the prompt the pipeline hands the narrator."""

    model_name = "capture"

    def __init__(self) -> None:
        self.prompts: list[str] = []

    def generate(self, prompt: str, *, role: str = "narrator", max_tokens: int = 600, **kwargs):
        self.prompts.append(prompt)
        return ProviderResult(
            text='{"narration": "The fire pops.", "npc_dialogue": [], "suggested_actions": []}'
        )


def test_prompt_carries_the_disposition_line():
    st = PlayState()
    prov = _CapturingProvider()
    ActEngine(PlaySession("pers-prompt", st), provider=prov).act(
        "I settle by the hearth and hum a lamplighter's tune"
    )
    assert prov.prompts
    assert "disposition: proud, watchful, kind under the armor" in prov.prompts[0]
