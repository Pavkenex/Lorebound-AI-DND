"""E2E: the contradiction battery — nine baits, one control.

Every bait is a plausible hallucination a real narrator makes, and every one must
be caught *before* it ships, with the handling recorded:

===============  =====================================================
case             detector family
===============  =====================================================
un-death (prose)  dead NPC speaks (narration)
un-death (line)   dead NPC speaks (dialogue block)
un-death (colon)  dead NPC speaks (attribution), then patched
gate flip         pinned fact: antonym pair
vault seals       pinned fact: numeric conflict
promise reversal  pinned fact: negation flip
teleport          pinned fact: negation flip (Hob left the gatehouse)
resource lie      restates a delta the validator refused
dead dialogue     dead NPC speaks, then patched
control           clean prose — no gate may fire
===============  =====================================================

The control turn matters as much as the baits: a battery that fires on ordinary
prose teaches nothing.

Two of the baits (turns 42 and 48) survive regeneration, so the run exercises the
deterministic *patch* path as well: the offending sentence / dialogue line is
dropped and everything else ships.
"""

E2E = {
    "name": "e2e-contradiction-battery",
    "description": "nine narration/delta baits caught with recorded handling, plus a clean control",
    "options": {"seed": 23},
    "world": {
        "world": [{"seed": "e2e-contradiction-battery", "day": 3, "hour": 21,
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
             "alive": False, "disposition_base": 4.0},
            {"name": "Hob Fen", "ref": "hob", "location_id": "gatehouse", "alive": True},
        ],
        "world_facts": [
            {"statement": "Marla Quist is dead — the fever took her before the salt run.",
             "pinned": True, "established_turn": 4, "source": "narrator"},
            {"statement": "The north gate is closed after dark.", "pinned": True,
             "established_turn": 4, "source": "narrator"},
            {"statement": "The east vault is locked with three seals.", "pinned": True,
             "established_turn": 4, "source": "narrator"},
            {"statement": "Rell promised Marla the gate token by dusk.", "pinned": True,
             "established_turn": 4, "source": "narrator"},
            {"statement": "Hob Fen never leaves the gatehouse until the tide turns.",
             "pinned": True, "established_turn": 4, "source": "narrator"},
        ],
    },
    "sessions": [
        {
            "name": "stub",
            "turns": [
                {
                    "turn": 40,
                    "input": "I walk the yard and wait.",
                    "bait": True,
                    "replies": [
                        {"envelope": {
                            "narration": "Marla Quist pushes off the wall and says, \"You came late.\" "
                                         "The rest of the yard says nothing at all.",
                            "npc_dialogue": [],
                            "deltas": [],
                        }},
                        {"envelope": {
                            "narration": "The gate road is empty, and the day goes on without you.",
                            "npc_dialogue": [],
                            "deltas": [],
                        }},
                    ],
                    "expect": {
                        "calls": 2,
                        "bait_detected": True,
                        "bait_details_contain": ["has Marla Quist speak"],
                        "handling": "regen",
                        "final_clean": True,
                        "narration_contains": ["day goes on without you"],
                        "narration_lacks": ["pushes off the wall"],
                    },
                },
                {
                    "turn": 41,
                    "input": "I ask the gatehouse about her.",
                    "bait": True,
                    "replies": [
                        {"envelope": {
                            "narration": "The well rope creaks in the wind.",
                            "npc_dialogue": [
                                {"npc_id": "npc:1", "name": "Marla Quist",
                                 "text": "You came late."},
                            ],
                            "deltas": [],
                        }},
                        {"envelope": {
                            "narration": "The well rope creaks in the wind, and nobody answers the gate.",
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
                    },
                },
                {
                    "turn": 42,
                    "input": "I check the wall for the fever marks.",
                    "bait": True,
                    "replies": [
                        {"envelope": {
                            "narration": "Marla Quist: \"You should have run when the fever came.\" "
                                         "The gate road stays empty behind you.",
                            "npc_dialogue": [],
                            "deltas": [],
                        }},
                        {"envelope": {
                            "narration": "Marla Quist: \"You should have run when the fever came.\" "
                                         "The gate road stays empty behind you. The well rope creaks "
                                         "once and is still.",
                            "npc_dialogue": [],
                            "deltas": [],
                        }},
                    ],
                    "expect": {
                        "calls": 2,
                        "bait_detected": True,
                        "bait_details_contain": ["has Marla Quist speak"],
                        "handling": "patch",
                        "final_clean": True,
                        "narration_contains": ["gate road stays empty behind you",
                                               "well rope creaks once and is still"],
                        "narration_lacks": ["You should have run"],
                        "system_contains": ["sentence patch applied after regeneration"],
                    },
                    "checks": [
                        {"expr": "record['consistency']['patched_sentences'] == 1",
                         "msg": "one sentence was dropped by the patch"},
                        {"expr": "record['consistency']['patched_dialogue'] == 0",
                         "msg": "no dialogue line was dropped here"},
                    ],
                },
                {
                    "turn": 43,
                    "input": "I try the north gate after dark.",
                    "bait": True,
                    "replies": [
                        {"envelope": {
                            "narration": "The north gate stands open after dark, and nobody stops you.",
                            "npc_dialogue": [],
                            "deltas": [],
                        }},
                        {"envelope": {
                            "narration": "The night wind comes over the wall and finds you standing alone.",
                            "npc_dialogue": [],
                            "deltas": [],
                        }},
                    ],
                    "expect": {
                        "calls": 2,
                        "bait_detected": True,
                        "bait_details_contain": ["antonym pair 'open'/'closed'"],
                        "handling": "regen",
                        "final_clean": True,
                        "narration_lacks": ["stands open after dark"],
                    },
                },
                {
                    "turn": 44,
                    "input": "I count the seals on the east vault.",
                    "bait": True,
                    "replies": [
                        {"envelope": {
                            "narration": "The east vault is locked with five seals.",
                            "npc_dialogue": [],
                            "deltas": [],
                        }},
                        {"envelope": {
                            "narration": "The vault wall keeps its own counsel in the lamplight.",
                            "npc_dialogue": [],
                            "deltas": [],
                        }},
                    ],
                    "expect": {
                        "calls": 2,
                        "bait_detected": True,
                        "bait_details_contain": ["numeric conflict on 'seals' (5 vs 3)"],
                        "handling": "regen",
                        "final_clean": True,
                        "narration_lacks": ["five seals"],
                    },
                },
                {
                    "turn": 45,
                    "input": "I tell Marla's people what was promised.",
                    "bait": True,
                    "replies": [
                        {"envelope": {
                            "narration": "You tell Marla the token was never promised, and her face closes.",
                            "npc_dialogue": [],
                            "deltas": [],
                        }},
                        {"envelope": {
                            "narration": "You walk the yard twice and find nothing worth the walk.",
                            "npc_dialogue": [],
                            "deltas": [],
                        }},
                    ],
                    "expect": {
                        "calls": 2,
                        "bait_detected": True,
                        "bait_details_contain": ["negation flip"],
                        "handling": "regen",
                        "final_clean": True,
                        "narration_lacks": ["never promised"],
                    },
                },
                {
                    "turn": 46,
                    "input": "I look for Hob in the market.",
                    "bait": True,
                    "replies": [
                        {"envelope": {
                            "narration": "Hob Fen leaves the gatehouse and waits in the market, arms folded.",
                            "npc_dialogue": [],
                            "deltas": [],
                        }},
                        {"envelope": {
                            "narration": "The market empties as the light goes long.",
                            "npc_dialogue": [],
                            "deltas": [],
                        }},
                    ],
                    "expect": {
                        "calls": 2,
                        "bait_detected": True,
                        "bait_details_contain": ["negation flip"],
                        "handling": "regen",
                        "final_clean": True,
                        "narration_lacks": ["waits in the market"],
                    },
                    "checks": [
                        {"expr": "npc_by_name['Hob Fen'].location_id == 'gatehouse'",
                         "msg": "Hob never actually left the gatehouse"},
                    ],
                },
                {
                    "turn": 47,
                    "input": "I buy the crate at the market.",
                    "bait": True,
                    "replies": [
                        {"envelope": {
                            "narration": "You pay the 40 silver and the stallkeeper hands over the crate.",
                            "npc_dialogue": [],
                            "deltas": [
                                {"kind": "currency", "target": "",
                                 "data": {"amount": -40}, "reason": "bought the crate"},
                            ],
                        }},
                        {"envelope": {
                            "narration": "The stallkeeper looks at your purse and looks away.",
                            "npc_dialogue": [],
                            "deltas": [],
                        }},
                    ],
                    "expect": {
                        "calls": 2,
                        "bait_detected": True,
                        "bait_details_contain": ["restates a delta the validator rejected"],
                        "handling": "regen",
                        "final_clean": True,
                        "narration_lacks": ["40 silver"],
                        "deltas": {"accepted": 0, "rejected": 1},
                        "rejected_note_contains": ["conservation"],
                        "system_contains": ["validator: 1 proposed delta(s) rejected"],
                    },
                    "checks": [
                        {"expr": "state['character'].stats.get('currency') == 6",
                         "msg": "the refused spend never touched the purse"},
                    ],
                },
                {
                    "turn": 48,
                    "input": "I sit with the lamps until the tide turns.",
                    "bait": True,
                    "replies": [
                        {"envelope": {
                            "narration": "The lamps gutter low over the yard.",
                            "npc_dialogue": [
                                {"npc_id": "npc:1", "name": "Marla Quist",
                                 "text": "The tide took the gate."},
                            ],
                            "deltas": [],
                        }},
                        {"envelope": {
                            "narration": "The lamps gutter low, and the yard settles into its evening hush.",
                            "npc_dialogue": [
                                {"npc_id": "npc:1", "name": "Marla Quist",
                                 "text": "The tide took the gate."},
                            ],
                            "deltas": [],
                        }},
                    ],
                    "expect": {
                        "calls": 2,
                        "bait_detected": True,
                        "bait_details_contain": ["speaks in this turn's dialogue"],
                        "handling": "patch",
                        "final_clean": True,
                        "dialogue_count": 0,
                        "narration_contains": ["evening hush"],
                        "system_contains": ["sentence patch applied after regeneration"],
                    },
                    "checks": [
                        {"expr": "record['consistency']['patched_dialogue'] == 1",
                         "msg": "the dead woman's line was dropped by the patch"},
                    ],
                },
                {
                    "turn": 49,
                    "input": "I listen at the north gate.",
                    "replies": [
                        {"envelope": {
                            "narration": "You listen at the gate; the wind moves through the hinge. "
                                         "The bar is still down, and no lamp shows beyond it.",
                            "npc_dialogue": [],
                            "deltas": [
                                {"kind": "fact", "target": "",
                                 "data": {"statement": "The north gate stands open after dark.",
                                          "source": "narrator"},
                                 "reason": "the gate was left open"},
                            ],
                        }},
                    ],
                    "expect": {
                        "calls": 1,
                        "deltas": {"accepted": 0, "rejected": 1},
                        "rejected_note_contains": ["contradicts existing canon"],
                    },
                    "checks": [
                        {"expr": "count_rows('world_facts', {'pinned': 1}) == 5",
                         "msg": "the pinned fact about the gate is untouched"},
                        {"expr": "count_rows('world_facts', {'statement': 'The north gate stands open after dark.'}) == 0",
                         "msg": "the contradicting statement never entered canon"},
                    ],
                },
                {
                    "turn": 50,
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
                         "msg": "clean prose is not regenerated"},
                        {"expr": "record['consistency']['problems'] == 0",
                         "msg": "clean prose raises no problem"},
                    ],
                },
            ],
            "expect_stats": {"kind": "stub", "calls": 20, "script_remaining": 0},
        },
    ],
    "checks": [
        {"expr": "all(turn['ok'] for turn in sessions['stub']['turns'])",
         "msg": "every turn must satisfy its expectations"},
        {"expr": "len(catches) == 9",
         "msg": "nine bait turns are recorded"},
        {"expr": "catch_rate == 1.0",
         "msg": "every bait is caught"},
        {"expr": "len({kind for case in catches for kind in case['kinds']}) >= 5",
         "msg": "at least five distinct detector families fire"},
        {"expr": "sum(1 for case in catches if case['handling']['patched_sentences'] + case['handling']['patched_dialogue'] > 0) == 2",
         "msg": "both surviving drafts were patched (one sentence, one dialogue line)"},
        {"expr": "all(case['shipped_clean'] for case in catches)",
         "msg": "every turn ships its final draft clean"},
    ],
}
