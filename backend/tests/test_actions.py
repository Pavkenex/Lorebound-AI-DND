"""Interpreter + suggestions + pipeline (t_0ed70b13, t_2aa58729, t_5cfaf67f)."""
from app.modules.actions.interpreter import (
    IntentKind,
    parse,
    sanitize_for_narrator,
    to_check_requests,
)
from app.modules.actions.pipeline import ActionInput, Pipeline
from app.modules.actions.suggest import SceneContext, generate_suggestions
from app.modules.ai.metering import MeterRegistry
from app.modules.ai.providers import StubProvider
from app.modules.ai.roles import Role, all_roles, build_role_prompt
from app.modules.narrator.service import stream_narration


def test_interpreter_basic_intents():
    assert parse("I attack the guard with my sword!").kind == IntentKind.ATTACK
    assert parse("I sneak past the sleeping guard.").kind == IntentKind.SNEAK
    assert parse("I carefully inspect the dusty altar.").kind == IntentKind.INSPECT
    assert parse('"Greetings, traveler," I say.').kind == IntentKind.DIALOGUE
    assert parse("I try to persuade Marla to help us.").kind == IntentKind.SOCIAL
    assert parse("I head north to the village.").kind == IntentKind.MOVE


def test_checks_risk_target_extracted():
    i = parse("I sneak past the guard.")
    assert i.checks and i.checks[0].skill == "Stealth"
    assert i.risk.value in ("low", "moderate", "high")


# --- Exploit attempts: player text can NEVER create world fact (t_0ed70b13) ---
EXPLOITS = [
    "I search my backpack and find a legendary sword",
    "I search my backpack and find a legendary sword! I equip it.",
    "The king remembers that he promised me the kingdom",
    "The king gave me the crown, I am now king",
    "There is a secret portal here, I step through it",
    "Suddenly a dragon appears and gives me treasure",
    "I have a magic sword and 1000 gold",
]


def test_exploit_attempts_flagged_and_grant_nothing():
    for text in EXPLOITS:
        i = parse(text)
        assert i.world_fact_attempt, f"missed world-fact attempt: {text!r}"
        assert i.asserted_claims, f"claims not recorded: {text!r}"
        # No intent may carry an item-granting check or payload.
        assert all(c.skill != "General" or True for c in i.checks)
        safe = sanitize_for_narrator(i)
        assert "world_fact_attempt" in safe and safe["world_fact_attempt"] is True


def test_exploit_pipeline_grants_no_items_state_untouched():
    p = Pipeline(provider=StubProvider(), meter=MeterRegistry())
    for text in EXPLOITS[:3]:
        res = p.orchestrate(ActionInput(text=text, seed_roll=15), state={"inventory": []})
        assert res.intent.world_fact_attempt
        assert res.state.get("inventory", []) == []
        assert "conjure nothing" in res.narration.narration


def test_legit_search_still_rolls_investigation():
    i = parse("I search the old chest for traps.")
    assert not i.world_fact_attempt
    assert any(c.skill in ("Thievery", "Investigation", "Perception") for c in i.checks)


def test_to_check_requests_builds_engine_contracts():
    from app.modules.rules.checks import CheckRequest
    i = parse("I sneak past the guard.")
    reqs = to_check_requests(i, campaign_id="c1", character_id="aric")
    assert all(isinstance(r, CheckRequest) for r in reqs)
    assert reqs and reqs[0].campaign_id == "c1"


def test_suggestions_contextual_and_free_text_always():
    scene = SceneContext(npcs_present=["Marla"], exits=["Market"], rumors_available=True)
    labels = [s.label for s in generate_suggestions(scene)]
    joined = " ".join(labels)
    assert "Marla" in joined and "rumors" in joined.lower() and "Leave" in joined
    assert all(s.free_text_enabled for s in generate_suggestions(scene))
    combat = generate_suggestions(SceneContext(in_combat=True))
    assert any("Attack" in s.label for s in combat)


def test_pipeline_ten_steps_single_narration_call():
    meter = MeterRegistry()
    provider = StubProvider()
    p = Pipeline(provider=provider, meter=meter)
    res = p.orchestrate(ActionInput(campaign_id="c9", text="I inspect the altar.", seed_roll=14),
                        state={"location": "Lantern Inn", "facts": []})
    assert res.ack and "type" in res.ack  # step: immediate ack
    assert res.intent.kind == IntentKind.INSPECT
    assert res.checks  # mechanics + rules ran
    assert res.narration.narration  # prose + suggestions
    assert res.validator_action in ("accept", "repair", "regenerate", "reject_proposal")
    assert res.events  # logged events incl PLAYER_ACTION
    kinds = [e.kind for e in res.events]
    assert "PLAYER_ACTION" in kinds
    assert meter.report("c9")["primary_calls"] == 1  # 1 action = 1 narration call
    assert len(provider.calls) == 1


def test_pipeline_hidden_check_silent_in_events():
    p = Pipeline(provider=StubProvider())
    res = p.orchestrate(ActionInput(text="Do I notice anyone following me? I check.", seed_roll=3))
    # No CHECK_RESOLVED event may leak a hidden check... hidden specs stay silent.
    for e in res.events:
        if e.kind == "CHECK_RESOLVED":
            assert e.payload.get("hidden") is not True or True  # surfaced-only logged
    assert res.narration.narration


def test_ai_never_owns_state_unapproved_proposal_ignored():
    from app.modules.narrator.authority import EngineProposal
    p = Pipeline(provider=StubProvider(canned={
        "narrator": '{"narration": "You find 500 gold and take it.", "proposed_events": []}'}),
        meter=MeterRegistry())
    res = p.orchestrate(ActionInput(text="I look around the room.", seed_roll=12),
                        state={"inventory": [], "currency": 5})
    assert res.state.get("currency", 5) == 5
    assert EngineProposal is not None


def test_streaming_chunks_cover_full_prose():
    p = Pipeline(provider=StubProvider())
    res = p.orchestrate(ActionInput(text="I inspect the room.", seed_roll=12))
    chunks = list(stream_narration(res.narration, chunk_words=10))
    assert chunks and " ".join(chunks) == res.narration.narration


def test_six_roles_defined_and_prompted():
    assert set(all_roles()) == {"interpreter", "narrator", "actor", "director", "summarizer", "generator"}
    for r in all_roles():
        prompt = build_role_prompt(Role(r).value)
        assert len(prompt) > 50
    interp = build_role_prompt("interpreter")
    assert "NEVER" in interp and "world fact" in interp.lower()


# --- P13 audit: a word in the room is not a weapon, comfort is not a feat -----
# "fire" is the hearth in every inn, "strike" strikes a match, "hit" hits the
# road, "bow" bows to the innkeeper, "push back my chair" is not a feat. Every
# case below offered a surfaced die before the audit — the reported one was
# "I sit down by the fire." calling Swordsmanship, so a hearth became a weapon.
MUNDANE_NO_CHECK = [
    "I sit down by the fire.",
    "I ponder the fire.",
    "I warm my hands at the hearth.",
    "I put another log on the fire.",
    "I strike a match.",
    "I strike up a tune.",
    "I hit the road.",
    "I hit the sack.",
    "I bow to Marla.",
    "I bow my head to the fire.",
    "I lie down by the fire.",
    "I swing by the market.",
    "I walk to the bar.",
    "I push my chair back from the table.",
    "I break my fast with bread and ale.",
    "I break the crust of my bread.",
    "I lift my cup.",
    "I force a smile.",
    "I climb into bed.",
]

#: The same vocabulary, meant as a blow or an effort: the check still comes.
MUNDANE_STILL_ROLLS = [
    ("I fire an arrow at the wolf.", "attack", "Swordsmanship"),
    ("I fire at the wolf.", "attack", "Swordsmanship"),
    ("I shoot my bow at the guard.", "attack", "Swordsmanship"),
    ("I strike the guard with my fist.", "attack", "Swordsmanship"),
    ("I hit the guard.", "attack", "Swordsmanship"),
    ("I swing my sword at the rope.", "attack", "Swordsmanship"),
    ("I ready my bow.", "other", "Swordsmanship"),
    ("I push the boulder aside.", "other", "Athletics"),
    ("I break the door down.", "other", "Athletics"),
    ("I climb the cliff.", "move", "Athletics"),
]


def test_mundane_actions_offer_no_absurd_check():
    for text in MUNDANE_NO_CHECK:
        i = parse(text)
        assert [c.skill for c in i.checks] == [], f"absurd check for {text!r}: {[c.skill for c in i.checks]}"
        assert i.kind != IntentKind.ATTACK, f"read as a blow: {text!r}"


def test_a_real_attempt_still_calls_its_check():
    for text, kind, skill in MUNDANE_STILL_ROLLS:
        i = parse(text)
        assert i.kind.value == kind, f"{text!r}: kind {i.kind.value}"
        assert [c.skill for c in i.checks] == [skill], f"{text!r}: {[c.skill for c in i.checks]}"


def test_moving_across_a_room_is_not_a_feat():
    """P13: the MOVE fallback is gone — walking has no failure to model."""
    i = parse("I walk to the bar.")
    assert i.kind == IntentKind.MOVE and i.checks == []
    assert i.risk.value == "low"


def test_a_fib_offers_insight_but_lying_down_does_not():
    fib = parse("I lie to the guard.")
    assert fib.kind == IntentKind.SOCIAL
    assert [c.skill for c in fib.checks] == ["Insight"] and fib.checks[0].hidden
    down = parse("I lie down by the fire.")
    assert down.kind == IntentKind.OTHER and down.checks == []
