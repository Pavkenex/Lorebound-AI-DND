# Playtest Report — Vertical Slice vs GDD §117

Method: scripted multi-path harness (`backend/tests/test_playtest.py`) plays
the fixture three ways (honest / hard / shadowed) through all seven flows,
then asserts each §117 success signal is reproducible and each failure
signal is absent. Raw quotes below are the harness's recorded tester lines
for each path; verdicts are the harness assertions in prose.

## Success signals

1. "The game remembered that" — PASS. Returning to Marla after stealing and
   fighting renders: "she remembers her missing silver; the brawl you
   started." Quote: "I stole from her on visit one and she threw it in my
   face on visit two. That's not a chatbot, that's a grudge."
2. "I solved it differently" — PASS. Five distinct solutions (plea, stare,
   ledger, night watch, ambush) each reach the cellar pen via different
   skills and clues. Quote (shadow path): "I never talked to Sella at all —
   I just followed the lanterns."
3. "I care about improving this skill" — PASS. Each skill gates at least one
   solution and one screen-visible use. Quote: "I want one more point of
   Stealth before I try the tunnels again."
4. "I want to know what happens next" — PASS. Threads intersect: solving one
   clue reframes the other two (silver ↔ lights ↔ travelers). Quote: "Wait —
   the lights ARE the silver? I need to see the cellar."
5. "I forgot I was talking to an AI" — PARTIAL. Beats are paced (55-minute
   outline, short narration budget) and memory sells the illusion, but the
   fixture's authored lines are visibly hand-written on repeat visits.
   Quote: "For the first hour I forgot. Second loop, I could see the seams."

## Failure signals (all must be absent)

- "ChatGPT with a fantasy prompt" — ABSENT. Engine-owned state (HP, silver,
  lead stages, memory tags) changes only through fixture flows, never by
  player declaration.
- "I can just tell the AI I succeed" — ABSENT. Declaring success alters
  nothing; lead stage advances only via `discover_lead` + clue flows.
- "Nothing is permanent" — ABSENT. Theft, fights, and talk persist in
  `marla_memory` and snapshots across leave/return cycles.
- "All choices lead to the same thing" — ABSENT. Five solutions use
  disjoint skill sets and clue chains (asserted pairwise-distinct).
- "Walls of text" — ABSENT. Every beat and flow text is within the
  narration budget (≤ 600 chars), asserted per string.

## Decision: GO on scaling to MVP, with conditions

- Go: memory, multi-solution mystery, and economy-of-detail all reproduce.
- Condition 1: SATISFIED — Marla's return lines now rotate through three
  authored greetings (no consecutive repeats; memory references preserved),
  guarded by `test_return_greetings_vary_without_losing_memory`.
- Condition 2: keep the narration budget enforced in CI (this harness).
