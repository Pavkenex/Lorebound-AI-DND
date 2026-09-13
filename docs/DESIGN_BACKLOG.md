# Lorebound — design backlog (rulings + open items)

_2026-09-13. Written down per Matija's "remember all these" after the seven
suggestions were reviewed point-by-point. Nothing here is scheduled; items
move to kanban cards when picked up._

## The seven suggestions & rulings

| # | Item | Ruling |
|---|------|--------|
| 1 | **Relationship-gated content** — warmth unlocks NPC-initiated offers (personal favors at Warm, stick-their-neck-out at Bonded) | OPEN — question raised: "aren't they already more open based on relationship?" See §1 below. |
| 2 | **Promises become oaths** — journal Oaths section, soft windows; kept → warmth + treasured memory; lapsed → trust damage they open with | AGREED — next up. |
| 3 | **Downtime & training** — town hours, trainers, novelty decay; turns already-written systems into a second loop | PARKED — needs a more detailed, campaign-agnostic design (characters differ per campaign; handle generally). |
| 4 | **Story-so-far digest** — rolling ~200-word recap carried in the prompt | BUILT 2026-09-13 (`d57e504`) — deterministic, model-free, campaign-agnostic; see §4 below. |
| 5 | **Fail forward everywhere** — extend success-at-cost / hail-mary to stealth/investigation/combat; crit-fail opens a branch | AGREED — next up. |
| 6 | **Nightly self-play ritual** — golden-chronicle transcripts + soak bot against the real relay | PARKED — "some other time" (token budget). |
| 7 | **Cheap delights** — Inspiration; saga export PDF/EPUB; tabbed mobile shell | PARTLY BUILT 2026-09-13 (`6131eb5`): Inspiration + mobile tabs live; saga export still open. |

## §1 — what the social mechanics do TODAY (checked in code)

- `relationship_credit()` (personality.py): attitude → DC credit, attitude×0.1 capped ±10; warmer = lower DC, colder = higher.
- `should_roll_social()` (rules/checks.py): a die is thrown **only** when the NPC is resistant, it's a manipulation attempt, or the outcome matters and is genuinely uncertain. Otherwise **role-play resolves it — no roll at all**.
- `rp_dc_shift()`: RP quality moves the DC ±2 more ("great RP lowers the DC").
- `_leverage_paid()` (play/engine.py): when the purse meets a character's price, **no die is thrown** — the §6 coin shortcut.
- nat-20 always critically succeeds; nat-1 always fails.

So: asks already tilt with relationship and can skip the dice with good play or
with coin. **What is missing is NPC-initiated content**: favors/quests that
only *open up* at Warm/Bonded (the "warm unlocks" half of suggestion 1).

## §4 — what carries continuity TODAY (checked in code)

- `world_facts` table exists (campaign/world.py) and the narrator prompt has an "[Established world facts]" slot — but `_pipeline_state()` feeds `"facts": []` (hardcoded stub): the slot is always empty in live play.
- The narrator actually gets: NPC memories (top-3 per NPC), the chronicle tail (last 8 entries, long lines elided head+tail), leads, and this action's events.
- NPC memories are per-character one-liners; nothing summarizes the campaign so far.

→ The digest idea is NOT covered by existing "world memories". If/when built, the empty `world_facts` slot is its natural home (§25 continuity).

**Built 2026-09-13** (`d57e504`, all green — 431 backend tests + ruff, live-verified):
`PlayState.saga`/`saga_at` + `story/saga.py` (pure builder: hero, location/day,
quest line from lead/clues/solution, top-3 bonds, top-3 salient memories; capped
~1100 chars). Refreshes on checkpoint beats (transition/progress) + every 12 idle
actions; rides every narrator prompt as `[Story so far]`; exposed on `/state` for
a future journal surface. No model call, no hardcoded names — works for any
campaign/cast.

## Free-model route — status 2026-09-13

- **muse-spark\* uses `/v1/responses`** (not `/chat/completions` — that wire 500s). Hermes knows this (`muse-spark` → `codex_responses` in `hermes_cli/models.py`).
- **`muse-spark-1.3-contributor-free`** (provider `opencode-zen`; key `OPENCODE_ZEN_API_KEY` in `/opt/data/.env`) — verified **$0**.
- **Flipped 2026-09-13 (user consented)**: default = `muse-spark-1.3-contributor-free`, fallback = `muse-spark-1.2-contributor-free` (also $0). `security.allow_data_training_tiers_noninteractive: true` set. Both watch-crons (`kanban-cleared-watch`, `spacesage-ui-watch`) pinned to the same model so nothing keeps using the paid snapshot.
- **Gate + standing rule:** `-contributor*` = Meta's data-training tier (Meta may train on prompts/completions). **Standing rule (Matija): never let API keys, credentials, or overly private material into muse prompts — secret handling stays server-side.**
- Non-contributor `muse-spark-1.3` = paid; account balance empty (401). Keyless free tier: only inside the OpenCode app (400 otherwise).
- Probes: `/opt/data/scratch/probe_muse{_responses,_final,_quality,_retry}.sh`, `probe_muse_12free.sh`.
- **Verified end-to-end 2026-09-13:** unattended one-shot ran a real tool call on muse (~12s), no refusal.

### Flip (done 2026-09-13)

```bash
hermes config set model.default muse-spark-1.3-contributor-free
hermes config set model.provider opencode-zen
hermes config set security.allow_data_training_tiers_noninteractive true
# + fallback repointed via /opt/data/scratch/set_fallback.py
```

Revert: `hermes config set model.default deepseek-v4.1-flash && hermes config set model.provider opencode-go`.

## §7 — cheap delights, build notes 2026-09-13 (`6131eb5`)

**Inspiration (table applause) — BUILT, all green.** No RP-quality scorer
exists in the codebase (the ±2 `rp_dc_shift` is wired but its input is always
0), so earning is progress-gated instead of judged: lead advanced, clue
found, route opened, travelers freed, tale closed, or a meter crossing into a
warmer band — capped at 3. Spending (`I spend my inspiration`, or the ✦ Spend
button) arms the next surfaced check with advantage: the prompt throws twice,
the engine keeps the higher (kept die drives crits, exactly like the table's
advantage), then burns the point win or lose. Hidden/trivial checks never burn
it. `roll_check(..., roll2=)` + `CheckResult.kept_from`; `ActIn.roll2` rides
the same path as the first face. Live prove-out: ask Marla → +1 → spend →
steal threw 5 + 19 → kept 19 (Exceptional), point burned.
**Mobile shell — BUILT.** ≤720px the adventure page becomes three tabs
(❧ Chronicle / ♥ Status / ◈ World); a called check
auto-brings the Chronicle pane so the die stays throwable with one thumb.
Desktop untouched. Verified via `next build` + SSR markup (`tabbar`,
`data-tab="tale"`); no browser on this box, so phone-pixel check is still owed.
**Still open:** saga export (PDF/EPUB at arc end).
