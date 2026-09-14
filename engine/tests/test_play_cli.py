"""``python -m engine`` CLI — play / state / evals, exit codes, key hygiene (R7).

The CLI is the human surface: it must play keylessly offline, resume campaigns,
refuse a bad invocation with a fixable message (exit 2) and never echo a secret.
"""
from __future__ import annotations

import json

import pytest

from engine.cli import main
from engine.store import Store
from tests.test_providers_server import FakeProviderServer, openai_payload

ENVELOPE = {
    "narration": "The clerk waves you through without looking up.",
    "npc_dialogue": [],
    "deltas": [],
}


@pytest.fixture()
def stdin_lines(monkeypatch):
    def feed(text: str) -> None:
        import io
        import sys
        monkeypatch.setattr(sys, "stdin", io.StringIO(text))
    return feed


def test_play_runs_offline_and_saves(capsys, tmp_path, stdin_lines) -> None:
    stdin_lines("I search the yard for boot prints\n/quit\n")
    code = main(["play", "--db", str(tmp_path / "c.db")])
    out = capsys.readouterr().out

    assert code == 0
    assert "stub narrator" in out
    assert "[turn 1]" in out
    assert "Marla Quist" in out          # the scene reaches the narration
    assert "(campaign saved)" in out
    assert tmp_path.joinpath("c.db").exists()


def test_play_handles_eof_and_blank_lines(capsys, tmp_path, stdin_lines) -> None:
    stdin_lines("\n\nI wait\n")
    code = main(["play", "--db", str(tmp_path / "c.db")])
    out = capsys.readouterr().out
    assert code == 0
    assert "end of input — campaign saved" in out
    assert out.count("[turn 1]") == 1


def test_play_resumes_the_campaign_clock(capsys, tmp_path, stdin_lines) -> None:
    stdin_lines("I wait\n/quit\n")
    assert main(["play", "--db", str(tmp_path / "c.db")]) == 0
    capsys.readouterr()

    stdin_lines("I wait\n/quit\n")
    assert main(["play", "--db", str(tmp_path / "c.db")]) == 0
    out = capsys.readouterr().out
    assert "turn: 2" in out
    assert "[turn 2]" in out


def test_play_state_and_help_are_in_loop_commands(capsys, tmp_path, stdin_lines) -> None:
    stdin_lines("/help\n/state\n/quit\n")
    assert main(["play", "--db", str(tmp_path / "c.db")]) == 0
    out = capsys.readouterr().out

    assert "/state  print the JSON state snapshot" in out
    payload = out[out.index("{"):out.rindex("}") + 1]
    snapshot = json.loads(payload)
    assert snapshot["location"]["name"] == "The Salt Gate yard"
    assert snapshot["turn"] == 0  # nothing played yet


def test_play_accepts_a_world_fixture(capsys, tmp_path, stdin_lines) -> None:
    world = {
        "locations": [{"name": "A Windy Hut", "description_static": "Planks.",
                       "connections": [], "flags": {}}],
        "characters": [{"location_id": "a-windy-hut", "stats": {"hp": 4},
                        "inventory": [], "status_effects": [], "known_facts": [],
                        "journal": []}],
    }
    fixture = tmp_path / "world.json"
    fixture.write_text(json.dumps(world))
    stdin_lines("I wait\n/quit\n")
    assert main(["play", "--db", str(tmp_path / "w.db"), "--world", str(fixture)]) == 0
    out = capsys.readouterr().out
    assert "A Windy Hut" in out


def test_play_reports_a_missing_world_fixture(capsys, tmp_path, stdin_lines) -> None:
    stdin_lines("I wait\n")
    code = main(["play", "--db", str(tmp_path / "c.db"),
                 "--world", str(tmp_path / "nope.json")])
    assert code == 2
    assert "world fixture not found" in capsys.readouterr().err


def test_state_subcommand_prints_json(capsys, tmp_path, stdin_lines) -> None:
    stdin_lines("I wait\n/quit\n")
    main(["play", "--db", str(tmp_path / "c.db")])
    capsys.readouterr()

    assert main(["state", "--db", str(tmp_path / "c.db")]) == 0
    snapshot = json.loads(capsys.readouterr().out)
    assert snapshot["turn"] == 1
    assert snapshot["next_turn"] == 2
    assert snapshot["hp"] == 11


def test_state_subcommand_errors_without_a_campaign(capsys, tmp_path) -> None:
    assert main(["state", "--db", str(tmp_path / "missing.db")]) == 1
    assert "no campaign" in capsys.readouterr().err

    empty = Store(tmp_path / "empty.db")
    empty.close()
    assert main(["state", "--db", str(tmp_path / "empty.db")]) == 1
    assert "holds no campaign" in capsys.readouterr().err


# --------------------------------------------------------------------------- #
# invocation errors
# --------------------------------------------------------------------------- #

def test_usage_errors_exit_2(capsys, tmp_path, stdin_lines) -> None:
    stdin_lines("")
    cases = [
        (["play", "--db", str(tmp_path / "c.db"), "--provider", "openai"],
         "--provider needs --model"),
        (["play", "--db", str(tmp_path / "c.db"), "--model", "gpt-test"],
         "need --provider"),
        (["play", "--db", str(tmp_path / "c.db"), "--provider", "sorcery",
          "--model", "x"], "unknown provider"),
        (["play", "--db", str(tmp_path / "c.db"), "--provider", "local",
          "--model", "x"], "needs --base-url"),
        (["play", "--db", str(tmp_path / "c.db"), "--provider", "openai",
          "--model", "x", "--api-key-env", "LB_TEST_KEY_UNSET"], "unset or empty"),
    ]
    for argv, needle in cases:
        assert main(argv) == 2, argv
        assert needle in capsys.readouterr().err


def test_unset_key_message_never_echoes_a_value(capsys, tmp_path, stdin_lines,
                                                monkeypatch) -> None:
    monkeypatch.setenv("LB_TEST_KEY_UNSET", "")
    stdin_lines("")
    assert main(["play", "--db", str(tmp_path / "c.db"), "--provider", "openai",
                 "--model", "x", "--api-key-env", "LB_TEST_KEY_UNSET"]) == 2
    captured = capsys.readouterr()
    assert "LB_TEST_KEY_UNSET" in captured.err
    assert "sk-" not in captured.out + captured.err


# --------------------------------------------------------------------------- #
# live path over the fake wire
# --------------------------------------------------------------------------- #

def test_play_over_the_live_wire(capsys, tmp_path, stdin_lines, monkeypatch) -> None:
    monkeypatch.setenv("LB_TEST_KEY", "test-key-not-a-real-credential")
    server = FakeProviderServer()
    server.enqueue(200, openai_payload(text="No tool here."))              # probe
    server.enqueue(200, openai_payload(text=json.dumps(ENVELOPE)))         # narration
    server.start()
    try:
        stdin_lines("I wait\n/quit\n")
        code = main([
            "play", "--db", str(tmp_path / "live.db"),
            "--provider", "openai", "--model", "gpt-test",
            "--base-url", server.url, "--api-key-env", "LB_TEST_KEY",
        ])
        out = capsys.readouterr().out
    finally:
        server.stop()

    assert code == 0
    assert ENVELOPE["narration"] in out
    assert "[turn 1]" in out
    # the model key travelled in the header, the run recorded who narrated
    headers = {key.lower(): value for key, value in server.requests[-1].headers.items()}
    assert headers.get("authorization") == "Bearer test-key-not-a-real-credential"
    assert "test-key-not-a-real-credential" not in out

    store = Store(tmp_path / "live.db")
    try:
        telemetry = store.find_one("telemetry", {"turn": 1})
        assert telemetry["provider"] == "openai"
        assert telemetry["model"] == "gpt-test"
        assert json.loads(telemetry["notes"])["intent"]["kind"] == "exploration"
    finally:
        store.close()


# --------------------------------------------------------------------------- #
# evals delegation
# --------------------------------------------------------------------------- #

def test_evals_list_delegates_to_the_harness(capsys) -> None:
    pytest.importorskip("evals.harness")
    assert main(["evals", "--list"]) == 0
    out = capsys.readouterr().out
    assert "dead-npc-lock" in out
    assert "contradiction-bait" in out
