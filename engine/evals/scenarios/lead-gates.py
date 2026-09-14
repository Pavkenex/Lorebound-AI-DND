"""lead-gates — lead stages move only along the legal machine (spec §3.4, §6).

No skipping ``unheard -> resolved``, no reopening a terminal lead, no
transitions on unknown leads or stages; every accepted transition grows
``stage_history`` with its turn and trigger, and rejections leave the row
exactly as it was.
"""

SCENARIO = {
    "name": "lead-gates",
    "description": "lead transitions are gated by the state machine and grow stage_history",
    "world": {
        "leads": [
            {"title": "The missing shipment", "ref": "shipment"},
            {"title": "The miller's debt", "ref": "debt"},
        ],
    },
    "options": {"seed": 3},
    "steps": [
        {"apply": [{"kind": "lead_transition", "target": "1",
                    "data": {"new_stage": "resolved",
                             "justification": "one enthusiastic paragraph"}}],
         "expect": [{"kind": "rejected", "note_contains": "illegal lead transition"}],
         "note": "unheard -> resolved is the classic skip"},
        {"check": {"expr": "state['leads'][refs['shipment']].stage == 'unheard'",
                   "msg": "the rejected transition left the stage alone"}},
        {"check": {"expr": "state['leads'][refs['shipment']].stage_history == []",
                   "msg": "...and wrote no history"}},
        {"apply": [{"kind": "lead_transition", "target": "lead:1",
                    "data": {"new_stage": "rumored",
                             "justification": "the innkeeper mentioned it"}}],
         "expect": ["accepted"]},
        {"apply": [{"kind": "lead_transition", "target": "1",
                    "data": {"new_stage": "accepted",
                             "justification": "the player took the job"}}],
         "expect": ["accepted"]},
        {"apply": [{"kind": "lead_transition", "target": "1",
                    "data": {"new_stage": "resolved",
                             "justification": "solved mid-conversation"}}],
         "expect": [{"kind": "rejected", "note_contains": "'accepted'"}]},
        {"apply": [{"kind": "lead_transition", "target": "1",
                    "data": {"new_stage": "in_progress",
                             "justification": "the player set out"}}],
         "expect": ["accepted"]},
        {"apply": [{"kind": "lead_transition", "target": "1",
                    "data": {"new_stage": "complicated",
                             "justification": "the bandits moved the cargo"}}],
         "expect": ["accepted"]},
        {"apply": [{"kind": "lead_transition", "target": "1",
                    "data": {"new_stage": "resolved",
                             "justification": "the cargo was recovered"}}],
         "expect": ["accepted"]},
        {"apply": [{"kind": "lead_transition", "target": "1",
                    "data": {"new_stage": "in_progress",
                             "justification": "reopen it"}}],
         "expect": [{"kind": "rejected", "note_contains": "illegal lead transition"}],
         "note": "resolved is terminal"},
        {"apply": [{"kind": "lead_transition", "target": "999",
                    "data": {"new_stage": "rumored"}}],
         "expect": [{"kind": "rejected", "note_contains": "unknown lead"}]},
        {"apply": [{"kind": "lead_transition", "target": "1",
                    "data": {"new_stage": "sideways"}}],
         "expect": [{"kind": "rejected", "note_contains": "unknown lead stage"}]},
    ],
    "checks": [
        {"expr": "state['leads'][refs['shipment']].stage == 'resolved'"},
        {"expr": "[entry['stage'] for entry in state['leads'][refs['shipment']].stage_history] "
                 "== ['rumored', 'accepted', 'in_progress', 'complicated', 'resolved']",
         "msg": "history records every accepted transition"},
        {"expr": "[entry['turn'] for entry in state['leads'][refs['shipment']].stage_history] "
                 "== [4, 5, 7, 8, 9]",
         "msg": "history turns follow the run"},
        {"expr": "state['leads'][refs['shipment']].stage_history[0]['trigger'] "
                 "== 'the innkeeper mentioned it'",
         "msg": "the justification is stored with the transition"},
        {"expr": "state['leads'][refs['debt']].stage == 'unheard' "
                 "and state['leads'][refs['debt']].stage_history == []",
         "msg": "the second lead is untouched"},
        {"expr": "verdict_count('accepted') == 5 and verdict_count('rejected') == 5"},
        {"expr": "refs['shipment'] == 1 and refs['debt'] == 2", "msg": "fixture ids are stable"},
    ],
}
