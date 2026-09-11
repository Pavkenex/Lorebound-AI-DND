# Design Risk Register (GDD §121)

Each risk maps to at least one owning system. "Owning system" names the
backend bounded context or content area responsible for the mitigation.

| # | Risk | Mitigation | Owning system |
|---|---|---|---|
| 1 | Repetition (samey narration) | Memory + pacing beats + authored lore variety; session beats cap scene length | memory, story, content (Ravenford) |
| 2 | Contradiction (world denies itself) | World facts ledger + NPC knowledge scoping + proposal validation before writes | world, npc, narrator (authority) |
| 3 | Exploitation (players talk the AI into success) | Interpreter proposes / engine disposes; server-authoritative checks with schemas | rules, actions, narrator (authority) |
| 4 | Cost (AI bills scale with play) | Small models by default + retrieval over context + narration caching | ai, memory, core (config) |
| 5 | Direction loss (open world, no story) | Story Director + hidden truths + tracked story leads | story, journal, content (threads) |
| 6 | Too much reading (walls of text) | Short narration budget + concise mode; beats carry timing | narrator, frontend |
| 7 | Farmable progression (grinding skills) | Novelty-gated XP + difficulty gates + daily/capped limits | progression, rules |

## Coverage statement

All seven GDD §121 risks have an owning system above. Risks 1, 2, 5, and 7
are additionally covered by the vertical slice: Marla's memory (1, 2),
three intersecting threads with hidden truths (5), and skill-gated
multi-solution checks (7). Risks 3, 4, 6 are owned by engine/frontend cards
outside this stream; the playtest harness (`test_playtest.py`) asserts the
player-facing halves (no free successes, concise beats).
