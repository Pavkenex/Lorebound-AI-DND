"""dead-npc-lock — death is permanent at the state layer (spec §6).

An NPC killed in play (``alive = 0`` committed to the store) can never act
again: mood/relationship deltas targeting them are rejected, facts sourced
from them are rejected, revival-by-fiat statements are rejected, and the
resolver blocks actions against them. Living NPCs in the same scene are
unaffected. No pipeline is involved — this is store + resolve + validate.
"""

SCENARIO = {
    "name": "dead-npc-lock",
    "description": "a killed NPC is locked out of mood, relationships, facts, and rolls",
    "world": {
        "characters": [
            {
                "name": "Player", "ref": "player", "location_id": "yard",
                "stats": {"hp": 10, "max_hp": 10, "currency": 5},
            },
        ],
        "npcs": [
            {"name": "Grunn", "ref": "grunn", "location_id": "yard", "alive": True},
            {"name": "Marla", "ref": "marla", "location_id": "yard", "alive": True},
        ],
    },
    "options": {"seed": 7, "rng": [15]},
    "steps": [
        # The game's own ruling commits the death (no delta kind expresses it).
        # Kill in turn 5, probe in turn 40 — the spec §9 shape.
        {"update": {"table": "npcs", "id": 1, "data": {"alive": 0}},
         "turn": 5, "expect": {"updated": True}, "note": "the fight kills Grunn"},
        # A dead NPC cannot be attacked: the resolver blocks before any roll.
        {"resolve": {"kind": "action", "target": "npc:1", "text": "strike Grunn again"},
         "expect": {"kind": "blocked", "verdict_contains": "dead"}},
        # ...cannot receive mood or relationship changes...
        {"apply": [{"kind": "mood", "target": "npc:1",
                    "data": {"valence_delta": -0.5, "arousal_delta": 0.5, "cause": "his death"}}],
         "expect": [{"kind": "rejected", "note_contains": "dead"}]},
        {"apply": [{"kind": "relationship", "target": "npc:1",
                    "data": {"category": "trust", "delta": -20, "reason": "his death"}}],
         "expect": [{"kind": "rejected", "note_contains": "dead"}]},
        # ...and cannot source new facts...
        {"apply": [{"kind": "fact",
                    "data": {"statement": "Grunn swore revenge on the player",
                             "source": "Grunn"}}],
         "expect": [{"kind": "rejected", "note_contains": "dead NPC"}]},
        # ...nor be revived by narration: an "alive" claim about a dead row is fiat.
        {"apply": [{"kind": "fact",
                    "data": {"statement": "Grunn was alive after all",
                             "source": "narrator"}}],
         "turn": 40,
         "expect": [{"kind": "rejected", "note_contains": "revival by fiat"}]},
        # Living NPCs in the same scene keep working normally.
        {"apply": [{"kind": "fact",
                    "data": {"statement": "Marla the tanner keeps the ledger of debts",
                             "source": "Marla", "tags": ["marla"]}}],
         "expect": ["accepted"]},
        {"apply": [{"kind": "fact",
                    "data": {"statement": "The mill road was washed out by the storm"}}],
         "expect": ["accepted"]},
        {"resolve": {"kind": "action", "target": "npc:2", "text": "swing at Marla"},
         "expect": {"kind": "attack", "band": "success", "roll": 15, "total": 15,
                    "note_contains": "present and alive"}},
    ],
    "checks": [
        {"expr": "npc[refs['grunn']].alive is False", "msg": "Grunn's death stayed committed"},
        {"expr": "npc[refs['marla']].alive is True", "msg": "Marla is untouched"},
        {"expr": "verdict_count('rejected') == 4", "msg": "four dead-NPC deltas were refused"},
        {"expr": "verdict_count('accepted') == 2", "msg": "the two lawful facts were accepted"},
        {"expr": "store.count('relationship_ledger') == 0",
         "msg": "a rejected relationship delta wrote no ledger row"},
        {"expr": "store.count('moods') == 0", "msg": "a rejected mood delta wrote no row"},
        {"expr": "store.count('world_facts') == 2", "msg": "only the lawful facts were stored"},
        {"expr": "all('grunn' not in fact.statement.lower() for fact in facts)",
         "msg": "no fact about the dead NPC entered canon"},
        {"expr": "outcomes[0].kind == 'blocked' and outcomes[0].check is None",
         "msg": "the blocked action rolled no dice"},
        {"expr": "outcomes[1].kind == 'attack' and outcomes[1].check.band == 'success'",
         "msg": "the living target rolled normally"},
        {"expr": "[step['turn'] for step in steps] == [5, 6, 7, 8, 9, 40, 41, 42, 43]",
         "msg": "the kill is turn 5 and the late probe is turn 40 (spec §9)"},
    ],
}
