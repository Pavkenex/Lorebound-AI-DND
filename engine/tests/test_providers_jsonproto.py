"""JSON-in-text protocol + Pass B codec: schema, parsing, regeneration loop."""
from __future__ import annotations

import json

import pytest
from test_providers_server import FakeProviderServer, openai_payload

from engine.models import (
    DELTA_KINDS,
    DELTA_REQUIRED,
    ChatMessage,
    ChatRequest,
    Delta,
    ProposalSet,
    ProviderConfig,
    ToolCall,
)
from engine.providers.jsonproto import (
    PROPOSE_DELTAS_TOOL_NAME,
    build_json_protocol_prompt,
    delta_tool_schema,
    parse_tolerant,
    parse_with_repair,
    proposals_from_payload,
    proposals_from_tool_calls,
    regenerate_note,
    should_regenerate,
)
from engine.providers.registry import build_adapter

ENVELOPE: dict = {
    "narration": "You bribe the guard and he looks away.",
    "npc_dialogue": [{"npc_id": "npc:2", "name": "Marla", "text": "Never saw you."}],
    "deltas": [
        {
            "kind": "relationship",
            "target": "npc:2",
            "data": {"category": "trust", "delta": -5},
            "reason": "He took a bribe.",
        },
        {"kind": "currency", "target": "", "data": {"amount": -3}},
    ],
}


# --------------------------------------------------------------------------- #
# delta_tool_schema — the canonical Pass B envelope
# --------------------------------------------------------------------------- #

def test_delta_tool_schema_carries_the_envelope() -> None:
    schema = delta_tool_schema()
    assert isinstance(schema, list) and len(schema) == 1
    tool = schema[0]
    assert tool["type"] == "function"
    function = tool["function"]
    assert function["name"] == PROPOSE_DELTAS_TOOL_NAME
    parameters = function["parameters"]
    assert set(parameters["properties"]) == {"narration", "npc_dialogue", "deltas"}
    assert parameters["required"] == ["narration", "deltas"]
    assert parameters["properties"]["narration"]["type"] == "string"
    dialogue = parameters["properties"]["npc_dialogue"]["items"]
    assert dialogue["required"] == ["npc_id", "text"]
    delta_items = parameters["properties"]["deltas"]["items"]
    assert delta_items["properties"]["kind"]["enum"] == sorted(DELTA_KINDS)
    assert delta_items["required"] == ["kind", "data"]


def test_delta_tool_schema_spells_out_every_kinds_payload() -> None:
    data_hint = delta_tool_schema()[0]["function"]["parameters"]["properties"]["deltas"][
        "items"
    ]["properties"]["data"]["description"]
    for kind, keys in DELTA_REQUIRED.items():
        assert kind in data_hint, kind
        for key in sorted(keys):
            assert key in data_hint, (kind, key)


def test_delta_tool_schema_is_deterministic() -> None:
    assert delta_tool_schema() == delta_tool_schema()


# --------------------------------------------------------------------------- #
# proposals_from_tool_calls / proposals_from_payload
# --------------------------------------------------------------------------- #

def test_proposals_from_tool_calls_builds_deltas() -> None:
    calls = [ToolCall(id="call_1", name=PROPOSE_DELTAS_TOOL_NAME, arguments=ENVELOPE)]
    notes: list[str] = []
    proposals = proposals_from_tool_calls(calls, notes=notes)
    assert isinstance(proposals, ProposalSet)
    assert notes == []
    assert proposals.narration == ENVELOPE["narration"]
    assert proposals.npc_dialogue == ENVELOPE["npc_dialogue"]
    assert [delta.kind for delta in proposals.deltas] == ["relationship", "currency"]
    first = proposals.deltas[0]
    assert isinstance(first, Delta)
    assert (first.target, first.data, first.reason) == (
        "npc:2",
        {"category": "trust", "delta": -5},
        "He took a bribe.",
    )
    assert proposals.deltas[1].target == ""
    assert proposals.deltas[1].data == {"amount": -3}


def test_proposals_from_tool_calls_drops_unknown_kinds_with_a_note() -> None:
    payload = {
        "narration": "Something shifts.",
        "deltas": [
            {"kind": "mood", "target": "npc:1", "data": {"valence_delta": 1, "arousal_delta": 0}},
            {"kind": "weather", "target": "", "data": {"rain": True}},
            "not even an object",
        ],
    }
    notes: list[str] = []
    proposals = proposals_from_tool_calls(
        [ToolCall(id="c", name=PROPOSE_DELTAS_TOOL_NAME, arguments=payload)], notes=notes
    )
    assert [delta.kind for delta in proposals.deltas] == ["mood"]
    assert notes == ["dropped unknown delta kind 'weather'", "dropped non-object delta proposal: 'not even an object'"]


def test_proposals_from_tool_calls_accepts_string_arguments() -> None:
    notes: list[str] = []
    proposals = proposals_from_tool_calls(
        [
            ToolCall(
                id="c",
                name=PROPOSE_DELTAS_TOOL_NAME,
                arguments='```json\n' + json.dumps(ENVELOPE) + "\n```",  # type: ignore[arg-type]
            )
        ],
        notes=notes,
    )
    assert [delta.kind for delta in proposals.deltas] == ["relationship", "currency"]
    assert notes == []


def test_proposals_from_tool_calls_without_the_envelope_is_noted() -> None:
    notes: list[str] = []
    proposals = proposals_from_tool_calls(
        [ToolCall(id="c", name="some_other_tool", arguments={"x": 1})], narration="plain text", notes=notes
    )
    assert proposals.deltas == []
    assert proposals.narration == "plain text"
    assert notes == [f"no {PROPOSE_DELTAS_TOOL_NAME} tool call found"]


def test_proposals_from_payload_is_tolerant_of_shape() -> None:
    notes: list[str] = []
    proposals = proposals_from_payload(
        {"narration": "", "npc_dialogue": "not a list", "deltas": {"kind": "hp"}},
        narration="fallback narration",
        notes=notes,
    )
    assert proposals.narration == "fallback narration"
    assert proposals.npc_dialogue == []
    assert proposals.deltas == []
    assert notes == ["ignored deltas: not a list"]


def test_proposals_from_payload_normalizes_dialogue_and_bad_data() -> None:
    proposals = proposals_from_payload(
        {
            "narration": "ok",
            "npc_dialogue": [{"name": "Marla", "extra": 1}],
            "deltas": [{"kind": "fact", "target": None, "data": "broken"}],
        }
    )
    assert proposals.npc_dialogue == [{"npc_id": "", "name": "Marla", "text": "", "extra": 1}]
    delta = proposals.deltas[0]
    assert (delta.kind, delta.target, delta.data) == ("fact", "", {})


def test_proposals_from_payload_rejects_non_dicts() -> None:
    notes: list[str] = []
    proposals = proposals_from_payload("not a dict", notes=notes)  # type: ignore[arg-type]
    assert proposals.deltas == []
    assert notes == ["dropped non-object payload: 'not a dict'"]


# --------------------------------------------------------------------------- #
# build_json_protocol_prompt
# --------------------------------------------------------------------------- #

def test_build_json_protocol_prompt_appends_the_exact_envelope() -> None:
    prompt = build_json_protocol_prompt("You are the narrator.")
    assert prompt.startswith("You are the narrator.")
    assert '"narration"' in prompt and '"npc_dialogue"' in prompt and '"deltas"' in prompt
    assert "no markdown fences" in prompt.replace("No markdown fences", "no markdown fences")
    for kind in DELTA_KINDS:
        assert kind in prompt
    assert prompt.endswith("Any other text is discarded.")


def test_build_json_protocol_prompt_without_base_prompt() -> None:
    prompt = build_json_protocol_prompt("")
    assert prompt.startswith("OUTPUT CONTRACT")


# --------------------------------------------------------------------------- #
# parse_tolerant
# --------------------------------------------------------------------------- #

@pytest.mark.parametrize(
    ("text", "expected"),
    [
        ('{"a": 1}', {"a": 1}),
        ('  {"a": 1}  ', {"a": 1}),
        ('```json\n{"a": 1}\n```', {"a": 1}),
        ('```\n{"a": 1}\n```', {"a": 1}),
        ('Sure! Here is my reply:\n{"a": 1}', {"a": 1}),
        ('{"a": 1}\nHope that helps!', {"a": 1}),
        ('Here: {"a": {"b": [1, 2]}} — end', {"a": {"b": [1, 2]}}),
        ('{"s": "a \\" quote { b", "n": 1}', {"s": 'a " quote { b', "n": 1}),
        ('{"a": 1} then {"b": 2}', {"a": 1}),
        ("", None),
        ("   ", None),
        ("no json at all", None),
        ("{broken json", None),
        ('{"a": 1,}', None),
        ("[1, 2, 3]", None),
        ("null", None),
        ('```json\n{"a": } \n```', None),
    ],
)
def test_parse_tolerant_variants(text: str, expected: dict | None) -> None:
    assert parse_tolerant(text) == expected


def test_parse_tolerant_rejects_non_strings() -> None:
    assert parse_tolerant(None) is None  # type: ignore[arg-type]
    assert parse_tolerant(b'{"a": 1}') is None  # type: ignore[arg-type]


def test_parse_tolerant_extracts_the_envelope_from_a_chatty_reply() -> None:
    reply = "Certainly! Here is the JSON you asked for:\n\n```json\n" + json.dumps(ENVELOPE) + "\n```\n\nLet me know if you need anything else."
    payload = parse_tolerant(reply)
    assert payload is not None
    assert proposals_from_payload(payload).narration == ENVELOPE["narration"]


# --------------------------------------------------------------------------- #
# parse_with_repair — the "repair" step of strict parse -> repair -> regenerate
# --------------------------------------------------------------------------- #

@pytest.mark.parametrize(
    ("text", "expected", "note_contains"),
    [
        ('{"a": 1}', {"a": 1}, ""),
        ('```json\n{"a": 1}\n```', {"a": 1}, ""),
        ('preamble {"a": 1} trailing', {"a": 1}, ""),
        ('{"a": 1,}', {"a": 1}, "trailing comma"),
        ('here: {"a": [1, 2,],}', {"a": [1, 2]}, "trailing comma"),
        ('{"a": {"b": 1,},}', {"a": {"b": 1}}, "trailing comma"),
        ('{"a": "x", } and more prose', {"a": "x"}, "trailing comma"),
        ('{"s": "brace, } and \\" quote", }', {"s": 'brace, } and " quote'}, "trailing comma"),
        ("still chatting", None, "no valid JSON object"),
        ('{"a": }', None, "no valid JSON object"),
        ("{unclosed", None, "no valid JSON object"),
        ("", None, "empty"),
    ],
)
def test_parse_with_repair(text: str, expected: dict | None, note_contains: str) -> None:
    payload, note = parse_with_repair(text)
    assert payload == expected
    if note_contains:
        assert note_contains in note
    else:
        assert note == ""


def test_parse_tolerant_still_refuses_broken_json() -> None:
    # the pinned behavior: repair is a separate, explicit step
    assert parse_tolerant('{"a": 1,}') is None
    assert parse_with_repair('{"a": 1,}')[0] == {"a": 1}


# --------------------------------------------------------------------------- #
# Regeneration loop
# --------------------------------------------------------------------------- #

def test_should_regenerate_is_bounded() -> None:
    assert should_regenerate(None, attempts=1, max_attempts=2) is True
    assert should_regenerate(None, attempts=2, max_attempts=2) is False
    assert should_regenerate(None, attempts=1, max_attempts=1) is False
    assert should_regenerate(None, attempts=1, max_attempts=0) is False
    assert should_regenerate({"a": 1}, attempts=1, max_attempts=3) is False


def test_regenerate_note_states_the_contract() -> None:
    note = regenerate_note("the reply was not valid JSON")
    assert "the reply was not valid JSON" in note
    assert "ONLY the JSON object" in note
    assert '"narration"' in note and '"deltas"' in note
    assert regenerate_note("") == regenerate_note("the previous reply was not valid JSON")


def _local_adapter(server: FakeProviderServer, model: str = "tiny-local"):
    cfg = ProviderConfig(
        name="local",
        model=model,
        base_url=f"{server.url}/v1",
        api_mode="openai",
        timeout_s=5.0,
        max_retries=0,
    )
    return cfg, build_adapter(cfg, api_key=None)


def _degraded_loop(adapter, messages: list[ChatMessage], *, max_attempts: int):
    """The bounded regenerate-on-parse-failure loop a caller runs (spec §7)."""
    notes: list[str] = []
    last_payload = None
    for attempt in range(1, max_attempts + 1):
        response = adapter.complete(
            ChatRequest(model="tiny-local", messages=messages, max_tokens=400)
        )
        last_payload = parse_tolerant(response.text)
        if last_payload is not None:
            return proposals_from_payload(last_payload, notes=notes), notes
        if not should_regenerate(last_payload, attempts=attempt, max_attempts=max_attempts):
            return None, notes
        messages.append(
            ChatMessage(
                role="user",
                content=regenerate_note("the reply was not valid JSON"),
            )
        )
    return None, notes


def test_regeneration_loop_recovers_from_a_bad_reply() -> None:
    with FakeProviderServer() as server:
        server.enqueue(200, openai_payload(text="Sure! Here is my reply:"))
        server.enqueue(200, openai_payload(text="```json\n" + json.dumps(ENVELOPE) + "\n```"))
        _, adapter = _local_adapter(server)
        proposals, notes = _degraded_loop(
            adapter, [ChatMessage(role="user", content="I bribe the guard.")], max_attempts=2
        )
        assert len(server.requests) == 2
        retry_message = server.requests[1].body["messages"][-1]
    assert proposals is not None
    assert proposals.narration == ENVELOPE["narration"]
    assert [delta.kind for delta in proposals.deltas] == ["relationship", "currency"]
    assert notes == []
    assert retry_message["role"] == "user"
    assert "ONLY the JSON object" in retry_message["content"]


def test_regeneration_loop_is_bounded() -> None:
    with FakeProviderServer() as server:
        server.enqueue(200, openai_payload(text="I would rather chat."))
        server.enqueue(200, openai_payload(text="Still just chatting."))
        server.enqueue(200, openai_payload(text="Never JSON."))
        _, adapter = _local_adapter(server)
        proposals, notes = _degraded_loop(
            adapter, [ChatMessage(role="user", content="go")], max_attempts=2
        )
        assert len(server.requests) == 2  # never a third call
    assert proposals is None
    assert notes == []


# --------------------------------------------------------------------------- #
# End-to-end: fake local provider -> tool call -> Delta objects
# --------------------------------------------------------------------------- #

def test_local_provider_tool_call_round_trip_to_deltas() -> None:
    envelope = dict(ENVELOPE)
    envelope["deltas"] = [*ENVELOPE["deltas"], {"kind": "summon_dragon", "target": "", "data": {}}]
    with FakeProviderServer() as server:
        server.enqueue(
            200,
            openai_payload(
                text="You bribe the guard.",
                tool_calls=[("call_1", PROPOSE_DELTAS_TOOL_NAME, envelope)],
            ),
        )
        cfg, adapter = _local_adapter(server)
        response = adapter.complete(
            ChatRequest(
                model=cfg.model,
                messages=[ChatMessage(role="user", content="I bribe the guard.")],
                tools=delta_tool_schema(),
                max_tokens=400,
            )
        )
        sent = server.last_request()
        assert sent.body["tools"][0]["function"]["name"] == PROPOSE_DELTAS_TOOL_NAME
        assert sent.body["model"] == "tiny-local"
        notes: list[str] = []
        proposals = proposals_from_tool_calls(response.tool_calls, notes=notes)
    assert proposals.narration == envelope["narration"]
    assert [delta.kind for delta in proposals.deltas] == ["relationship", "currency"]
    assert all(isinstance(delta, Delta) for delta in proposals.deltas)
    assert all(delta.kind in DELTA_KINDS for delta in proposals.deltas)
    assert notes == ["dropped unknown delta kind 'summon_dragon'"]
