"""E2E: one session, three transports — native tool calls, a flaky codec, a
tool-less provider — and the same world state at the end.

The model layer is the least deterministic part of a real run, so the only thing
worth asserting is that *transport cannot change the game*:

* ``native``: the narrator answers with ``propose_deltas`` tool calls (arguments
  as an object; one turn as a JSON *string*, one turn with fenced arguments).
* ``flaky``: a native-tools provider whose serializer slips — fenced arguments,
  a trailing comma in the JSON text, a fenced envelope in the text channel.
  Recoveries must be silent (the trailing comma is *repaired* and counted).
* ``degraded``: no native tools at all (text JSON only), and one turn where the
  reply is prose: that parse failure is retried once inside the model layer and
  the retry is the same envelope every other session committed.

The matrix then compares the three sessions row for row: state tables, per-turn
outcomes/reports/narrations/deltas, and the assembled prompts (sha1), which must
be identical because the transport lives below the pipeline.
"""

_TURN_ONE = {
    "narration": "You spend the morning at the tallow bench, and the yard keeps its rhythm.",
    "npc_dialogue": [],
    "deltas": [
        {"kind": "mood", "target": "npc:1",
         "data": {"valence_delta": -0.1, "arousal_delta": 0.1},
         "reason": "she worked the same corner all morning"},
    ],
}

_TURN_TWO = {
    "narration": "Marla watches you re-hang the gate latch, and her shoulders come down.",
    "npc_dialogue": [],
    "deltas": [
        {"kind": "relationship", "target": "npc:1",
         "data": {"category": "trust", "delta": 6, "decay_class": "slow"},
         "reason": "she saw you fix what you broke"},
    ],
}

_TURN_THREE = {
    "narration": "The lamplighter's boy takes your coin and runs the gate road ahead of you.",
    "npc_dialogue": [],
    "deltas": [
        {"kind": "currency", "target": "", "data": {"amount": -2},
         "reason": "the boy's fee"},
        {"kind": "fact", "target": "",
         "data": {"statement": "The lamplighter's boy runs the gate road at dusk.",
                  "source": "narrator"},
         "reason": "a face to remember"},
    ],
}

E2E = {
    "name": "e2e-model-matrix",
    "description": "three transports, one game: the same state and outputs whichever way the model answers",
    "options": {"seed": 11, "context_window": 2400},
    "world": {
        "world": [{"seed": "e2e-model-matrix", "day": 2, "hour": 18, "active_scene_id": "yard"}],
        "locations": [
            {"name": "The Salt Gate yard", "description_static": "Packed earth and a rope well.",
             "flags": {"slug": "yard"}},
        ],
        "characters": [
            {"name": "Rell", "ref": "player", "location_id": "yard",
             "stats": {"hp": 11, "max_hp": 11, "currency": 6}},
        ],
        "npcs": [
            {"name": "Marla Quist", "ref": "marla", "location_id": "yard",
             "alive": True, "disposition_base": 10.0},
        ],
        "world_facts": [
            {"statement": "The salt run leaves at dawn and does not wait.", "pinned": True,
             "established_turn": 1, "source": "narrator"},
        ],
        "leads": [
            {"title": "Find out who has been shorting the tally", "ref": "lead"},
        ],
    },
    "sessions": [
        {
            "name": "native",
            "profile": "native",
            "model": "matrix-native",
            "turns": [
                {"turn": 1, "input": "I work the tallow bench until the light goes long.",
                 "replies": [{"envelope": _TURN_ONE, "via": "auto"}],
                 "expect": {"calls": 1, "deltas": {"accepted": 1, "rejected": 0}}},
                {"turn": 2, "input": "I re-hang the gate latch properly this time.",
                 "replies": [{"envelope": _TURN_TWO, "via": "args-json"}],
                 "expect": {"calls": 1, "deltas": {"accepted": 1, "rejected": 0}}},
                {"turn": 3, "input": "I pay the lamplighter's boy to light the gate road.",
                 "replies": [{"envelope": _TURN_THREE, "via": "native-args"}],
                 "expect": {"calls": 1, "deltas": {"accepted": 2, "rejected": 0}}},
            ],
            "expect_stats": {"native_tools": True, "calls": 3, "requests": 3,
                             "regenerations": 0, "parse_failures": 0, "repairs": 0},
        },
        {
            "name": "flaky",
            "profile": "flaky",
            "model": "matrix-flaky",
            "turns": [
                {"turn": 1, "input": "I work the tallow bench until the light goes long.",
                 "replies": [{"envelope": _TURN_ONE, "via": "args-fenced"}],
                 "expect": {"calls": 1, "deltas": {"accepted": 1, "rejected": 0}}},
                {"turn": 2, "input": "I re-hang the gate latch properly this time.",
                 "replies": [{"envelope": _TURN_TWO, "via": "text-trailing-comma"}],
                 "expect": {"calls": 1, "deltas": {"accepted": 1, "rejected": 0}}},
                {"turn": 3, "input": "I pay the lamplighter's boy to light the gate road.",
                 "replies": [{"envelope": _TURN_THREE, "via": "text-fenced"}],
                 "expect": {"calls": 1, "deltas": {"accepted": 2, "rejected": 0}}},
            ],
            "expect_stats": {"native_tools": True, "calls": 3, "requests": 3,
                             "regenerations": 0, "parse_failures": 0, "repairs": 1},
        },
        {
            "name": "degraded",
            "profile": "degraded",
            "model": "matrix-degraded",
            "turns": [
                {"turn": 1, "input": "I work the tallow bench until the light goes long.",
                 "replies": [{"envelope": _TURN_ONE, "via": "auto"}],
                 "expect": {"calls": 1, "deltas": {"accepted": 1, "rejected": 0}}},
                {"turn": 2, "input": "I re-hang the gate latch properly this time.",
                 "replies": [{"envelope": _TURN_TWO, "via": "args-junk"}],
                 "expect": {"calls": 1, "deltas": {"accepted": 1, "rejected": 0}}},
                {"turn": 3, "input": "I pay the lamplighter's boy to light the gate road.",
                 "replies": [
                     {"text": "The yard is quiet and nothing follows you to the gate.",
                      "via": "prose"},
                     {"envelope": _TURN_THREE, "via": "auto"},
                 ],
                 # One narrate() call: the retry happens *inside* the model layer
                 # (bounded regenerate-on-parse-failure), so the pipeline sees one
                 # reply — but the adapter was asked twice.
                 "expect": {"calls": 1, "requests": 2,
                            "deltas": {"accepted": 2, "rejected": 0}}},
            ],
            "expect_stats": {"native_tools": False, "calls": 4, "requests": 4,
                             "regenerations": 1, "parse_failures": 1, "repairs": 0},
        },
    ],
    "matrix": {"sessions": ["native", "flaky", "degraded"], "compare_prompts": True},
    "checks": [
        {"expr": "all(turn['ok'] for session in sessions.values() for turn in session['turns'])",
         "msg": "every session must satisfy its turn expectations"},
        {"expr": "matrix['equal'] is True and matrix['diffs'] == []",
         "msg": "the transports must reach the same state and the same outputs"},
        {"expr": "set(matrix['sessions']) == {'native', 'flaky', 'degraded'}",
         "msg": "all three transports were compared"},
        {"expr": "profiles['native']['native_tools'] is True and "
                 "profiles['degraded']['native_tools'] is False",
         "msg": "the matrix really spans native-tools and tool-less providers"},
        {"expr": "sessions['native']['turns'][2]['narration'] == "
                 "sessions['degraded']['turns'][2]['narration'] == "
                 "sessions['flaky']['turns'][2]['narration']",
         "msg": "the prose-then-JSON retry shipped the same envelope"},
    ],
}
