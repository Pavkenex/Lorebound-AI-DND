"""Content boundaries on the engine path (phase 2 P8, docs/INTEGRATION_PLAN.md §11).

Offline: real app + engine router, in-memory app DB, throwaway ``ENGINE_DATA_DIR``,
and a recording fake adapter on the live path so the assembled prompt can be
inspected. What this module pins:

* the router parses ``X-Content-Prefs`` with ``narrator.prefs.ContentPrefs`` —
  same vocabulary, same aliases, no parallel parser;
* the directive ``ContentPrefs.describe_for_prompt()`` reaches the prompt's
  system message verbatim (the boundaries the model actually sees);
* absent input means the defaults, silently — an unreadable payload means the
  defaults WITH a system note (the legacy silent-drop bug stays dead);
* the applied prefs (nsfw + the four levels) are echoed in the turn payload and
  the header payload itself is never logged, persisted, or echoed.
"""
from __future__ import annotations

import json
import logging
from pathlib import Path
from typing import Any

import pytest
from engine.models import ChatResponse, ProviderCaps, ToolCall
from engine.pipeline import default_ruleset_text
from engine.providers.jsonproto import PROPOSE_DELTAS_TOOL_NAME
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from app.core.config import settings
from app.core.database import Base, get_db
from app.main import app
from app.modules.auth.models import User  # noqa: F401  (register metadata)
from app.modules.campaign import models as cm  # noqa: F401
from app.modules.campaign import npc as _npc  # noqa: F401
from app.modules.campaign import story as _story  # noqa: F401
from app.modules.campaign import world as _world  # noqa: F401
from app.modules.character import models as _char  # noqa: F401
from app.modules.engine import bridge
from app.modules.engine import router as engine_router
from app.modules.inventory import models as _inv  # noqa: F401
from app.modules.narrator.prefs import ContentPrefs
from app.modules.play import models as pm  # noqa: F401

test_engine = create_engine(
    "sqlite://", connect_args={"check_same_thread": False}, poolclass=StaticPool
)
TestingSession = sessionmaker(bind=test_engine, autoflush=False, autocommit=False)
Base.metadata.create_all(bind=test_engine)

#: The ruleset as the context assembler delivers it (instruction suffix for the
#: system message; the directive rides after it, never truncated).
RULESET = default_ruleset_text().strip()

#: A distinctive string smuggled inside a header payload: any leak is greppable.
HEADER_CANARY = "p8-header-canary-2f6c9e"

#: Distinctive runtime key literal (ASCII — httpx refuses non-ASCII headers).
RUNTIME_KEY = "sk-p8-runtime-key-not-a-secret"

#: Readable, non-default prefs (the four levels each moved off "standard").
CUSTOM = {"violence": "off", "horror": "reduced", "romance": "reduced",
          "language": "reduced", "nsfw": False}


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


@pytest.fixture(autouse=True)
def engine_data_dir(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    target = tmp_path / "engine_data"
    monkeypatch.setattr(settings, "ENGINE_DATA_DIR", str(target))
    return target


@pytest.fixture(autouse=True)
def engine_mode_on(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(settings, "ENGINE_MODE", True)


# --------------------------------------------------------------------------- #
# Helpers
# --------------------------------------------------------------------------- #


def _tool_names(request: Any) -> set[str]:
    return {
        spec["function"]["name"] for spec in (getattr(request, "tools", None) or [])
    }


class _RecordingAdapter:
    """Fake live adapter (the P2/P3 shape) that keeps every prompt it was sent."""

    name = "openai"

    def __init__(self, cfg: Any, api_key: str | None, recorder: _Recorder) -> None:
        self.cfg = cfg
        self.api_key = api_key
        self.recorder = recorder
        self.caps = ProviderCaps(native_tools=True)
        self.requests: list[Any] = []

    def capabilities(self) -> ProviderCaps:
        return self.caps

    def secrets(self) -> tuple[str, ...]:
        return (self.api_key,) if self.api_key else ()

    def complete(self, request: Any) -> ChatResponse:
        self.requests.append(request)
        if "report_capability" in _tool_names(request):
            return ChatResponse(
                text="",
                tool_calls=[ToolCall(name="report_capability", arguments={"ok": True})],
            )
        return ChatResponse(
            text="",
            tool_calls=[
                ToolCall(
                    name=PROPOSE_DELTAS_TOOL_NAME,
                    arguments={
                        "narration": "The yard holds its breath while gulls wheel over the gate.",
                        "npc_dialogue": [],
                        "deltas": [],
                    },
                )
            ],
        )

    @property
    def turn_prompts(self) -> list[Any]:
        """The narration calls (the canary probe rides separately)."""
        return [
            request for request in self.requests
            if PROPOSE_DELTAS_TOOL_NAME in _tool_names(request)
        ]


class _Recorder:
    """``build_adapter`` replacement: one recording adapter per (re)build."""

    def __init__(self) -> None:
        self.adapters: list[_RecordingAdapter] = []

    def build(self, cfg: Any, *, api_key: str | None = None) -> _RecordingAdapter:
        adapter = _RecordingAdapter(cfg, api_key, self)
        self.adapters.append(adapter)
        return adapter

    @property
    def prompts(self) -> list[Any]:
        return [prompt for adapter in self.adapters for prompt in adapter.turn_prompts]


@pytest.fixture()
def recorder(monkeypatch: pytest.MonkeyPatch) -> _Recorder:
    recorder = _Recorder()
    monkeypatch.setattr(bridge, "build_adapter", recorder.build)
    return recorder


def _register(client: TestClient, email: str) -> dict:
    r = client.post(
        "/auth/register",
        json={"email": email, "password": "password123", "display_name": email.split("@")[0]},
    )
    assert r.status_code == 201, r.text
    return {"Authorization": f"Bearer {r.json()['token']['access_token']}"}


def _campaign(client: TestClient, headers: dict, name: str = "Boundaries") -> str:
    r = client.post("/engine/campaigns", headers=headers, json={"name": name})
    assert r.status_code == 201, r.text
    campaign_id = r.json()["id"]
    r = client.put(
        "/engine/connection", headers=headers,
        json={"provider": "openai", "model": "gpt-4o-mini",
              "base_url": "https://api.example.test/v1"},
    )
    assert r.status_code == 200, r.text
    return campaign_id


def _turn(client: TestClient, headers: dict, campaign_id: str, *,
          prefs: dict | str | None = None, text: str = "I take in the yard") -> Any:
    sent = {**headers, "X-Provider-Key": RUNTIME_KEY}  # live turns need a runtime key
    if prefs is not None:
        sent["X-Content-Prefs"] = prefs if isinstance(prefs, str) else json.dumps(prefs)
    return client.post(
        f"/engine/campaigns/{campaign_id}/turns", headers=sent, json={"text": text}
    )


def _system_prompt(recorder: _Recorder, index: int = -1) -> str:
    prompt = recorder.prompts[index]
    messages = prompt.messages
    assert messages[0].role == "system"
    return messages[0].content


def _db_text() -> str:
    db = TestingSession()
    try:
        chunks: list[str] = []
        for table in Base.metadata.sorted_tables:
            for row in db.execute(table.select()).fetchall():
                chunks.extend(str(value) for value in row)
        return "\n".join(chunks)
    finally:
        db.close()


def _data_dir_text(data_dir: Path) -> str:
    if not data_dir.exists():
        return ""
    return "\n".join(
        path.read_bytes().decode("utf-8", "ignore")
        for path in sorted(data_dir.rglob("*"))
        if path.is_file()
    )


# --------------------------------------------------------------------------- #
# The parser: the legacy ContentPrefs contract, nothing parallel
# --------------------------------------------------------------------------- #


@pytest.mark.parametrize("raw", [None, "", "   "])
def test_an_absent_header_is_the_defaults_and_readable(raw: str | None) -> None:
    parsed, readable = engine_router.content_prefs(raw)
    assert parsed == ContentPrefs() and readable is True


def test_the_parser_is_the_legacy_model_with_its_aliases() -> None:
    parsed, readable = engine_router.content_prefs(
        json.dumps({"violence": "low", "horror": "clean", "romance": "mild",
                    "language": "off", "nsfw": True, "unknown_key": "ignored"})
    )
    assert readable is True
    # The same aliases the legacy play surface applies (low|clean|mild -> reduced),
    # unknown keys ignored rather than fatal.
    assert parsed == ContentPrefs(violence="reduced", horror="reduced", romance="reduced",
                                  language="off", nsfw=True)
    assert parsed.describe_for_prompt().startswith("Content boundaries — uncensored")

    # Lax boolean coercion is the legacy model's own behaviour ("yes" -> True),
    # not a new engine-path tolerance: identical acceptance on both surfaces.
    coerced, readable = engine_router.content_prefs(json.dumps({"nsfw": "yes"}))
    assert readable is True and coerced.nsfw is True


@pytest.mark.parametrize("raw", [
    "{not json", "[]", '"violence"', "12", "null",
    '{"violence": "none"}', '{"nsfw": "probably"}', '{"horror": 3}',
])
def test_unreadable_payloads_answer_the_defaults_and_say_so(raw: str) -> None:
    parsed, readable = engine_router.content_prefs(raw)
    assert parsed == ContentPrefs()  # safe: nsfw off, standard caps
    assert readable is False


# --------------------------------------------------------------------------- #
# The prompt: the directive the model actually receives
# --------------------------------------------------------------------------- #


def test_the_default_boundaries_ride_every_prompt_without_a_header(
    client: TestClient, recorder: _Recorder
) -> None:
    headers = _register(client, "prefs-default@example.com")
    campaign_id = _campaign(client, headers)

    r = _turn(client, headers, campaign_id)
    assert r.status_code == 200, r.text

    directive = ContentPrefs().describe_for_prompt()
    assert _system_prompt(recorder) == f"{RULESET}\n\n{directive}"
    # The default directive is the per-axis caps plus the flat sex exclusion.
    assert "violence: standard fantasy violence" in _system_prompt(recorder)
    assert "horror: standard fantasy horror" in _system_prompt(recorder)
    assert "romance: standard romantic themes" in _system_prompt(recorder)
    assert "language: occasional strong language permitted" in _system_prompt(recorder)
    assert "no explicit sexual content" in _system_prompt(recorder)
    # No header, no note: the defaults are simply the boundaries.
    assert bridge.CONTENT_DEFAULTS_NOTE not in r.json()["system_lines"]
    assert r.json()["content"] == {
        "nsfw": False, "violence": "standard", "horror": "standard",
        "romance": "standard", "language": "standard",
    }


def test_player_boundaries_reach_the_prompt_verbatim(
    client: TestClient, recorder: _Recorder
) -> None:
    headers = _register(client, "prefs-custom@example.com")
    campaign_id = _campaign(client, headers)

    r = _turn(client, headers, campaign_id, prefs=CUSTOM)
    assert r.status_code == 200, r.text

    directive = ContentPrefs(**CUSTOM).describe_for_prompt()
    assert directive in _system_prompt(recorder)
    assert _system_prompt(recorder) == f"{RULESET}\n\n{directive}"
    # Direction matters: the per-axis caps, not the uncensored master text.
    assert "no graphic violence" in _system_prompt(recorder)
    assert "uncensored mode" not in _system_prompt(recorder)


def test_the_nsfw_switch_reaches_the_prompt_with_the_hard_exclusion(
    client: TestClient, recorder: _Recorder
) -> None:
    headers = _register(client, "prefs-nsfw@example.com")
    campaign_id = _campaign(client, headers)

    r = _turn(client, headers, campaign_id, prefs={**CUSTOM, "nsfw": True})
    assert r.status_code == 200, r.text

    directive = ContentPrefs(**{**CUSTOM, "nsfw": True}).describe_for_prompt()
    assert _system_prompt(recorder) == f"{RULESET}\n\n{directive}"
    assert "uncensored mode" in _system_prompt(recorder)
    assert "never sexual content involving minors" in _system_prompt(recorder)
    assert r.json()["content"]["nsfw"] is True


def test_boundaries_are_re_read_per_turn(client: TestClient, recorder: _Recorder) -> None:
    headers = _register(client, "prefs-per-turn@example.com")
    campaign_id = _campaign(client, headers)

    assert _turn(client, headers, campaign_id, text="I take in the yard").status_code == 200
    assert _turn(client, headers, campaign_id, prefs={**CUSTOM, "nsfw": True},
                 text="I look again").status_code == 200
    assert _turn(client, headers, campaign_id, prefs=CUSTOM,
                 text="I turn away").status_code == 200

    assert len(recorder.prompts) == 3
    first, second, third = (_system_prompt(recorder, index) for index in range(3))
    # No header -> defaults; each flip is applied to the next turn only.
    assert first == f"{RULESET}\n\n{ContentPrefs().describe_for_prompt()}"
    nsfw = ContentPrefs(**{**CUSTOM, "nsfw": True}).describe_for_prompt()
    assert second == f"{RULESET}\n\n{nsfw}"
    assert third == f"{RULESET}\n\n{ContentPrefs(**CUSTOM).describe_for_prompt()}"
    assert "uncensored mode" not in third  # switching back re-bounds the prose


def test_unreadable_input_keeps_the_turn_alive_on_the_defaults(
    client: TestClient, recorder: _Recorder
) -> None:
    headers = _register(client, "prefs-broken@example.com")
    campaign_id = _campaign(client, headers)

    r = _turn(client, headers, campaign_id, prefs="{violence: off")  # malformed JSON
    assert r.status_code == 200, r.text
    payload = r.json()

    # Defaults applied (never a dropped boundary) and the fallback is visible.
    assert payload["content"] == {
        "nsfw": False, "violence": "standard", "horror": "standard",
        "romance": "standard", "language": "standard",
    }
    assert bridge.CONTENT_DEFAULTS_NOTE in payload["system_lines"]
    assert payload["narration"].strip()
    assert _system_prompt(recorder) == f"{RULESET}\n\n{ContentPrefs().describe_for_prompt()}"

    # Readable input never carries the note.
    ok = _turn(client, headers, campaign_id, prefs=CUSTOM, text="I look again")
    assert bridge.CONTENT_DEFAULTS_NOTE not in ok.json()["system_lines"]


@pytest.mark.parametrize(("slug", "prefs"), [
    ("level", json.dumps({"violence": "none"})),      # out-of-vocabulary level
    ("switch", json.dumps({"nsfw": "probably"})),      # wrong type on the master switch
    ("list", "[1, 2]"),                                # valid JSON, not an object
    ("scalar", '"standard"'),
])
def test_every_unreadable_shape_falls_back_with_the_note(
    client: TestClient, recorder: _Recorder, slug: str, prefs: str
) -> None:
    headers = _register(client, f"prefs-shape-{slug}@example.com")
    campaign_id = _campaign(client, headers)

    r = _turn(client, headers, campaign_id, prefs=prefs)
    assert r.status_code == 200, r.text
    assert bridge.CONTENT_DEFAULTS_NOTE in r.json()["system_lines"]
    assert r.json()["content"]["nsfw"] is False


def test_the_applied_prefs_are_echoed_not_the_header(
    client: TestClient, recorder: _Recorder
) -> None:
    headers = _register(client, "prefs-echo@example.com")
    campaign_id = _campaign(client, headers)

    payload = _turn(client, headers, campaign_id,
                    prefs={**CUSTOM, "canary": HEADER_CANARY}).json()
    # Applied values, canonical spelling: aliases resolve, junk keys vanish.
    assert payload["content"] == {
        "nsfw": False, "violence": "off", "horror": "reduced",
        "romance": "reduced", "language": "reduced",
    }
    assert HEADER_CANARY not in json.dumps(payload)

    aliased = _turn(client, headers, campaign_id,
                    prefs={"violence": "mild", "canary": HEADER_CANARY},
                    text="I look again").json()
    assert aliased["content"]["violence"] == "reduced"  # the applied value, not "mild"
    assert aliased["content"]["horror"] == "standard"   # untouched axes: defaults


def test_an_unconnected_turn_is_refused_before_any_content_work(
    client: TestClient, recorder: _Recorder
) -> None:
    """P11: with no model connected the turn is refused and nothing runs.

    The engine is never reached, so no prompt is assembled, nothing is echoed,
    and the boundaries payload cannot leak through a refusal.
    """
    headers = _register(client, "prefs-unconnected@example.com")
    r = client.post("/engine/campaigns", headers=headers, json={"name": "Unconnected"})
    campaign_id = r.json()["id"]  # no /connection PUT -> not connected

    refused = _turn(client, headers, campaign_id, prefs=CUSTOM)
    assert refused.status_code == 400
    assert refused.json() == {"detail": "connect_your_ai"}
    assert recorder.prompts == []  # no model call, so no prompt carried boundaries

    # Browsing stays usable: the campaign exists, untouched by the refusal.
    state = client.get(f"/engine/campaigns/{campaign_id}/state", headers=headers)
    assert state.status_code == 200
    assert state.json()["turn"] == 0


# --------------------------------------------------------------------------- #
# Hygiene: the header payload is never logged, never persisted
# --------------------------------------------------------------------------- #


def test_the_header_payload_is_never_logged(
    client: TestClient, recorder: _Recorder, caplog: pytest.LogCaptureFixture
) -> None:
    headers = _register(client, "prefs-log@example.com")
    campaign_id = _campaign(client, headers)

    with caplog.at_level(logging.DEBUG):
        r = _turn(client, headers, campaign_id,
                  prefs={**CUSTOM, "canary": HEADER_CANARY})
    assert r.status_code == 200, r.text

    assert HEADER_CANARY not in caplog.text
    assert "X-Content-Prefs" not in caplog.text


def test_the_policy_text_is_prompt_only(
    client: TestClient, recorder: _Recorder, engine_data_dir: Path
) -> None:
    headers = _register(client, "prefs-persist@example.com")
    campaign_id = _campaign(client, headers)
    assert _turn(client, headers, campaign_id, prefs=CUSTOM).status_code == 200

    directive = ContentPrefs(**CUSTOM).describe_for_prompt()
    assert directive in _system_prompt(recorder)  # the model saw it...
    # ...and that is the only place it lives: not in the app DB, not in the
    # engine's SQLite (turns embed narration, never the prompt text).
    assert directive not in _db_text()
    assert directive not in _data_dir_text(engine_data_dir)
    assert HEADER_CANARY not in _db_text() and HEADER_CANARY not in _data_dir_text(engine_data_dir)
