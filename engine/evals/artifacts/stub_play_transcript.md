# Stub-play transcript (R9 evidence)

Raw, verbatim stdout of a 12-turn session driven through the real CLI on a
scratch database. No model, no API key (spec §10.1): the deterministic stub
narrator reads the same assembled prompt a live narrator gets.

    cd engine && PYTHONPATH=src .venv/bin/python -m engine play --stub --db /opt/data/scratch/r9_stub_play.db

Input piped on stdin: the 12 actions below, then `/state`, then `/quit`.

```
I look around the yard and take stock of the morning.
I ask Marla Quist about the missing salt shipment.
I search the cart ruts by the gate for boot prints.
I climb the salt-crusted wall to see over it.
I listen at the mouth of the rope well for anything below.
I threaten Grunn Sallow.
I study Marla's face for what she is not saying.
I read the gatehouse facade for the age of the portcullis chain.
I haul the rope well's bucket up hand over hand.
I ask Marla whether her brother Dain ever came back through the gate.
I try to lift the fallen beam beside the cart ruts.
I watch the market crowd through the gate for anyone following me.
```

Each turn shows the narration followed by its `(verdict: …)` line — the
code-owned mechanics statement from `resolve.py` that the narration must
honor (FAILURE bands at turns 3, 5, 7; a natural-1 CRITICAL FAILURE at turn 8;
SUCCESS AT A COST at turns 9 and 12 — fail-forward, never a clean win the code
did not award), plus any narrator/consistency notes. The transcript ends with
the end-of-session `state_view()` from `/state`.

```text
Lorebound engine — stub narrator
db: /opt/data/scratch/r9_stub_play.db   world: saltmarsh-demo   turn: 1
Type an action, /state for the snapshot, /help for commands, /quit to leave.

[turn 1] You take in The Salt Gate yard. Marla Quist is here with you.

Your perception check goes through cleanly. The world answers plainly, and for this moment the odds are on your side.

The scene holds, waiting to see what you do next.
  (verdict: Perception check vs dc 12: SUCCESS (19 vs 12, margin 7) — the action works as intended.)

[turn 2] You are still at The Salt Gate yard, and the light has not changed. Marla Quist is here with you.

Your words go out into the scene and the moment simply holds — whatever answer comes will have to be earned.

Whatever comes next, the world will not move first.
  (verdict: No mechanics resolve this dialogue turn; narration may proceed freely.)

[turn 3] The Salt Gate yard waits around you. Marla Quist is here with you.

Your investigation check does not land. The moment turns against you, and the world hands back a complication instead of a result.

The moment stays open; the next move is yours.
  (verdict: Investigation check vs dc 12: FAILURE (10 vs 12, margin -2) — it does not succeed; fail forward with a consequence, never narrate success.)

[turn 4] You take in The Salt Gate yard. Marla Quist is here with you.

Your athletics check goes through cleanly. The world answers plainly, and for this moment the odds are on your side.

The scene holds, waiting to see what you do next.
  (verdict: Athletics check vs dc 12: SUCCESS (20 vs 12, margin 8) — the action works as intended.)

[turn 5] You are still at The Salt Gate yard, and the light has not changed. Marla Quist is here with you.

Your perception check does not land. The moment turns against you, and the world hands back a complication instead of a result.

Whatever comes next, the world will not move first.
  (verdict: Perception check vs dc 12: FAILURE (9 vs 12, margin -3) — it does not succeed; fail forward with a consequence, never narrate success.)

[turn 6] The Salt Gate yard waits around you. Marla Quist is here with you.

The way is barred before you even start: the scene will not allow it, and nothing you do here changes that.

The moment stays open; the next move is yours.
  (verdict: No roll is made: target 'npc:5' is not present (at 'market', player at 'yard'). The action cannot proceed as declared; narrate the obstacle, not a success or failure of the attempt.)

[turn 7] You take in The Salt Gate yard. Marla Quist is here with you.

Your investigation check does not land. The moment turns against you, and the world hands back a complication instead of a result.

The scene holds, waiting to see what you do next.
  (verdict: Investigation check vs dc 12: FAILURE (4 vs 12, margin -8) — it does not succeed; fail forward with a consequence, never narrate success.)

[turn 8] You are still at The Salt Gate yard, and the light has not changed. Marla Quist is here with you.

Your insight check goes wrong from the first motion, and the wreck carries further than you meant.

Whatever comes next, the world will not move first.
  (verdict: Natural 1 on the insight check (dc 12): CRITICAL FAILURE — the action fails badly with an added complication.)

[turn 9] The Salt Gate yard waits around you. Marla Quist is here with you.

Your athletics check works — and takes its price on the way through. Whatever you won, the scene keeps a piece of it.

The moment stays open; the next move is yours.
  (verdict: Athletics check vs dc 12: SUCCESS AT A COST (13 vs 12, margin 1) — it works, but a cost or complication must be narrated.)

[turn 10] You take in The Salt Gate yard. Marla Quist is here with you.

Your words go out into the scene and the moment simply holds — whatever answer comes will have to be earned.

The scene holds, waiting to see what you do next.
  (verdict: No mechanics resolve this dialogue turn; narration may proceed freely.)

[turn 11] You are still at The Salt Gate yard, and the light has not changed. Marla Quist is here with you.

Your athletics check goes through cleanly. The world answers plainly, and for this moment the odds are on your side.

Whatever comes next, the world will not move first.
  (verdict: Athletics check vs dc 12: SUCCESS (21 vs 12, margin 9) — the action works as intended.)

[turn 12] The Salt Gate yard waits around you. Marla Quist is here with you.

Your perception check works — and takes its price on the way through. Whatever you won, the scene keeps a piece of it.

The moment stays open; the next move is yours.
  (verdict: Perception check vs dc 12: SUCCESS AT A COST (12 vs 12, margin 0) — it works, but a cost or complication must be narrated.)
{
  "turn": 12,
  "next_turn": 13,
  "location": {
    "id": "yard",
    "name": "The Salt Gate yard",
    "description_static": "Packed earth the colour of old bone, ringed by salt-crusted wall. Cart ruts run to the gatehouse; a rope well sits in the north corner.",
    "connections": [
      "gatehouse",
      "market",
      "well"
    ]
  },
  "hp": 11,
  "max_hp": 11,
  "currency": 6,
  "inventory": [
    {
      "item_id": "coil of rope",
      "qty": 1,
      "flags": {}
    },
    {
      "item_id": "gate token",
      "qty": 1,
      "flags": {
        "stamped": "salt-gate"
      }
    }
  ],
  "status_effects": [],
  "present_npcs": [
    {
      "id": "npc:1",
      "name": "Marla Quist",
      "alive": true,
      "disposition": 12.0
    }
  ],
  "leads": [
    {
      "id": 1,
      "title": "The missing salt shipment",
      "stage": "accepted"
    },
    {
      "id": 2,
      "title": "The gatehouse ledger",
      "stage": "rumored"
    },
    {
      "id": 3,
      "title": "What the well keeps",
      "stage": "unheard"
    }
  ],
  "pinned_facts": [
    "Marla's brother Dain is dead \u2014 drowned at the Salt Gate three winters ago."
  ]
}
(campaign saved)
```
