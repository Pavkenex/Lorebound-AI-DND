"""Mood layer (systems slice 3): vocabulary, decay, event moods, prompt, payload.

Covers docs/SYSTEMS_DESIGN.md §4: PlayState.moods + set_mood()/mood_of() with a
per-character baseline and per-hour decay on the clock, the beat sites that set
moods (kindness, snooping, theft, brawl, intimidation, flirt, resolution, long
day), NSFW content gating on every surface (save, chip, narrator prompt), the
[NPCs present] prompt block, and the GET /state ``mood``/``mood_intensity``
payload. Also covers save/load: a restore keeps moods as of the save.
"""
from __future__ import annotations

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from app.core.database import Base, get_db
from app.main import app
from app.modules.ai.providers import ProviderResult
from app.modules.auth.models import User  # noqa: F401  (register metadata)
from app.modules.campaign import models as cm  # noqa: F401
from app.modules.campaign import npc as _npc  # noqa: F401
from app.modules.campaign import story as _story  # noqa: F401
from app.modules.campaign import world as _world  # noqa: F401
from app.modules.character import models as _char  # noqa: F401
from app.modules.inventory import models as _inv  # noqa: F401
from app.modules.narrator.prefs import ContentPrefs
from app.modules.narrator.prompts import PromptContext, assemble_prompt
from app.modules.npc import mood as mood_mod
from app.modules.npc.mood import (
    DEFAULT_MOOD,
    MOOD_DECAY_PER_HOUR,
    MOOD_FALLBACK,
    MOOD_VOCAB,
    baseline_mood,
    clamp_intensity,
    surfaced_mood,
)
from app.modules.play import models as pm  # noqa: F401
from app.modules.play.engine import ActEngine
from app.modules.play.models import PlayStateRow
from app.modules.play.session import PlaySession
from app.modules.play.state import PlayState

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
    return headers, c.json()["id"]


def _act(client: TestClient, headers: dict, text: str, *, seed: int | None = None) -> dict:
    body: dict = {"text": text}
    if seed is not None:
        body["seed_roll"] = seed
    r = client.post("/act", headers=headers, json=body)
    assert r.status_code == 200, r.text
    data = r.json()
    if data.get("pending_check"):
        r = client.post("/act", headers=headers, json={
            "text": text, "roll": 18, "pending_token": data["pending_check"]["token"],
        })
        assert r.status_code == 200, r.text
        data = r.json()
    return data


def _state(campaign_id: str) -> PlayState:
    db = TestingSession()
    try:
        row = db.get(PlayStateRow, campaign_id)
        return PlayState.from_json(row.state_json)
    finally:
        db.close()


def _npc_entry(payload: dict, name: str) -> dict:
    return next(n for n in payload["npcs"] if n["name"] == name)


def _state_npc(client: TestClient, headers: dict, name: str) -> dict:
    r = client.get("/state", headers=headers)
    assert r.status_code == 200, r.text
    return _npc_entry(r.json(), name)


# ------------------------------------------------------ unit: vocab + set/read


def test_set_mood_validates_and_clamps():
    st = PlayState()
    # No live mood reads as the resting baseline, never an invented word.
    assert st.mood_of("marla") == {"mood": "neutral", "intensity": 0.0}
    assert st.mood_of("") == {"mood": DEFAULT_MOOD, "intensity": 0.0}

    assert st.set_mood("marla", "amused", 0.6) == {"mood": "amused", "intensity": 0.6}
    assert st.set_mood("marla", "ANGRY", 1.7) == {"mood": "angry", "intensity": 1.0}
    assert st.set_mood("marla", "sad", -2.0)["intensity"] == 0.0  # settles back
    assert st.set_mood("marla", "sad", "junk")["mood"] == "neutral"
    with pytest.raises(ValueError):
        st.set_mood("marla", "smug", 0.5)  # off-vocabulary never enters the save

    assert clamp_intensity(float("nan")) == 0.0
    assert clamp_intensity("junk") == 0.0
    assert set(MOOD_VOCAB) >= {"neutral", "flirty", "horny", "tired", "proud"}


def test_mood_decays_toward_the_baseline_on_the_clock():
    st = PlayState()
    st.set_mood("marla", "warm", 1.0)
    st.advance_minutes(60)
    assert st.mood_of("marla") == {"mood": "warm", "intensity": 1.0 - MOOD_DECAY_PER_HOUR}
    st.advance_minutes(120)
    assert round(st.mood_of("marla")["intensity"], 4) == round(1.0 - 3 * MOOD_DECAY_PER_HOUR, 4)
    # Past the threshold the mood has settled: baseline word, zero intensity.
    st.advance_minutes(120)
    assert st.mood_of("marla") == {"mood": "neutral", "intensity": 0.0}
    # Time only moves forward: no advance, no decay.
    st.set_mood("marla", "tired", 0.5)
    st.advance_minutes(0)
    st.advance_minutes(-30)
    assert st.mood_of("marla") == {"mood": "tired", "intensity": 0.5}


def test_baseline_is_per_character(monkeypatch):
    monkeypatch.setitem(mood_mod.MOOD_BASELINES, "borin", "suspicious")
    try:
        assert baseline_mood("borin") == "suspicious"
        assert baseline_mood("marla") == DEFAULT_MOOD
        st = PlayState()
        st.set_mood("borin", "angry", 0.5)
        st.advance_minutes(24 * 60)
        assert st.mood_of("borin") == {"mood": "suspicious", "intensity": 0.0}
    finally:
        monkeypatch.undo()


def test_mood_rides_playstate_json():
    st = PlayState()
    st.set_mood("marla", "amused", 0.6)
    st.set_mood("borin", "afraid", 0.7)
    restored = PlayState.from_json(st.to_json())
    assert restored.moods == {
        "marla": {"mood": "amused", "intensity": 0.6},
        "borin": {"mood": "afraid", "intensity": 0.7},
    }
    assert restored.mood_of("marla")["mood"] == "amused"
    # An old save without moods reads as everyone at rest.
    legacy = PlayState.from_json('{"version": 1, "day": 2}')
    assert legacy.mood_of("marla") == {"mood": DEFAULT_MOOD, "intensity": 0.0}


def test_gated_words_fall_back_without_content_settings():
    assert surfaced_mood("flirty", nsfw=False) == MOOD_FALLBACK["flirty"] == "warm"
    assert surfaced_mood("horny", nsfw=False) == MOOD_FALLBACK["horny"] == "amused"
    assert surfaced_mood("flirty", nsfw=True) == "flirty"
    assert surfaced_mood("warm", nsfw=False) == "warm"
    # Fallbacks are never gated: surfacing twice changes nothing.
    assert surfaced_mood(surfaced_mood("horny"), nsfw=False) == "amused"


# ------------------------------------------------------- unit: narrator prompt


def test_narrator_block_renders_mood_next_to_memories():
    ctx = PromptContext(npcs=[
        {
            "name": "Marla", "note": "wiping a cup", "mood": "amused", "mood_intensity": 0.6,
            "remembers": ["promised to look into the missing travelers"],
        },
        {"name": "Borin", "note": "nursing a grudge", "mood": "neutral", "mood_intensity": 0.0},
    ])
    user = assemble_prompt(ctx).user
    assert "Marla: wiping a cup | mood: amused (0.6) | remembers about the player: " \
           "promised to look into the missing travelers" in user
    # A settled character carries no mood line — the block stays lean.
    assert "Borin: nursing a grudge" in user
    assert "Borin: nursing a grudge | mood" not in user


def test_narrator_prompt_never_carries_a_gated_word_the_settings_forbid():
    def _prompt(prefs: ContentPrefs) -> str:
        return assemble_prompt(
            PromptContext(npcs=[{
                "name": "Marla", "note": "cupping a hand", "mood": "flirty", "mood_intensity": 0.6,
            }]),
            prefs=prefs,
        ).user

    boundaries_off = _prompt(ContentPrefs(nsfw=False))
    assert "mood: warm (0.6)" in boundaries_off
    assert "flirty" not in boundaries_off
    uncensored = _prompt(ContentPrefs(nsfw=True))
    assert "mood: flirty (0.6)" in uncensored


# ------------------------------------------------------------- moods from beats


def test_talk_warms_marla_and_she_settles_between_beats(client: TestClient):
    h, cid = _setup(client, "mood-talk@example.com")
    _act(client, h, "I ask Marla about the travelers")
    st = _state(cid)
    assert st.mood_of("marla") == {"mood": "warm", "intensity": 0.5}
    # The promise to look into it warms her further.
    _act(client, h, "I ask Marla what she needs from me")
    assert _state(cid).mood_of("marla") == {"mood": "warm", "intensity": 0.7}


def test_snooping_reads_suspicious(client: TestClient):
    h, cid = _setup(client, "mood-ledger@example.com")
    _act(client, h, "I read Marla's guest ledger", seed=18)
    assert _state(cid).mood_of("marla") == {"mood": "suspicious", "intensity": 0.4}

    h2, cid2 = _setup(client, "mood-cellar@example.com")
    _act(client, h2, "I ask Marla about the travelers")
    _act(client, h2, "I ask Marla what she needs from me")
    _act(client, h2, "I inspect the cellar door")
    assert _state(cid2).mood_of("marla") == {"mood": "suspicious", "intensity": 0.5}


def test_steal_moods_escalate_clean_seen_caught(client: TestClient):
    # Stealth +4 vs Moderate 13: seed 15 = clean, seed 10 = seen, seed 1 = caught.
    h, cid = _setup(client, "mood-steal-clean@example.com")
    _act(client, h, "I steal from the storeroom strongbox", seed=15)
    assert _state(cid).mood_of("marla") == {"mood": "suspicious", "intensity": 0.5}

    h2, cid2 = _setup(client, "mood-steal-seen@example.com")
    _act(client, h2, "I steal from the storeroom strongbox", seed=10)
    assert _state(cid2).mood_of("marla") == {"mood": "suspicious", "intensity": 0.8}

    h3, cid3 = _setup(client, "mood-steal-caught@example.com")
    _act(client, h3, "I break into the strongbox in the pantry", seed=1)
    assert _state(cid3).mood_of("marla") == {"mood": "angry", "intensity": 0.8}


def test_brawl_and_intimidation_set_fear_and_anger(client: TestClient):
    # Brawl won (seed 18): Marla is angry at the violence, Borin at the loss.
    h, cid = _setup(client, "mood-brawl-win@example.com")
    _act(client, h, "I fight Borin by the fire", seed=18)
    st = _state(cid)
    assert st.mood_of("marla") == {"mood": "angry", "intensity": 0.7}
    assert st.mood_of("borin") == {"mood": "angry", "intensity": 0.8}

    # Brawl lost (seed 8): the mercenary keeps the upper hand — and the grin.
    h2, cid2 = _setup(client, "mood-brawl-lose@example.com")
    _act(client, h2, "I attack Borin", seed=8)
    assert _state(cid2).mood_of("borin") == {"mood": "amused", "intensity": 0.6}

    # Cowing him (seed 18) frightens him — the "afraid" leg of a violent beat.
    h3, cid3 = _setup(client, "mood-intimidate@example.com")
    _act(client, h3, "I intimidate Borin", seed=18)
    assert _state(cid3).mood_of("borin") == {"mood": "afraid", "intensity": 0.7}


def test_resolution_lifts_the_room(client: TestClient):
    h, cid = _setup(client, "mood-resolve@example.com")
    _act(client, h, "I ask Marla about the travelers")
    _act(client, h, "I ask Marla what she needs from me")
    _act(client, h, "I step out into the rain and take the northern road")
    _act(client, h, "I study the wagon ruts at the crossroads", seed=18)
    _act(client, h, "I follow the lanterns through the trees", seed=18)
    _act(client, h, "I head to the old monastery")
    fin = _act(client, h, "I confront what waits below and enter the cellar")
    assert fin["dialogue"] and fin["dialogue"][0]["speaker"] == "The elder traveler"

    st = _state(cid)
    assert st.mood_of("marla") == {"mood": "happy", "intensity": 0.9}
    assert st.mood_of("borin") == {"mood": "happy", "intensity": 0.6}
    assert st.mood_of("sella") == {"mood": "proud", "intensity": 0.6}


def test_a_long_day_leaves_the_innkeeper_tired(client: TestClient):
    h, cid = _setup(client, "mood-rest@example.com")
    _act(client, h, "I rest for the night")
    assert _state(cid).mood_of("marla") == {"mood": "tired", "intensity": 0.6}


def test_mood_decays_as_play_advances_the_clock(client: TestClient):
    h, cid = _setup(client, "mood-decay-play@example.com")
    _act(client, h, "I ask Marla about the travelers")  # warm 0.5
    _act(client, h, "I walk to the market")  # +15 min; Marla is off-screen
    assert _state(cid).mood_of("marla")["intensity"] == 0.4375  # 0.5 − 0.25 × 0.25 h
    # Back on screen the chip carries the decayed value, not the value she wore.
    _act(client, h, "I go back to the inn")  # +10 min more
    marla = _state_npc(client, h, "Marla Voss")
    assert marla["mood"] == "warm" and marla["mood_intensity"] == 0.4
    assert _state(cid).mood_of("marla")["intensity"] == 0.3958


# ------------------------------------------------------------- prefs gating


def test_flirt_surfaces_gated_by_content_settings(client: TestClient):
    # Default boundaries: the flirt is stored in its fallback family and the
    # chip reads warm — no gated word ever reaches a boundary-respecting save.
    h, cid = _setup(client, "mood-flirt-sfw@example.com")
    _act(client, h, "I flirt with Marla")
    st = _state(cid)
    assert st.moods["marla"] == {"mood": "warm", "intensity": 0.6}
    assert st.attitudes["marla"] == 3  # the paired memory still moves the meter
    marla = _state_npc(client, h, "Marla Voss")
    assert marla["mood"] == "warm" and marla["mood_intensity"] == 0.6

    # Uncensored campaign: the authored word surfaces everywhere.
    h2, cid2 = _setup(client, "mood-flirt-nsfw@example.com")
    uncensored = {**h2, "X-Content-Prefs": '{"nsfw": true}'}
    r = client.post("/act", headers=uncensored, json={"text": "I wink at Marla across the bar"})
    assert r.status_code == 200, r.text
    assert _state(cid2).moods["marla"] == {"mood": "flirty", "intensity": 0.6}
    st2 = client.get("/state", headers=uncensored).json()
    assert _npc_entry(st2, "Marla Voss")["mood"] == "flirty"
    # A stricter surface re-gates the very same save on read.
    plain = client.get("/state", headers=h2).json()
    assert _npc_entry(plain, "Marla Voss")["mood"] == MOOD_FALLBACK["flirty"]


# ------------------------------------------------------------------ GET /state


def test_state_payload_carries_mood_and_intensity_for_every_present_npc(client: TestClient):
    h, _cid = _setup(client, "mood-payload@example.com")
    s = client.get("/state", headers=h).json()
    marla = _npc_entry(s, "Marla Voss")
    assert marla["mood"] == "neutral" and marla["mood_intensity"] == 0.0
    borin = _npc_entry(s, "Borin")
    assert borin["mood"] == "neutral" and borin["mood_intensity"] == 0.0

    _act(client, h, "I steal from the storeroom strongbox", seed=10)
    s = client.get("/state", headers=h).json()
    marla = _npc_entry(s, "Marla Voss")
    assert marla["mood"] == "suspicious" and marla["mood_intensity"] == 0.8


# -------------------------------------------------------------- save/restore


def test_mood_rides_saves(client: TestClient):
    h, cid = _setup(client, "mood-saveload@example.com")
    _act(client, h, "I ask Marla about the travelers")  # warm 0.5
    save = client.post(
        f"/campaigns/{cid}/saves", headers=h, json={"label": "Warm evening"}
    ).json()

    _act(client, h, "I fight Borin by the fire", seed=18)  # anger moves in
    s = client.get("/state", headers=h).json()
    assert _npc_entry(s, "Marla Voss")["mood"] == "angry"
    assert _npc_entry(s, "Borin")["mood"] == "angry"

    r = client.post(f"/saves/{save['id']}/load", headers=h)
    assert r.status_code == 200 and r.json()["loaded"] is True
    s = client.get("/state", headers=h).json()
    assert _npc_entry(s, "Marla Voss")["mood"] == "warm"  # as of the save
    assert _npc_entry(s, "Borin")["mood"] == "neutral"


# ------------------------------------------------- narrator sees the live mood


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


def test_pipeline_beat_hands_the_live_mood_to_the_narrator():
    st = PlayState()
    st.set_mood("marla", "warm", 0.5)
    session = PlaySession("mood-pipeline", st)
    prov = _CapturingProvider()
    engine_obj = ActEngine(session, provider=prov)
    # Free text with no beat keywords: the pipeline path builds the prompt.
    engine_obj.act("I settle by the hearth and hum a lamplighter's tune")
    assert prov.prompts, "the narrator was never called"
    assert "mood: warm (0.5)" in prov.prompts[0]
    assert "Marla" in prov.prompts[0]
