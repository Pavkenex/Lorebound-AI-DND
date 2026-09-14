"""Scene director (systems slice 5): micro-scenes, invitations, the anti-loop.

Covers docs/SYSTEMS_DESIGN.md §7: the scene model carried in ``PlayState`` (the
current scene, the remembered scenes, the sub-scene stack), per-beat bookkeeping
with progress detection, diminishing replies for repeated/idle actions that
point at what is still possible, the no-pull-back invariant (a resolved scene
never re-runs its opening and narration never drags the player back), the
invitation as a transition trigger the guard never blocks, the
scene-transition autosave, and scene state surviving save/load.

P14 governs the prose assertions here: every beat's words are the model's,
written from the facts the engine hands over. So "the opening played once" is
asserted as *the narrator was handed that beat exactly once*, and "the reply
diminished" as *the narrator was handed the diminished brief* (§7).
"""
from __future__ import annotations

import itertools

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from app.core.database import Base, get_db
from app.main import app
from app.modules.actions.pipeline import scene_line
from app.modules.ai.providers import ProviderResult
from app.modules.campaign.service import AUTOSAVE_CHECKPOINTS
from app.modules.play import models as pm  # noqa: F401  (register metadata)
from app.modules.play.engine import ActEngine
from app.modules.play.models import PlayStateRow
from app.modules.play.session import CHECKPOINT_BY_KIND, PlaySession
from app.modules.play.state import PlayState, seeded_state
from app.modules.story.scenes import (
    ACTIVE,
    PROLOGUE,
    PROLOGUE_LABEL,
    RESOLVED,
    SCENE_STATES,
    TRANSITIONING,
    UPSTAIRS,
    UPSTAIRS_GOAL,
    UPSTAIRS_LABEL,
    SceneDirector,
    possible_moves,
)
from tests.narrator_fake import MARK, RecordingNarrator

engine = create_engine("sqlite://", connect_args={"check_same_thread": False}, poolclass=StaticPool)
TestingSession = sessionmaker(bind=engine, autoflush=False, autocommit=False)
Base.metadata.create_all(bind=engine)

INN = "lantern-inn"


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
    return headers, c.json()["id"]


def _act(client: TestClient, h: dict, text: str, *, seed: int | None = None, throw: int = 18) -> dict:
    """Resolve one action to completion (a pending throw uses the player's 18)."""
    body = {"text": text, **({"seed_roll": seed} if seed is not None else {})}
    r = client.post("/act", headers=h, json=body)
    assert r.status_code == 200, r.text
    data = r.json()
    if data.get("pending_check"):
        r = client.post(
            "/act",
            headers=h,
            json={"text": text, "roll": throw, "pending_token": data["pending_check"]["token"]},
        )
        assert r.status_code == 200, r.text
        data = r.json()
    return data


def _state(campaign_id: str) -> PlayState:
    db = TestingSession()
    try:
        return PlayState.from_json(db.get(PlayStateRow, campaign_id).state_json)
    finally:
        db.close()


def _engine(campaign_id: str, narrator: RecordingNarrator,
            st: PlayState | None = None) -> ActEngine:
    """An engine over a fresh campaign's state — the engine itself needs no DB."""
    return ActEngine(
        PlaySession(campaign_id, st if st is not None else seeded_state()),
        provider=narrator,
    )


def _beats(narrator: RecordingNarrator) -> list[str]:
    """Every beat the narrator was handed, in order — the prose contract."""
    return [RecordingNarrator.beat_of(p) for p in narrator.narrator_prompts]


def _last_beat(narrator: RecordingNarrator) -> str:
    return _beats(narrator)[-1]


def _last_facts(narrator: RecordingNarrator) -> str:
    return " | ".join(RecordingNarrator.facts_of(narrator.narrator_prompts[-1]))


def _walk_in(engine_obj: ActEngine) -> dict:
    """The prologue's one door (§intro): down the last stretch and inside."""
    out, _checkpoint = engine_obj.act("I head down to the inn and step inside")
    return out


def _engine_in_inn(campaign_id: str, narrator: RecordingNarrator) -> ActEngine:
    """The tavern start these bar tests used to get from a fresh seed (§intro)."""
    engine_obj = _engine(campaign_id, narrator)
    _walk_in(engine_obj)
    return engine_obj


def _scene(state: PlayState):
    return SceneDirector(state).current()


def _walk_upstairs(engine_obj: ActEngine) -> tuple[dict, str | None]:
    """Tell Marla the story (the stair is lent, §7d) and take it."""
    engine_obj.act("I ask Marla about the travelers")
    return engine_obj.act("I follow Marla upstairs")


# ------------------------------------------------------------- the scene model


def test_a_fresh_campaign_opens_on_the_road_above_ravenford():
    """A new chronicle begins at the prologue (§intro), not mid-tavern."""
    st = seeded_state()
    assert (st.location, st.scene) == (PROLOGUE, PROLOGUE)
    scene = SceneDirector(st).current()
    assert (scene.id, scene.label, scene.state) == (PROLOGUE, PROLOGUE_LABEL, ACTIVE)
    assert scene.goal == "Come in out of the rain"
    assert scene.beats == 0 and scene.last_progress == 0
    # The seed writes no prose at all (P14): the chronicle is empty until the
    # narrator is asked, and the inn's opening waits at its own door (§intro).
    assert st.feed == []
    assert st.opening_pending is True
    assert scene.state in SCENE_STATES


def test_the_walk_in_is_the_inns_one_first_arrival():
    """Entering town is a transition; its opening plays once (§intro, §7)."""
    narrator = RecordingNarrator()
    engine_obj = _engine("scene-inn-arrival", narrator)
    out = _walk_in(engine_obj)
    st = engine_obj.state

    assert st.location == INN and st.scene == INN
    assert _last_beat(narrator) == "inn.first"
    facts = _last_facts(narrator)
    assert "notice board" in facts                       # the plea is on the wall
    assert "never came back down the Northern Road" in facts
    assert MARK in out["narration"]                      # the model wrote it
    assert out["dialogue"][0]["speaker"] == "Marla Voss"
    assert any(line.startswith("▸ Scene — The Lantern Inn") for line in out["system"])
    # The arrival is narrated once: the door never asks for it again.
    assert _beats(narrator).count("inn.first") == 1


def test_scene_state_rides_the_save_json():
    st = seeded_state()
    st.lead_stage = "rumored"
    SceneDirector(st).enter(
        UPSTAIRS, label=UPSTAIRS_LABEL, location=INN, parent=INN, goal=UPSTAIRS_GOAL
    )
    again = PlayState.from_json(st.to_json())
    assert again.scene == UPSTAIRS
    assert again.scenes[UPSTAIRS]["label"] == UPSTAIRS_LABEL
    assert again.scenes[UPSTAIRS]["parent"] == INN
    assert again.scenes[UPSTAIRS]["location"] == INN


def test_beat_bookkeeping_counts_and_marks_progress():
    st = seeded_state()
    d = SceneDirector(st)
    d.note_beat(key="inspect:hearth", progress=False)
    assert (_scene(st).beats, _scene(st).idle) == (1, 1)
    d.note_beat(key="talk:travelers", progress=True)
    scene = _scene(st)
    assert (scene.beats, scene.last_progress, scene.idle) == (2, 2, 0)
    assert scene.repeats("inspect:hearth") == 1 and scene.repeats("talk:travelers") == 1
    d.note_beat()
    assert (_scene(st).beats, _scene(st).idle) == (3, 1)
    assert _scene(st).exhausted is False


# ---------------------------------------------------- micro-scenes and return


def test_a_bar_conversation_can_walk_upstairs():
    narrator = RecordingNarrator()
    engine_obj = _engine_in_inn("scene-upstairs", narrator)
    st = engine_obj.state
    out, checkpoint = _walk_upstairs(engine_obj)
    subs = st.scenes[UPSTAIRS]

    assert st.scene == UPSTAIRS
    assert st.location == INN  # the map has not moved: scenes are not the map
    assert (subs["label"], subs["parent"], subs["location"]) == (UPSTAIRS_LABEL, INN, INN)
    assert checkpoint == "scene"
    assert _last_beat(narrator) == "upstairs.first"
    facts = _last_facts(narrator)
    assert "her room above the common room" in facts     # where the stair leads
    assert "sit" in facts                                # she offers the chair
    assert out["dialogue"][0]["speaker"] == "Marla Voss"
    assert any(line.startswith("▸ Scene — The upstairs room") for line in out["system"])


def test_leaving_the_micro_scene_resolves_back_to_its_parent():
    narrator = RecordingNarrator()
    engine_obj = _engine_in_inn("scene-downstairs", narrator)
    st = engine_obj.state
    _walk_upstairs(engine_obj)
    back, checkpoint = engine_obj.act("I go back down to the bar")
    assert st.scene == INN and checkpoint == "scene"
    assert any(line.startswith("▸ Scene — The Lantern Inn") for line in back["system"])
    assert _last_beat(narrator) == "downstairs"
    # Walked out before the room's goal was met: it waits as it was left.
    assert st.scenes[UPSTAIRS]["state"] == TRANSITIONING
    # ... and picking the thread back up restores it as the live scene.
    _again, _ = engine_obj.act("I follow Marla upstairs")
    assert st.scene == UPSTAIRS and st.scenes[UPSTAIRS]["state"] == ACTIVE
    assert _last_beat(narrator) == "upstairs.return"
    assert "unfinished" in _last_facts(narrator)
    assert _beats(narrator).count("upstairs.first") == 1


def test_the_room_keeps_its_beat_count_across_the_trip_down():
    narrator = RecordingNarrator()
    engine_obj = _engine_in_inn("scene-keeps-count", narrator)
    st = engine_obj.state
    _walk_upstairs(engine_obj)
    engine_obj.act("I go back down to the bar")
    beats_upstairs = st.scenes[UPSTAIRS]["beats"]
    engine_obj.act("I inspect the hearth")  # beats land in the inn, not the room
    engine_obj.act("I follow Marla upstairs")
    assert st.scenes[UPSTAIRS]["beats"] == beats_upstairs + 1  # only the re-entry
    assert st.scenes[INN]["beats"] >= 2


def test_a_resolved_scene_never_replays_its_opening():
    narrator = RecordingNarrator()
    engine_obj = _engine_in_inn("scene-no-pull-back", narrator)
    st = engine_obj.state
    _walk_upstairs(engine_obj)
    assert _last_beat(narrator) == "upstairs.first"

    engine_obj.act("I ask Marla what she wanted to say")  # the room's goal is met
    assert st.scenes[UPSTAIRS]["state"] == RESOLVED
    engine_obj.act("I go back down to the bar")
    assert st.scenes[UPSTAIRS]["state"] == RESOLVED  # it was finished, not abandoned
    again, _ = engine_obj.act("I follow Marla upstairs")
    # The aftermath, once, and never the opening again.
    assert _last_beat(narrator) == "upstairs.aftermath"
    assert "said what she would not say at the bar" in _last_facts(narrator)
    assert _beats(narrator).count("upstairs.first") == 1
    assert MARK in again["narration"]


def test_leaving_and_returning_never_replays_the_inn_opening():
    narrator = RecordingNarrator()
    engine_obj = _engine_in_inn("scene-inn-return", narrator)
    st = engine_obj.state
    engine_obj.act("I step out and take the northern road")
    assert (st.location, st.scene) == ("northern-road", "northern-road")
    back, _ = engine_obj.act("I head back to the inn")
    assert st.scene == INN and st.location == INN
    assert _last_beat(narrator) == "return.inn"
    assert _beats(narrator).count("inn.first") == 1
    assert MARK in back["narration"]


# ------------------------------------------------------------ the invitation


def test_the_stair_is_hers_until_the_story_is_told():
    narrator = RecordingNarrator()
    engine_obj = _engine_in_inn("scene-stair-refused", narrator)
    out, checkpoint = engine_obj.act("I go up to her room")
    assert engine_obj.state.scene == INN  # no transition: the fiction refuses
    assert checkpoint == ""  # and nothing checkpoints for a room that never opened
    assert _last_beat(narrator) == "upstairs.refused"
    facts = _last_facts(narrator)
    assert "has not lent it" in facts                    # she says no, in her own words
    assert out["dialogue"][0]["speaker"] == "Marla Voss"
    # Asking her about the road is what opens it (§7d).
    _walk_upstairs(engine_obj)
    assert engine_obj.state.scene == UPSTAIRS


def test_a_live_invitation_is_never_blocked_by_the_anti_loop():
    narrator = RecordingNarrator()
    engine_obj = _engine_in_inn("scene-invite", narrator)
    engine_obj.act("I ask Marla about the travelers")
    for _ in range(5):
        engine_obj.act("I inspect the hearth")  # linger until the guard is fully on
    scene = _scene(engine_obj.state)
    assert scene.exhausted is True and scene.idle >= 4
    assert _last_beat(narrator) == "scene.diminished"    # repeats answer short
    # The door the fiction opened is taken, whatever the guard thinks.
    out, checkpoint = engine_obj.act("I follow Marla upstairs")
    assert checkpoint == "scene" and engine_obj.state.scene == UPSTAIRS
    assert _last_beat(narrator) == "upstairs.first"      # never a diminishing brief
    assert MARK in out["narration"]


# ------------------------------------------------------------- the anti-loop


def test_repeated_actions_diminish_and_point_at_what_is_still_possible():
    narrator = RecordingNarrator()
    engine_obj = _engine_in_inn("scene-diminish", narrator)
    first, _ = engine_obj.act("I inspect the hearth")
    assert _last_beat(narrator) == "inspect.room"
    second, _ = engine_obj.act("I inspect the hearth")

    # A second look is still the room's own beat — and the engine tells the
    # narrator it is the second look, so the words come back fresh (P13/P14).
    assert _last_beat(narrator) == "inspect.room"
    assert "gone over this ground before" in _last_facts(narrator)
    assert second["narration"] != first["narration"]
    # The third identical attempt is the guard's: a brief, open-ended reply.
    third, _ = engine_obj.act("I inspect the hearth")
    assert _last_beat(narrator) == "scene.diminished"
    assert "still open here" in _last_facts(narrator)
    assert third["narration"] != second["narration"]
    labels = [s["label"] for s in third["suggestions"]]
    assert "Read the notice board" in labels
    assert "Take the stairs with Marla" not in labels  # unheard: her stair is not on offer
    # What the chronicle shows is the reply the player saw, nothing else.
    assert engine_obj.state.feed[-1]["text"] == third["narration"]


def test_the_anti_loop_fires_later_than_it_used_to():
    """P13 tuning: the idle arm fires at 6 beats, not 4 — and repeats at 3."""
    from app.modules.story import scenes

    assert scenes.REPEAT_DIMINISH_AFTER == 3  # stop droning on literal repeats
    assert scenes.IDLE_DIMINISH_AFTER == 6  # fire later: room to breathe


def test_idle_beats_diminish_a_repeat_but_never_wall_off_new_ground():
    """P13: distinct attempts keep narrating; only walked ground answers short."""
    narrator = RecordingNarrator()
    engine_obj = _engine_in_inn("scene-linger", narrator)
    fresh = [
        "I inspect the hearth",
        "I inspect the hearth once more",
        "I inspect the hearth in detail",
        "I inspect the hearth very carefully",
        "I inspect the hearth from the door",
        "I inspect the hearth in the firelight",
        "I inspect the hearth a final time",
    ]
    start = len(narrator.narrator_prompts)
    outs = [engine_obj.act(t)[0] for t in fresh]
    scene = _scene(engine_obj.state)
    assert scene.idle >= 6 and scene.exhausted is True
    # Seven distinct attempts, seven answers from the room's own beat: the wall
    # never settles over the scene (before P13 every one of these was canned).
    assert _beats(narrator)[start:] == ["inspect.room"] * len(fresh)
    assert all(
        outs[i]["narration"] != outs[i - 1]["narration"] for i in range(1, len(outs))
    )
    # A genuine repeat on already-walked ground now answers with the guard's
    # brief... and a novel attempt still narrates: per-action, not a wall.
    repeat = engine_obj.act("I inspect the hearth")[0]
    assert _last_beat(narrator) == "scene.diminished"
    novel = engine_obj.act("I inspect the window frames")[0]
    assert _last_beat(narrator) == "inspect.room"
    assert novel["narration"] != repeat["narration"]
    # Progress also clears the clock outright.
    assert _scene(engine_obj.state).idle < 6


def test_diminishing_replies_are_written_fresh_every_time():
    """P13/P14: two diminishing replies are never the same words, and never a
    rotation of authored lines — the guard hands the model a brief, and the
    model answers it (one call per reply, the beat's own prose is never
    written and thrown away)."""
    narrator = RecordingNarrator()
    engine_obj = _engine_in_inn("scene-rotate", narrator)
    for _ in range(4):
        engine_obj.act("I inspect the hearth")  # walk the ground out
    start_calls = narrator.narrator_calls
    outs = [engine_obj.act("I inspect the hearth")[0] for _ in range(5)]
    narrations = [o["narration"] for o in outs]
    assert narrator.narrator_calls == start_calls + 5          # one call per reply
    assert _beats(narrator)[-5:] == ["scene.diminished"] * 5
    assert all(MARK in n for n in narrations)                  # the model's words
    assert all(a != b for a, b in itertools.pairwise(narrations))
    # The chronicle carries exactly what the player read.
    assert [e["text"] for e in engine_obj.state.feed if e["kind"] == "narration"][-5:] == (
        narrations
    )


def test_possible_moves_follow_the_story_and_the_scene():
    # The prologue guides but never walls: three ways down, one of them the door.
    road = seeded_state()
    assert road.location == PROLOGUE
    assert [m["label"] for m in possible_moves(road)] == [
        "Look over the town below",
        "Head down to the Lantern",
        "Listen to the night",
    ]
    # Inside the inn the moves follow the story's stage (§1).
    engine_obj = _engine_in_inn("scene-moves", RecordingNarrator())
    st = engine_obj.state
    unheard = [m["label"] for m in possible_moves(st)]
    assert "Ask Marla about the road" in unheard
    assert "Take the stairs with Marla" not in unheard
    st.lead_stage = "rumored"
    assert "Take the stairs with Marla" in [m["label"] for m in possible_moves(st)]
    SceneDirector(st).enter(UPSTAIRS, label=UPSTAIRS_LABEL, location=INN, parent=INN)
    assert [m["label"] for m in possible_moves(st)][:2] == [
        "Head back down to the bar",
        "Ask her what she brought you up here for",
    ]


# --------------------------------------------------- boundaries and checkpoints


def test_a_story_boundary_moves_the_scenes_aim_not_the_player():
    narrator = RecordingNarrator()
    engine_obj = _engine_in_inn("scene-boundary", narrator)
    st = engine_obj.state
    before = _scene(st).goal
    engine_obj.act("I ask Marla about the travelers")
    scene = _scene(st)
    assert scene.goal != before and "Marla" in scene.goal
    assert (scene.id, st.location) == (INN, INN)  # nobody was moved anywhere


def test_the_scene_transition_uses_a_real_checkpoint_name():
    assert CHECKPOINT_BY_KIND["scene"] == "scene_transition"
    assert CHECKPOINT_BY_KIND["scene"] in AUTOSAVE_CHECKPOINTS
    # The names other systems already read are untouched (§7).
    assert CHECKPOINT_BY_KIND["travel"] == "travel"
    assert CHECKPOINT_BY_KIND["inspect"] == "scene_transition"
    assert CHECKPOINT_BY_KIND["talk"] == "important_dialogue"


def test_a_scene_transition_autosaves_a_checkpoint(
    client: TestClient, recording_narrator: RecordingNarrator
):
    h, cid = _setup(client, "scene-autosave@example.com")
    _act(client, h, "I head down to the inn and step inside")
    _act(client, h, "I ask Marla about the travelers")
    before = client.get(f"/campaigns/{cid}/saves", headers=h).json()
    assert "scene_transition" not in {r["checkpoint"] for r in before}
    _act(client, h, "I follow Marla upstairs")
    after = client.get(f"/campaigns/{cid}/saves", headers=h).json()
    assert len(after) == len(before) + 1
    assert "scene_transition" in {r["checkpoint"] for r in after}


# ---------------------------------------------------------- the live surface


def test_state_carries_the_scene_the_player_stands_in(
    client: TestClient, recording_narrator: RecordingNarrator
):
    h, _cid = _setup(client, "scene-state@example.com")
    _act(client, h, "I head down to the inn and step inside")
    _act(client, h, "I ask Marla about the travelers")
    _act(client, h, "I follow Marla upstairs")
    s = client.get("/state", headers=h).json()
    assert s["scene"]["id"] == UPSTAIRS
    assert s["scene"]["label"] == UPSTAIRS_LABEL
    assert s["scene"]["parent"] == INN
    assert s["scene"]["state"] == ACTIVE
    assert s["scene"]["goal"] == UPSTAIRS_GOAL
    assert s["scene"]["beats"] >= 1
    assert s["location"] == "The Lantern Inn, Ravenford"  # the map still reads the inn
    assert [n["name"] for n in s["npcs"]] == ["Marla Voss"]  # the micro-scene narrows the room


def test_scene_state_survives_save_and_load(
    client: TestClient, recording_narrator: RecordingNarrator
):
    h, cid = _setup(client, "scene-save-load@example.com")
    _act(client, h, "I head down to the inn and step inside")
    _act(client, h, "I ask Marla about the travelers")
    _act(client, h, "I follow Marla upstairs")
    assert _state(cid).scene == UPSTAIRS
    save = client.post(f"/campaigns/{cid}/saves", headers=h, json={"label": "upstairs"}).json()
    # Away from the room and off the map entirely...
    _act(client, h, "I go back down to the bar")
    _act(client, h, "I step out and take the northern road")
    assert _state(cid).scene == "northern-road"
    # ... the page turns back to the room the player actually stood in.
    r = client.post(f"/saves/{save['id']}/load", headers=h)
    assert r.status_code == 200 and r.json()["loaded"] is True
    st = _state(cid)
    assert st.scene == UPSTAIRS
    assert st.scenes[UPSTAIRS]["parent"] == INN
    assert st.scenes[UPSTAIRS]["beats"] >= 1
    assert client.get("/state", headers=h).json()["scene"]["label"] == UPSTAIRS_LABEL


class _CapturingProvider:
    """Records the prompt the pipeline hands the narrator."""

    model_name = "capture"

    def __init__(self) -> None:
        self.prompts: list[str] = []

    def generate(self, prompt: str, *, role: str = "narrator", max_tokens: int = 600, **kwargs):
        self.prompts.append(prompt)
        return ProviderResult(
            text='{"narration": "The rain keeps on.", "npc_dialogue": [], "suggested_actions": []}'
        )


def test_the_narrator_sees_the_scene_and_its_goal():
    st = seeded_state()
    st.lead_stage = "rumored"
    SceneDirector(st).enter(
        UPSTAIRS, label=UPSTAIRS_LABEL, location=INN, parent=INN, goal=UPSTAIRS_GOAL
    )
    prov = _CapturingProvider()
    ActEngine(PlaySession("scene-prompt", st), provider=prov).act(
        "I hum a lamplighter's tune at the little window"
    )
    assert prov.prompts
    assert f"{UPSTAIRS_LABEL} (active)" in prov.prompts[0]
    assert f"scene goal: {UPSTAIRS_GOAL}" in prov.prompts[0]


def test_the_scene_line_names_the_scene_its_goal_and_the_action():
    line = scene_line(
        "listens at the door",
        {"scene_label": UPSTAIRS_LABEL, "scene_goal": UPSTAIRS_GOAL, "scene_state": ACTIVE},
    )
    assert line == (
        f"{UPSTAIRS_LABEL} ({ACTIVE}) — PC acts: listens at the door — scene goal: {UPSTAIRS_GOAL}"
    )
    assert scene_line("waits", {}) == "PC acts: waits"
