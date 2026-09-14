"""E2E: the context budget forces public drop decisions, and telemetry records them.

A deliberately small window (1200 tokens vs the 8000 default) with a fat world:
25 chronicle rows, five session summaries, four open leads and a long pinned
fact. The assembler must fit the prompt by dropping whole sections in the
declared priority order, charge the groups their shares of the window, and file
a telemetry row per turn that records the same allocation it actually used.

This is the only scenario that needs a *live* narrator: telemetry's
provider/model columns are filled from the narrator's adapter identity, so the
stub cannot exercise them.
"""


def _fat_world() -> dict:
    rows = {
        "world": [{"seed": "e2e-telemetry-budget", "day": 6, "hour": 22,
                   "active_scene_id": "yard"}],
        "locations": [
            {"name": "The Salt Gate yard", "description_static": "Packed earth and old rope.",
             "flags": {"slug": "yard"}},
        ],
        "characters": [
            {"name": "Rell", "ref": "player", "location_id": "yard",
             "stats": {"hp": 11, "max_hp": 11, "currency": 6}},
        ],
        "npcs": [
            {"name": "Marla Quist", "ref": "marla", "location_id": "yard",
             "alive": True, "disposition_base": 12.0},
            {"name": "Hob Fen", "ref": "hob", "location_id": "yard", "alive": True},
        ],
        "world_facts": [
            {"statement": "The Salt Gate ledger lists every crate that ever left the yard, " * 8,
             "pinned": True, "established_turn": 1, "source": "narrator"},
            {"statement": "Marla Quist is owed two days of gate wages.", "pinned": True,
             "established_turn": 2, "source": "narrator"},
        ],
        "chronicle": [
            {"turn_id": turn_id, "actor": "narrator",
             "action_summary": f"Turn {turn_id}: the yard woke, the rope moved, the gate stayed shut.",
             "mechanical_result": "no deltas",
             "consequence_oneliner": f"consequence {turn_id}: nothing changed yet",
             "verbatim_text": f"The yard accepted turn {turn_id} without complaint. " * 4}
            for turn_id in range(1, 26)
        ],
        "saga_levels": [
            {"level": "session", "scope_id": f"session-{level}", "created_turn": level,
             "text": "The yard, the rope, and the watch. " * (12 * level)}
            for level in range(1, 6)
        ],
        "leads": [
            {"title": f"The {index}th lead about the salt run and the gate"}
            for index in range(1, 5)
        ],
        "npc_memory": [
            {"npc_id": "npc:1", "turn_established": turn, "type": "grievance",
             "sentiment": -0.8,
             "statement": f"Rell left the tally work undone on day {turn}, and Marla noticed."}
            for turn in (3, 9, 14, 21)
        ],
    }
    return rows


E2E = {
    "name": "e2e-telemetry-budget",
    "description": "a small window drops sections in priority order and telemetry records the same numbers",
    "options": {"seed": 23, "context_window": 1200},
    "world": _fat_world(),
    "sessions": [
        {
            "name": "native",
            "profile": "native",
            "model": "budget-native",
            "turns": [
                {
                    "turn": 1,
                    "input": "I read the ledger at the gatehouse bench.",
                    "replies": [
                        {"envelope": {
                            "narration": "Dust settles on the crate rows while you turn the pages.",
                            "npc_dialogue": [],
                            "deltas": [
                                {"kind": "mood", "target": "npc:1",
                                 "data": {"valence_delta": 0.05, "arousal_delta": 0.0},
                                 "reason": "the quiet of the bench"},
                            ],
                        }},
                    ],
                    "expect": {"calls": 1, "deltas": {"accepted": 1}},
                    "checks": [
                        {"expr": "record['prompt']['budget']['window'] == 1200 and "
                                 "record['prompt']['budget']['used'] <= record['prompt']['budget']['total']",
                         "msg": "the prompt fits the requested window"},
                        {"expr": "record['prompt']['budget']['output_reserve'] "
                                 "+ record['prompt']['budget']['total'] == 1200",
                         "msg": "the output reserve comes out of the window first"},
                        {"expr": "sum(record['prompt']['sections'].values()) "
                                 "== record['prompt']['budget']['used']",
                         "msg": "the section sizes account for every prompt token"},
                        {"expr": "sum(record['prompt']['allocations'].values()) "
                                 "== record['prompt']['budget']['total']",
                         "msg": "the four budget groups split the prompt budget exactly"},
                        {"expr": "len(record['prompt']['dropped']) > 0",
                         "msg": "a 1200-token window with a fat world must drop content"},
                        {"expr": "all(entry['dropped_tokens'] > 0 "
                                 "and entry['reason'] in ('over_budget', 'system_ceiling') "
                                 "and entry['kept_tokens'] >= 0 "
                                 "for entry in record['prompt']['dropped'])",
                         "msg": "every drop is recorded with its section, size and reason"},
                        {"expr": "'saga' in [entry['section'] for entry in record['prompt']['dropped']]",
                         "msg": "the saga summaries are the first content to go"},
                        {"expr": "record['prompt']['sections'].get('mechanics', 0) > 0 "
                                 "and 'mechanics' not in [entry['section'] "
                                 "for entry in record['prompt']['dropped']]",
                         "msg": "mechanics is non-negotiable and is kept"},
                        {"expr": "[entry['section'] for entry in record['prompt']['dropped']].index('saga') "
                                 "< [entry['section'] for entry in record['prompt']['dropped']].index('chronicle')",
                         "msg": "drops follow the declared priority order (saga before chronicle)"},
                        {"expr": "record['consistency']['problems'] == 0",
                         "msg": "the ledger turn is not a contradiction turn"},
                    ],
                },
                {
                    "turn": 2,
                    "input": "I ask Hob Fen what the ledger is missing.",
                    "replies": [
                        {"envelope": {
                            "narration": "Hob runs a thumb down the column and stops at the gap.",
                            "npc_dialogue": [],
                            "deltas": [],
                        }},
                    ],
                    "expect": {"calls": 1},
                    "checks": [
                        {"expr": "len(record['prompt']['dropped']) > 0",
                         "msg": "the second turn is budgeted like the first"},
                        {"expr": "record['prompt']['budget']['used'] <= record['prompt']['budget']['total']",
                         "msg": "the second turn also fits"},
                    ],
                },
            ],
            "expect_stats": {"native_tools": True, "calls": 2, "regenerations": 0,
                             "parse_failures": 0, "repairs": 0},
        },
    ],
    "checks": [
        {"expr": "all(turn['ok'] for turn in sessions['native']['turns'])",
         "msg": "every turn must satisfy its expectations"},
        {"expr": "len(telemetry_rows()) == 2",
         "msg": "one telemetry row per turn"},
        {"expr": "telemetry_rows()[0]['provider'] == 'fake-native' "
                 "and telemetry_rows()[0]['model'] == 'budget-native'",
         "msg": "telemetry names the provider and model that narrated"},
        {"expr": "telemetry_rows()[1]['turn'] == 2",
         "msg": "telemetry rows are keyed by turn"},
        {"expr": "telemetry_rows()[0]['budget_alloc'] "
                 "== sessions['native']['turns'][0]['prompt']['allocations']",
         "msg": "telemetry records the allocation the turn actually used"},
        {"expr": "telemetry_rows()[0]['notes']['provider'] == 'fake-native' "
                 "and telemetry_rows()[0]['notes']['model'] == 'budget-native' "
                 "and telemetry_rows()[0]['notes']['native_tools'] is True",
         "msg": "the telemetry notes carry the narrator identity"},
        {"expr": "telemetry_rows()[0]['completion_tokens'] == 0 "
                 "and telemetry_rows()[0]['prompt_tokens'] "
                 "== sessions['native']['turns'][0]['prompt']['budget']['used']",
         "msg": "prompt_tokens is the accounted prompt size"},
    ],
}
