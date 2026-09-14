# Rebuild plan & board map — engine v0.1 (2026-09-14)

Source of truth: the user's `AI Dungeon Master System` spec, stored verbatim at `docs/REBUILD_SPEC.md`.
This file is the operational map of the rebuild card graph on kanban board `default`.

## Flow
```
R1 ∥ R2  →  I1  →  R3 ∥ R4 ∥ R5 ∥ R6  →  I2  →  R7  →  R8  →  R9  →  R10
```
Worktree workers build on branches `eng/*` (never push). Integrators (I1/I2) merge branches to `main`.
Serial cards (R7–R10) work directly on `main`. The dispatcher spawns ready+assigned cards every tick.

## Cards
| id | card | workspace | branch | parents |
|----|------|-----------|--------|---------|
| t_3f01b3b8 | [Rebuild R1] State store — schema, migrations, generic row API | worktree | eng/store | — |
| t_285f0f47 | [Rebuild R2] Mechanics resolver + state validator | worktree | eng/resolve | — |
| t_b9c3aa8f | [Rebuild I1] Integrate core modules into main | dir | — | t_3f01b3b8, t_285f0f47 |
| t_a2dea257 | [Rebuild R3] Memory subsystem (§3 full) | worktree | eng/memory | t_b9c3aa8f |
| t_a0353077 | [Rebuild R4] Provider adapters + capability probe + JSON fallback | worktree | eng/providers | t_b9c3aa8f |
| t_955373bf | [Rebuild R5] Context assembly + budget controller | worktree | eng/context | t_b9c3aa8f |
| t_6d14088b | [Rebuild R6] Eval harness + core scenarios | worktree | eng/evals | t_b9c3aa8f |
| t_2e5797de | [Rebuild I2] Integrate stage 2 into main | dir | — | t_a2dea257, t_a0353077, t_955373bf, t_6d14088b |
| t_1f9d752e | [Rebuild R7] Pipeline (Pass A–D) + play session + CLI | dir | — | t_2e5797de |
| t_85dd70bc | [Rebuild R8] E2E evals — permanence, contradiction battery, model matrix | dir | — | t_1f9d752e |
| t_da7dddc6 | [Rebuild R9] Finalize — docs, transcript, gates, push | dir | — | t_85dd70bc |
| t_b548e65a | [Rebuild R10] Verify from clean clone | dir | — | t_da7dddc6 |

## Gates (every card)
- Focused tests first, then the FULL engine suite green; `ruff check src tests` clean.
- Completion result must carry evidence: files, exact commands, observed results, commit hashes.

## Night watch
- Cron `rebuild-watch` (every 20m, monitor `rebuild_watch_monitor.py`) — wakes on state change:
  `BLOCKED` → recover (unblock/reassign with guidance), `STUCK` → reclaim, `CLEARED` → morning report to Telegram.
- Non-actionable states are silenced (`[SILENT]`); the user is not pinged unless something needs eyes or the run finishes.

## Definition of done
- R10 verified from a clean clone at the pushed hash; docs `REBUILD_NOTES.md` + `REBUILD_VERIFICATION.md` present;
  engine suite + eval suites green from a pristine checkout; `backend/` and `frontend/` untouched.
