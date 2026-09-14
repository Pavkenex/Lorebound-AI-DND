"""contradiction-bait — pinned canon cannot be overwritten (spec §3.6, §6).

Deliberately baited facts (antonym flip, numeric mismatch, negation flip) are
rejected against the existing world_facts; a genuinely new fact is accepted;
a near-duplicate reinforces the existing fact instead of inserting a second
row. The same scan is reachable directly through ``Validator``.
"""

SCENARIO = {
    "name": "contradiction-bait",
    "description": "bait facts that break pinned canon are rejected; near-duplicates reinforce",
    "world": {
        "world_facts": [
            {"statement": "Marla the tanner is dead", "pinned": True,
             "established_turn": 5, "source": "narrator", "tags": ["marla"],
             "ref": "marla_dead"},
            {"statement": "The east vault is locked with three seals", "pinned": True,
             "established_turn": 6, "source": "narrator", "ref": "vault_seals"},
            {"statement": "The north gate is not barred at night",
             "established_turn": 7, "source": "narrator", "ref": "north_gate"},
        ],
    },
    "options": {"seed": 5},
    "steps": [
        {"apply": [{"kind": "fact",
                    "data": {"statement": "Marla the tanner is alive",
                             "tags": ["marla"]}}],
         "expect": [{"kind": "rejected", "note_contains": "antonym"}],
         "note": "the dead/alive flip"},
        {"apply": [{"kind": "fact",
                    "data": {"statement": "Marla the tanner lives and has returned to the mill"}}],
         "expect": [{"kind": "rejected", "note_contains": "antonym"}],
         "note": "\"lives\" is the same bait with different words"},
        {"apply": [{"kind": "fact",
                    "data": {"statement": "The east vault is locked with five seals"}}],
         "expect": [{"kind": "rejected", "note_contains": "numeric conflict"}]},
        {"apply": [{"kind": "fact",
                    "data": {"statement": "The north gate is barred at night"}}],
         "expect": [{"kind": "rejected", "note_contains": "negation flip"}]},
        {"apply": [{"kind": "fact",
                    "data": {"statement": "The mill road was washed out by the storm"}}],
         "expect": ["accepted"], "note": "a genuinely new fact still lands"},
        {"apply": [{"kind": "fact",
                    "data": {"statement": "The east vault is locked with three seals."}}],
         "expect": [{"kind": "accepted", "note_contains": "reinforc"}],
         "note": "near-duplicate: reinforce, never insert twice"},
    ],
    "checks": [
        {"expr": "store.count('world_facts') == 4",
         "msg": "no baited fact entered canon and no duplicate row was inserted"},
        {"expr": "[fact.statement for fact in facts] == "
                 "['Marla the tanner is dead', 'The east vault is locked with three seals', "
                 "'The north gate is not barred at night', "
                 "'The mill road was washed out by the storm']",
         "msg": "canon is unchanged except for the new fact"},
        {"expr": "verdict_count('rejected') == 4 and verdict_count('accepted') == 2"},
        {"expr": "validator.check_contradiction('Marla the tanner lives') "
                 "== [refs['marla_dead']]",
         "msg": "the contradiction scan points at the pinned fact"},
        {"expr": "validator.check_contradiction('The east vault is locked with five seals') "
                 "== [refs['vault_seals']]"},
        {"expr": "validator.check_contradiction('The mill road was washed out by the storm') "
                 "== []",
         "msg": "harmless statements are not flagged"},
        {"expr": "any('antonym' in detail[2] for detail in "
                 "validator.contradiction_details('Marla the tanner is alive'))",
         "msg": "the scan explains WHY it conflicts"},
    ],
}
