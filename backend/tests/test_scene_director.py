"""Scene director (systems slice 5): micro-scenes, invitations, the anti-loop.

Covers docs/SYSTEMS_DESIGN.md §7: the scene model carried in ``PlayState`` (the
current scene, the remembered scenes, the sub-scene stack), per-beat bookkeeping
with progress detection, diminishing replies for repeated/idle actions that
point at what is still possible, the no-pull-back invariant (a resolved scene
never re-runs its opening and narration never drags the player back), the
invitation as a transition trigger the guard never blocks, the
scene-transition autosave, and scene state surviving save/load.
"""
from __future__ import annotations

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
from app.modules.play.engine import (
    UPSTAIRS_AFTERMATH,
    UPSTAIRS_OPENING,
    UPSTAIRS_REFUSED,
    ActEngine,
)
from app.modules.play.models import PlayStateRow
from app.modules.play.session import CHECKPOINT_BY_KIND, PlaySession
from app.modules.play.state import OPENING_BEAT, PlayState, seeded_state
from app.modules.story.scenes import (
    ACTIVE,
    RESOLVED,
    SCENE_STATES,
    TRANSITIONING,
    UPSTAIRS,
    UPSTAIRS_GOAL,
    UPSTAIRS_LABEL,
    SceneDirector,
    possible_moves,
)

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


def _engine(campaign_id: str, st: PlayState | None = None) -> ActEngine:
    """An engine over a fresh campaign's state — the engine itself needs no DB."""
    return ActEngine(PlaySession(campaign_id, st if st is not None else seeded_state()))


def _scene(state: PlayState):
    return SceneDirector(state).current()


def _walk_upstairs(engine_obj: ActEngine) -> tuple[dict, str | None]:
    """Tell Marla the story (the stair is lent, §7d) and take it."""
    engine_obj.act("I ask Marla about the travelers")
    return engine_obj.act("I follow Marla upstairs")


# ------------------------------------------------------------- the scene model


def test_a_fresh_campaign_opens_standing_in_the_inn():
    st = seeded_state()
    assert st.scene == INN
    scene = SceneDirector(st).current()
    assert (scene.id, scene.label, scene.state) == (INN, "The Lantern Inn — common room", ACTIVE)
    assert scene.goal == "Take the measure of the room"
    assert scene.beats == 0 and scene.last_progress == 0
    # The opening beat *is* that scene's opening, so it never replays (it is one
    # narration in the feed, not a line a beat re-prints).
    assert [e.get("text", "") for e in st.feed].count(OPENING_BEAT) == 1
    assert scene.state in SCENE_STATES


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
    engine_obj = _engine("scene-upstairs")
    st = engine_obj.state
    out, checkpoint = _walk_upstairs(engine_obj)
    subs = st.scenes[UPSTAIRS]
    assert st.scene == UPSTAIRS
    assert st.location == INN  # the map has not moved: scenes are not the map
    assert (subs["label"], subs["parent"], subs["location"]) == (UPSTAIRS_LABEL, INN, INN)
    assert checkpoint == "scene"
    assert UPSTAIRS_OPENING in out["narration"]
    assert out["dialogue"][0]["speaker"] == "Marla Voss"
    assert any(line.startswith("▸ Scene — The upstairs room") for line in out["system"])


def test_leaving_the_micro_scene_resolves_back_to_its_parent():
    engine_obj = _engine("scene-downstairs")
    st = engine_obj.state
    _walk_upstairs(engine_obj)
    back, checkpoint = engine_obj.act("I go back down to the bar")
    assert st.scene == INN and checkpoint == "scene"
    assert any(line.startswith("▸ Scene — The Lantern Inn") for line in back["system"])
    # Walked out before the room's goal was met: it waits as it was left.
    assert st.scenes[UPSTAIRS]["state"] == TRANSITIONING
    # ... and picking the thread back up restores it as the live scene.
    again, _ = engine_obj.act("I follow Marla upstairs")
    assert st.scene == UPSTAIRS and st.scenes[UPSTAIRS]["state"] == ACTIVE
    assert UPSTAIRS_OPENING not in again["narration"]  # the opening played once
    assert "as you left it" in again["narration"]


def test_the_room_keeps_its_beat_count_across_the_trip_down():
    engine_obj = _engine("scene-keeps-count")
    st = engine_obj.state
    _walk_upstairs(engine_obj)
    engine_obj.act("I go back down to the bar")
    beats_upstairs = st.scenes[UPSTAIRS]["beats"]
    engine_obj.act("I inspect the hearth")  # beats land in the inn, not the room
    engine_obj.act("I follow Marla upstairs")
    assert st.scenes[UPSTAIRS]["beats"] == beats_upstairs + 1  # only the re-entry
    assert st.scenes[INN]["beats"] >= 2


def test_a_resolved_scene_never_replays_its_opening():
    engine_obj = _engine("scene-no-pull-back")
    st = engine_obj.state
    opened, _ = _walk_upstairs(engine_obj)
    assert opened["narration"] == UPSTAIRS_OPENING
    engine_obj.act("I ask Marla what she wanted to say")  # the room's goal is met
    assert st.scenes[UPSTAIRS]["state"] == RESOLVED
    engine_obj.act("I go back down to the bar")
    assert st.scenes[UPSTAIRS]["state"] == RESOLVED  # it was finished, not abandoned
    again, _ = engine_obj.act("I follow Marla upstairs")
    assert UPSTAIRS_OPENING not in again["narration"]
    assert again["narration"] == UPSTAIRS_AFTERMATH  # the aftermath, once, softly
    assert [e.get("text", "") for e in st.feed].count(UPSTAIRS_OPENING) == 1


def test_leaving_and_returning_never_replays_the_inn_opening():
    engine_obj = _engine("scene-inn-return")
    st = engine_obj.state
    engine_obj.act("I step out and take the northern road")
    assert (st.location, st.scene) == ("northern-road", "northern-road")
    back, _ = engine_obj.act("I head back to the inn")
    assert st.scene == INN and st.location == INN
    assert OPENING_BEAT not in back["narration"]
    assert [e["text"] for e in st.feed].count(OPENING_BEAT) == 1


# ------------------------------------------------------------ the invitation


def test_the_stair_is_hers_until_the_story_is_told():
    engine_obj = _engine("scene-stair-refused")
    out, checkpoint = engine_obj.act("I go up to her room")
    assert engine_obj.state.scene == INN  # no transition: the fiction refuses
    assert checkpoint == ""  # and nothing checkpoints for a room that never opened
    assert out["narration"] == UPSTAIRS_REFUSED
    # Asking her about the road is what opens it (§7d).
    _walk_upstairs(engine_obj)
    assert engine_obj.state.scene == UPSTAIRS


def test_a_live_invitation_is_never_blocked_by_the_anti_loop():
    engine_obj = _engine("scene-invite")
    engine_obj.act("I ask Marla about the travelers")
    for _ in range(5):
        engine_obj.act("I inspect the hearth")  # linger until the guard is fully on
    scene = _scene(engine_obj.state)
    assert scene.exhausted is True and scene.idle >= 4
    assert "Still open:" in engine_obj.act("I inspect the hearth")[0]["narration"]
    # The door the fiction opened is taken, whatever the guard thinks.
    out, checkpoint = engine_obj.act("I follow Marla upstairs")
    assert checkpoint == "scene" and engine_obj.state.scene == UPSTAIRS
    assert "Still open:" not in out["narration"]
    assert UPSTAIRS_OPENING in out["narration"]


# ------------------------------------------------------------- the anti-loop


def test_repeated_actions_diminish_and_point_at_what_is_still_possible():
    engine_obj = _engine("scene-diminish")
    first, _ = engine_obj.act("I inspect the hearth")
    second, _ = engine_obj.act("I inspect the hearth")
    third, _ = engine_obj.act("I inspect the hearth")
    assert second["narration"] == first["narration"]  # the guard is not hair-triggered
    assert third["narration"] != first["narration"]
    assert len(third["narration"]) < len(first["narration"])
    assert "Still open:" in third["narration"]
    labels = [s["label"] for s in third["suggestions"]]
    assert "Read the notice board" in labels
    assert "Take the stairs with Marla" not in labels  # unheard: her stair is not on offer
    # What the chronicle shows is the diminishing reply, not the lost prose.
    assert engine_obj.state.feed[-1]["text"] == third["narration"]


def test_a_run_of_idle_beats_also_diminishes():
    engine_obj = _engine("scene-linger")
    beats = [
        "I inspect the hearth",
        "I inspect the hearth once more",
        "I inspect the hearth in detail",
        "I inspect the hearth very carefully",
    ]
    outs = [engine_obj.act(t)[0] for t in beats]
    assert "Still open:" not in outs[2]["narration"]  # three idle beats: still the room
    fifth = engine_obj.act("I inspect the hearth from the door")[0]
    assert "Still open:" in fifth["narration"]
    scene = _scene(engine_obj.state)
    assert scene.idle >= 4 and scene.exhausted is True


def test_possible_moves_follow_the_story_and_the_scene():
    st = seeded_state()
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
    engine_obj = _engine("scene-boundary")
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


def test_a_scene_transition_autosaves_a_checkpoint(client: TestClient):
    h, cid = _setup(client, "scene-autosave@example.com")
    _act(client, h, "I ask Marla about the travelers")
    before = client.get(f"/campaigns/{cid}/saves", headers=h).json()
    assert "scene_transition" not in {r["checkpoint"] for r in before}
    _act(client, h, "I follow Marla upstairs")
    after = client.get(f"/campaigns/{cid}/saves", headers=h).json()
    assert len(after) == len(before) + 1
    assert "scene_transition" in {r["checkpoint"] for r in after}


# ---------------------------------------------------------- the live surface


def test_state_carries_the_scene_the_player_stands_in(client: TestClient):
    h, _cid = _setup(client, "scene-state@example.com")
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


def test_scene_state_survives_save_and_load(client: TestClient):
    h, cid = _setup(client, "scene-save-load@example.com")
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
