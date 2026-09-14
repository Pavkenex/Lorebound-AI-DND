"""boundary-clamps — every clamp lands exactly on its edge (spec §6).

Mood [-1, 1], relationship per-event (±40) and total (±100), hp (0..max_hp),
and stat bounds (-10..30) are probed from inside, at, and beyond each edge.
Each verdict must report the value actually applied (``clamped_to``), and the
committed rows must hold the exact edge value — no overshoot, no drift.

Relationship meters are read DECAYED (spec §3.5: current = anchor + Σ decayed
deltas), so a meter driven to ±100 one turn earlier sits one decay step inside
the edge when the follow-up probe arrives: the clamp is then the decayed slack,
not 0.0 — and the meter still lands exactly on ±100. ``slow`` deltas decay
0.01/turn (ARCHITECTURE), the paired probes are one turn apart, and
``RelationshipLedger.currents`` rounds to 6 dp, hence the two constants below.
"""

import math

# Slack the decay opened at the ±100 edges one turn after the edge-driving
# write (see the module docstring): 100 - round6(90 + 10*exp(-0.01)) and
# -100 - round6(-95 - 5*exp(-0.01)).
_TAM_SLACK = round(100 - round(90 + 10 * math.exp(-0.01), 6), 6)
_MARLA_SLACK = round(-100 - round(-95 - 5 * math.exp(-0.01), 6), 6)

SCENARIO = {
    "name": "boundary-clamps",
    "description": "mood/relationship/hp/stat clamps are exact at the edges and report the applied value",
    "world": {
        "characters": [
            {"name": "Player", "ref": "player", "location_id": "yard",
             "stats": {"hp": 3, "max_hp": 10, "currency": 10, "might": 9}},
        ],
        "npcs": [
            {"name": "Tam", "ref": "tam", "disposition_base": 90.0},
            {"name": "Marla", "ref": "marla", "disposition_base": -95.0},
            {"name": "Odo", "ref": "odo", "disposition_base": 0.0},
            {"name": "Ines", "ref": "ines", "disposition_base": 0.0},
        ],
        "moods": [
            {"npc_id": "npc:1", "valence": 0.6, "arousal": -0.4,
             "baseline_valence": 0.6, "baseline_arousal": -0.4},
            {"npc_id": "npc:2", "valence": 0.0, "arousal": 0.0,
             "baseline_valence": 0.0, "baseline_arousal": 0.0},
            {"npc_id": "npc:3", "valence": 0.2, "arousal": 0.0,
             "baseline_valence": 0.2, "baseline_arousal": 0.0},
        ],
    },
    "options": {"seed": 9},
    "steps": [
        {"check": {"expr": "refs['tam'] == 1 and refs['marla'] == 2 and refs['odo'] == 3 "
                           "and refs['ines'] == 4",
                   "msg": "fixture ids line up with the moods rows"}},
        # mood ceiling: 0.6 + 3 -> applied +0.4, lands exactly on 1.0
        {"apply": [{"kind": "mood", "target": "npc:1",
                    "data": {"valence_delta": 3, "arousal_delta": -0.5, "cause": "a toast"}}],
         "expect": [{"kind": "clamped",
                     "clamped_to": {"valence_delta": 0.4, "arousal_delta": -0.5}}]},
        # mood floor: 0.0 - 2 -> applied -1.0, lands exactly on -1.0
        {"apply": [{"kind": "mood", "target": "npc:2",
                    "data": {"valence_delta": -2, "arousal_delta": 0}}],
         "expect": [{"kind": "clamped",
                     "clamped_to": {"valence_delta": -1.0, "arousal_delta": 0.0}}]},
        # already at the floor: nothing applies, reported exactly as 0.0
        {"apply": [{"kind": "mood", "target": "npc:2",
                    "data": {"valence_delta": -2, "arousal_delta": 0}}],
         "expect": [{"kind": "clamped",
                     "clamped_to": {"valence_delta": 0.0, "arousal_delta": 0.0}}]},
        # in-bounds mood change is accepted, not clamped
        {"apply": [{"kind": "mood", "target": "npc:3",
                    "data": {"valence_delta": 0.5, "arousal_delta": 0.2}}],
         "expect": ["accepted"]},
        # relationship ceiling: 90 + 40 -> capped at +10, lands exactly on 100
        {"apply": [{"kind": "relationship", "target": "npc:1",
                    "data": {"category": "trust", "delta": 40, "reason": "kept the oath"}}],
         "turn": 6,
         "expect": [{"kind": "clamped", "clamped_to": 10.0, "note_contains": "already at"}]},
        # one decay step later only the decayed slack fits — the meter still
        # lands exactly on 100.00, so the applied delta is the slack, not 0.0
        {"apply": [{"kind": "relationship", "target": "npc:1",
                    "data": {"category": "trust", "delta": 40, "reason": "again"}}],
         "turn": 7,
         "expect": [{"kind": "clamped", "clamped_to": _TAM_SLACK,
                     "note_contains": "already at"}]},
        # relationship floor: -95 - 40 -> capped at -5, lands exactly on -100
        {"apply": [{"kind": "relationship", "target": "npc:2",
                    "data": {"category": "trust", "delta": -40, "reason": "betrayal"}}],
         "turn": 8,
         "expect": [{"kind": "clamped", "clamped_to": -5.0, "note_contains": "already at"}]},
        # the floor probe mirrors the ceiling: the decayed slack applies
        {"apply": [{"kind": "relationship", "target": "npc:2",
                    "data": {"category": "trust", "delta": -40, "reason": "again"}}],
         "turn": 9,
         "expect": [{"kind": "clamped", "clamped_to": _MARLA_SLACK,
                     "note_contains": "already at"}]},
        # in-bounds relationship change is accepted
        {"apply": [{"kind": "relationship", "target": "npc:3",
                    "data": {"category": "trust", "delta": 12, "reason": "a favour"}}],
         "expect": ["accepted"]},
        # per-event bound: 100 proposed -> 40 applied
        {"apply": [{"kind": "relationship", "target": "npc:4",
                    "data": {"category": "trust", "delta": 100, "reason": "a grand deed"}}],
         "expect": [{"kind": "clamped", "clamped_to": 40.0,
                     "note_contains": "per-event bound"}]},
        # hp: over-heal clamps to max_hp, over-damage to 0, nothing below 0
        {"apply": [{"kind": "hp", "target": "", "data": {"delta": 12, "cause": "a potion"}}],
         "expect": [{"kind": "clamped", "clamped_to": 7.0}]},
        {"apply": [{"kind": "hp", "target": "player", "data": {"delta": -20, "cause": "a trap"}}],
         "expect": [{"kind": "clamped", "clamped_to": -10.0}]},
        {"apply": [{"kind": "hp", "target": "", "data": {"delta": -1, "cause": "a scratch"}}],
         "expect": [{"kind": "clamped", "clamped_to": 0.0}]},
        # stat bounds are (-10, 30)
        {"apply": [{"kind": "stat", "target": "", "data": {"stat": "might", "delta": 50}}],
         "expect": [{"kind": "clamped", "clamped_to": 21.0}]},
        {"apply": [{"kind": "stat", "target": "", "data": {"stat": "might", "delta": -100}}],
         "expect": [{"kind": "clamped", "clamped_to": -40.0}]},
        {"apply": [{"kind": "stat", "target": "", "data": {"stat": "might", "delta": -5}}],
         "expect": [{"kind": "clamped", "clamped_to": 0.0}]},
    ],
    "checks": [
        {"expr": "state['moods']['npc:1']['valence'] == 1.0",
         "msg": "mood ceiling is reached exactly"},
        {"expr": "state['moods']['npc:1']['arousal'] == -0.9"},
        {"expr": "state['moods']['npc:2']['valence'] == -1.0",
         "msg": "mood floor is reached exactly"},
        {"expr": "state['moods']['npc:3']['valence'] == 0.7 and state['moods']['npc:3']['arousal'] == 0.2"},
        {"expr": (f"[row.delta for row in ledger if row.npc_id == 'npc:1']"
                  f" == [10.0, {_TAM_SLACK!r}]"),
         "msg": "only the applied relationship deltas are in the ledger — the second is decayed slack"},
        {"expr": (f"[row.delta for row in ledger if row.npc_id == 'npc:2']"
                  f" == [-5.0, {_MARLA_SLACK!r}]")},
        {"expr": "[row.delta for row in ledger if row.npc_id == 'npc:3'] == [12.0]"},
        {"expr": "[row.delta for row in ledger if row.npc_id == 'npc:4'] == [40.0]",
         "msg": "the per-event bound caps what is written"},
        {"expr": "state['character'].stats['hp'] == 0 and state['character'].stats['might'] == -10",
         "msg": "hp and stat edges hold exactly"},
        {"expr": "verdict_count('clamped') == 14", "msg": "every probe was reported as clamped"},
        {"expr": "verdict_count('accepted') == 2", "msg": "in-bounds changes are not clamped"},
    ],
}
