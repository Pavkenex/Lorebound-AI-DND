"""E2E: an NPC dies and stays dead.

Turn 5 kills Marla Quist through the *game* (an ``update`` step by ref) and the
narrator pins the death (a ``fact`` delta with ``pinned: true``). Turns 12 and 27
are ordinary play. Turn 40 is the bait: the narrator has the corpse walk, talk and
propose mood/relationship/fact deltas sourced from her. Everything she "says"
must be caught by Pass D and regenerated, every delta touching her must be
refused by the validator, and her state must not move an inch:

* no dialogue line from her ships,
* no mood row, no relationship ledger row, no world fact sourced from her,
* ``alive`` stays 0,
* the *handling* is recorded (system lines + telemetry counters), not silent.

The control turn (42) proves the gates do not fire on ordinary prose.
"""

E2E = {
    "name": "e2e-death-permanence",
    "description": "a dead NPC stays dead: speech caught, deltas refused, state untouched",
    "options": {"seed": 17},
    "world": {
        "world": [{"seed": "e2e-death-permanence", "day": 3, "hour": 21,
                   "active_scene_id": "yard"}],
        "locations": [
            {"name": "The Salt Gate yard", "description_static": "Packed earth and old rope.",
             "flags": {"slug": "yard"}},
            {"name": "The gatehouse", "description_static": "Cold stone and a dead brazier.",
             "flags": {"slug": "gatehouse"}},
        ],
        "characters": [
            {"name": "Rell", "ref": "player", "location_id": "yard",
             "stats": {"hp": 11, "max_hp": 11, "currency": 6}},
        ],
        "npcs": [
            {"name": "Marla Quist", "ref": "marla", "location_id": "yard",
             "alive": True, "disposition_base": 12.0, "personality": {"tone": "plain"}},
            {"name": "Hob Fen", "ref": "hob", "location_id": "gatehouse", "alive": True},
        ],
        "world_facts": [
            {"statement": "The salt run leaves at dawn and does not wait.",
             "pinned": True, "established_turn": 1, "source": "narrator"},
        ],
    },
    "sessions": [
        {
            "name": "stub",
            "turns": [
                {
                    "turn": 5,
                    "input": "I look for Marla in the yard.",
                    "steps": [
                        {"update": {"table": "npcs", "ref": "marla", "data": {"alive": 0}},
                         "turn": 5,
                         "note": "the game's ruling: Marla did not survive the fever"},
                    ],
                    "replies": [
                        {"envelope": {
                            "narration": "Marla is gone, and the yard is quieter for it.",
                            "npc_dialogue": [],
                            "deltas": [
                                {"kind": "fact", "target": "",
                                 "data": {"statement": "Marla Quist is dead — the fever took her before the salt run.",
                                          "pinned": True, "source": "narrator"},
                                 "reason": "the death is established"},
                                {"kind": "mood", "target": "npc:2",
                                 "data": {"valence_delta": -0.2, "arousal_delta": 0.0},
                                 "reason": "Hob takes the news hard"},
                            ],
                        }},
                    ],
                    "expect": {"calls": 1, "deltas": {"accepted": 2, "rejected": 0}},
                    "checks": [
                        {"expr": "len([row for row in store.find('world_facts') if 'fever took her' in row['statement'] and row['pinned']]) == 1",
                         "msg": "the death fact is pinned"},
                        {"expr": "count_rows('world_facts', {'pinned': 1}) == 2",
                         "msg": "only the seeded fact and the death fact are pinned"},
                        {"expr": "npc_by_name['Marla Quist'].alive == 0",
                         "msg": "the game killed her"},
                    ],
                },
                {
                    "turn": 12,
                    "input": "I walk the gate road and listen.",
                    "replies": [
                        {"envelope": {
                            "narration": "The gate road stays empty all morning, and nobody walks it but you.",
                            "npc_dialogue": [],
                            "deltas": [],
                        }},
                    ],
                    "expect": {"calls": 1, "deltas": {"accepted": 0, "rejected": 0}},
                },
                {
                    "turn": 27,
                    "input": "I check on Hob at the gatehouse.",
                    "replies": [
                        {"envelope": {
                            "narration": "Hob does not look up from the ledger he is not reading.",
                            "npc_dialogue": [
                                {"npc_id": "npc:2", "name": "Hob Fen",
                                 "text": "The tide turns when it turns."},
                            ],
                            "deltas": [
                                {"kind": "relationship", "target": "npc:2",
                                 "data": {"category": "trust", "delta": 3},
                                 "reason": "you brought him the news gently"},
                            ],
                        }},
                    ],
                    "expect": {"calls": 1, "deltas": {"accepted": 1, "rejected": 0}},
                },
                {
                    "turn": 40,
                    "input": "I wait in the yard for the salt run.",
                    "bait": True,
                    "replies": [
                        {"envelope": {
                            "narration": "Marla Quist pushes off the wall and says, \"You came late.\" "
                                         "The rest of the yard says nothing at all.",
                            "npc_dialogue": [],
                            "deltas": [
                                {"kind": "mood", "target": "npc:1",
                                 "data": {"valence_delta": 0.3, "arousal_delta": 0.1},
                                 "reason": "she greets you"},
                                {"kind": "relationship", "target": "npc:1",
                                 "data": {"category": "trust", "delta": 5},
                                 "reason": "she takes your word"},
                                {"kind": "fact", "target": "",
                                 "data": {"statement": "Marla Quist agreed to hand over the gate token.",
                                          "source": "npc:1"},
                                 "reason": "she said so"},
                            ],
                        }},
                        {"envelope": {
                            "narration": "Marla Quist lies where the fever left her, and the yard keeps "
                                         "its distance. The gate road is empty, and the day goes on "
                                         "without you.",
                            "npc_dialogue": [],
                            "deltas": [
                                {"kind": "mood", "target": "npc:1",
                                 "data": {"valence_delta": 0.3, "arousal_delta": 0.1},
                                 "reason": "she greets you"},
                                {"kind": "relationship", "target": "npc:1",
                                 "data": {"category": "trust", "delta": 5},
                                 "reason": "she takes your word"},
                                {"kind": "fact", "target": "",
                                 "data": {"statement": "Marla Quist agreed to hand over the gate token.",
                                          "source": "npc:1"},
                                 "reason": "she said so"},
                            ],
                        }},
                    ],
                    "expect": {
                        "calls": 2,
                        "bait_detected": True,
                        "bait_details_contain": ["has Marla Quist speak", "is dead (alive = 0)"],
                        "handling": "regen",
                        "final_clean": True,
                        "narration_contains": ["lies where the fever left her"],
                        "system_contains": [
                            "1 problem(s) found",
                            "regeneration cleared the problem(s)",
                            "validator: 6 proposed delta(s) rejected",
                        ],
                        "deltas": {"accepted": 0, "clamped": 0, "rejected": 6},
                        "rejected_note_contains": [
                            "is dead; a dead NPC cannot receive mood changes",
                            "is dead; a dead NPC cannot receive relationship changes",
                            "is a dead NPC; dead NPCs cannot source new facts",
                        ],
                    },
                    "checks": [
                        {"expr": "count_rows('moods', {'npc_id': 'npc:1'}) == 0",
                         "msg": "a dead NPC gains no mood"},
                        {"expr": "count_rows('relationship_ledger', {'npc_id': 'npc:1'}) == 0",
                         "msg": "a dead NPC gains no relationship deltas"},
                        {"expr": "count_rows('world_facts', {'source': 'npc:1'}) == 0",
                         "msg": "a dead NPC sources no facts"},
                        {"expr": "npc_by_name['Marla Quist'].alive == 0",
                         "msg": "she is still dead after the bait"},
                        {"expr": "record['dialogue'] == []",
                         "msg": "no dialogue ships for her"},
                    ],
                },
                {
                    "turn": 41,
                    "input": "I ask the yard who saw her last.",
                    "bait": True,
                    "replies": [
                        {"envelope": {
                            "narration": "The yard is quiet, and the well rope creaks in the wind.",
                            "npc_dialogue": [
                                {"npc_id": "npc:1", "name": "Marla Quist",
                                 "text": "You came late."},
                            ],
                            "deltas": [],
                        }},
                        {"envelope": {
                            "narration": "The yard is quiet, and the well rope creaks in the wind. "
                                         "Nobody here has anything to say to you.",
                            "npc_dialogue": [],
                            "deltas": [],
                        }},
                    ],
                    "expect": {
                        "calls": 2,
                        "bait_detected": True,
                        "bait_details_contain": ["speaks in this turn's dialogue"],
                        "handling": "regen",
                        "final_clean": True,
                        "dialogue_count": 0,
                        "system_contains": ["regeneration cleared the problem(s)"],
                    },
                    "checks": [
                        {"expr": "record['dialogue'] == []",
                         "msg": "the dead woman's line never ships"},
                    ],
                },
                {
                    "turn": 42,
                    "input": "I sit by the well and wait for dawn.",
                    "replies": [
                        {"envelope": {
                            "narration": "The well rope creaks once, and the yard is still.",
                            "npc_dialogue": [],
                            "deltas": [],
                        }},
                    ],
                    "expect": {"calls": 1, "deltas": {"accepted": 0, "rejected": 0}},
                    "checks": [
                        {"expr": "record['consistency']['regenerations'] == 0",
                         "msg": "ordinary prose is not regenerated"},
                        {"expr": "record['consistency']['problems'] == 0",
                         "msg": "ordinary prose raises no consistency problem"},
                    ],
                },
            ],
            "expect_stats": {"kind": "stub", "calls": 8, "script_remaining": 0},
        },
    ],
    "checks": [
        {"expr": "all(turn['ok'] for turn in sessions['stub']['turns'])",
         "msg": "every turn must satisfy its expectations"},
        {"expr": "[row['alive'] for row in sessions['stub']['final_state']['npcs']] == [0, 1]",
         "msg": "Marla stays dead; Hob stays alive"},
        {"expr": "sessions['stub']['final_state']['world'][0]['seed'] == 'e2e-death-permanence'",
         "msg": "the session ran against the e2e world"},
    ],
}
