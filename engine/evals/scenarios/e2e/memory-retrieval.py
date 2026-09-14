"""E2E: NPC memory in play — decay, reinforcement, retrieval, prompt injection.

The round's headline failure mode ("Marla is permanently furious") is a *stale
grievance* that never leaves the prompt. This scenario plays it out with real
turns through the pipeline:

* turn 1: Marla's grievance (sentiment -0.9) outranks a fresh-ish promise —
  while it is fresh, the grudge is the loudest thing she has.
* turn 40: the *same* grievance, established at turn 1, has decayed below a
  promise made at turn 38 (``grievance/kindness/observed`` decay 0.15/turn vs
  ``promise`` 0.02/turn). A *fresh* copy of the identical grievance still beats
  that promise, so it is age — not sentiment, not relevance — that moved it.
* turn 47: playing the promise out loud reinforces it three times
  (``npc_memory.reinforce``, the same call the game makes).
* turn 62: the reinforced promise is still retrieved (rank 5 of the top 6) and
  still injected into the prompt, while an equal-but-unreinforced twin of the
  same age, type and sentiment has fallen out of the top 6 entirely.

The reinforcement claim is exact: the reinforced entry's salience is the
unreinforced twin's plus ``w_reinforced * log1p(3)`` — nothing else.

Retrieval here is scoped per NPC (spec §3.1): Hob's memories never leak into
Marla's list.
"""

E2E = {
    "name": "e2e-memory-retrieval",
    "description": "stale memories decay out, reinforcement keeps a promise alive, retrieval is per-NPC",
    "options": {"seed": 31},
    "world": {
        "world": [{"seed": "e2e-memory-retrieval", "day": 4, "hour": 20,
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
             "alive": True, "disposition_base": 12.0},
            {"name": "Hob Fen", "ref": "hob", "location_id": "gatehouse", "alive": True},
        ],
        "npc_memory": [
            # the lead pair: same grievance, one stale (t1) and one recent (t38)
            {"npc_id": "npc:1", "turn_established": 1, "type": "grievance",
             "sentiment": -0.9,
             "statement": "Rell broke the salt tally, and Marla counts it against him."},
            {"npc_id": "npc:1", "turn_established": 38, "type": "grievance",
             "sentiment": -0.9,
             "statement": "Rell shorted the salt tally again this week."},
            {"npc_id": "npc:1", "turn_established": 38, "type": "promise",
             "sentiment": 0.7,
             "statement": "Rell promised Marla the gate token by dusk."},
            # the survival pair: one reinforced at turn 45+, one never touched
            {"npc_id": "npc:1", "turn_established": 1, "type": "promise",
             "sentiment": 0.7,
             "statement": "Rell promised Marla he would keep the night watch at the well."},
            {"npc_id": "npc:1", "turn_established": 1, "type": "promise",
             "sentiment": 0.7,
             "statement": "Rell promised Marla he would stand watch at the old well."},
            # filler so the top-6 cut is a real threshold at turn 62
            {"npc_id": "npc:1", "turn_established": 60, "type": "grievance",
             "sentiment": -0.9, "statement": "Rell left the salt tally short on purpose."},
            {"npc_id": "npc:1", "turn_established": 61, "type": "kindness",
             "sentiment": 0.8,
             "statement": "Rell mended the gate latch without being asked."},
            {"npc_id": "npc:1", "turn_established": 61, "type": "observed",
             "sentiment": 0.5, "statement": "Rell checks the well rope every morning now."},
            {"npc_id": "npc:1", "turn_established": 50, "type": "grievance",
             "sentiment": -0.8, "statement": "Rell argued with Marla about the night watch roster."},
            {"npc_id": "npc:1", "turn_established": 5, "type": "secret_shared",
             "sentiment": 0.6, "statement": "Rell told Marla where the second key is buried."},
            # another NPC's memory: never eligible for Marla's retrieval
            {"npc_id": "npc:2", "turn_established": 60, "type": "grievance",
             "sentiment": -0.7,
             "statement": "Rell tracked mud through the gatehouse and Hob noticed."},
        ],
    },
    "sessions": [
        {
            "name": "stub",
            "turns": [
                {
                    "turn": 1,
                    "input": "I ask Marla how the tally is holding up.",
                    "replies": [
                        {"envelope": {
                            "narration": "Marla does not look up from the tally board.",
                            "npc_dialogue": [], "deltas": [],
                        }},
                    ],
                    "expect": {"calls": 1},
                    "checks": [
                        {"expr": "memory_score('counts it against him', turn=1) > "
                                 "memory_score('gate token by dusk', turn=1)",
                         "msg": "while the grievance is fresh it outranks the promise"},
                    ],
                },
                {
                    "turn": 40,
                    "input": "I ask Marla about the salt tally again.",
                    "replies": [
                        {"envelope": {
                            "narration": "Marla lets the silence answer for her.",
                            "npc_dialogue": [], "deltas": [],
                        }},
                    ],
                    "expect": {"calls": 1},
                    "checks": [
                        {"expr": "memory_score('counts it against him', turn=40) < "
                                 "memory_score('gate token by dusk', turn=40)",
                         "msg": "the stale grievance has decayed below the recent promise"},
                        {"expr": "memory_score('shorted the salt tally', turn=40) > "
                                 "memory_score('counts it against him', turn=40)",
                         "msg": "a fresh copy of the same grievance still wins — decay, not sentiment"},
                        {"expr": "rank_of('counts it against him', turn=40) is None",
                         "msg": "the stale grievance is no longer retrieved at all"},
                        {"expr": "rank_of('shorted the salt tally', turn=40) is not None",
                         "msg": "the fresh copy is retrieved"},
                    ],
                },
                {
                    "turn": 47,
                    "input": "I keep the night watch at the well and tell Marla so.",
                    "steps": [
                        {"turn": 45, "memory": {"action": "reinforce", "npc_id": "npc:1",
                                                "statement": "Rell promised Marla he would keep the night watch at the well."},
                         "expect": {"matched": 1}, "note": "she hears it and it counts"},
                        {"turn": 46, "memory": {"action": "reinforce", "npc_id": "npc:1",
                                                "statement": "Rell promised Marla he would keep the night watch at the well."}},
                        {"turn": 47, "memory": {"action": "reinforce", "npc_id": "npc:1",
                                                "statement": "Rell promised Marla he would keep the night watch at the well."}},
                    ],
                    "replies": [
                        {"envelope": {
                            "narration": "The well rope runs through your hands all night, and the yard sleeps.",
                            "npc_dialogue": [], "deltas": [],
                        }},
                    ],
                    "expect": {"calls": 1},
                    "checks": [
                        {"expr": "memory_entry('keep the night watch at the well').reinforced_count == 3",
                         "msg": "three reinforcements were recorded"},
                        {"expr": "memory_entry('stand watch at the old well').reinforced_count == 0",
                         "msg": "the twin was never reinforced"},
                    ],
                },
                {
                    "turn": 62,
                    "input": "I check the well rope and ask Marla about the night watch.",
                    "replies": [
                        {"envelope": {
                            "narration": "Marla answers without turning: the watch was kept, and she knows it.",
                            "npc_dialogue": [], "deltas": [],
                        }},
                    ],
                    "expect": {
                        "calls": 1,
                        "prompt_contains": ["keep the night watch at the well"],
                        "prompt_lacks": ["stand watch at the old well"],
                    },
                    "checks": [
                        {"expr": "rank_of('keep the night watch at the well', turn=62) == 5",
                         "msg": "the reinforced promise is still retrieved (rank 5 of 6)"},
                        {"expr": "rank_of('stand watch at the old well', turn=62) is None",
                         "msg": "the unreinforced twin has fallen out of the top 6"},
                        {"expr": "abs((memory_score('keep the night watch at the well', turn=62) "
                                 "- memory_score('stand watch at the old well', turn=62)) "
                                 "- 0.10 * log(1 + 3)) < 1e-6",
                         "msg": "the reinforced entry is worth exactly w_reinforced*log1p(3) more"},
                        {"expr": "retrieve(turn=62, npc_id='npc:2') == "
                                 "['Rell tracked mud through the gatehouse and Hob noticed.']",
                         "msg": "Hob's memory set is scoped to Hob"},
                        {"expr": "all('gatehouse' not in statement for statement in retrieve(turn=62))",
                         "msg": "another NPC's memory never leaks into Marla's retrieval"},
                        {"expr": "record['consistency']['problems'] == 0",
                         "msg": "these turns are not contradiction turns"},
                    ],
                },
            ],
            "expect_stats": {"kind": "stub", "calls": 4, "script_remaining": 0},
        },
    ],
    "checks": [
        {"expr": "all(turn['ok'] for turn in sessions['stub']['turns'])",
         "msg": "every turn must satisfy its expectations"},
        {"expr": "count_rows('npc_memory', {}) == 11",
         "msg": "the world seeded eleven memories"},
    ],
}
