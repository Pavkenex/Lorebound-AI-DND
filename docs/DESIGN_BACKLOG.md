# Lorebound — design backlog (rulings + open items)

_2026-09-13. Written down per Matija's "remember all these" after the seven
suggestions were reviewed point-by-point. Nothing here is scheduled; items
move to kanban cards when picked up._

## The seven suggestions & rulings

| # | Item | Ruling |
|---|------|--------|
| 1 | **Relationship-gated content** — warmth unlocks NPC-initiated offers (personal favors at Warm, stick-their-neck-out at Bonded) | OPEN — question raised: "aren't they already more open based on relationship?" See §1 below. |
| 2 | **Promises become oaths** — journal Oaths section, soft windows; kept → warmth + treasured memory; lapsed → trust damage they open with | AGREED — build when the token/free-model situation is settled. |
| 3 | **Downtime & training** — town hours, trainers, novelty decay; turns already-written systems into a second loop | PARKED — needs a more detailed, campaign-agnostic design (characters differ per campaign; handle generally). |
| 4 | **Story-so-far digest** — rolling ~200-word recap carried in the prompt | OPEN — question raised: "isn't that what world memories are for?" See §4 below. |
| 5 | **Fail forward everywhere** — extend success-at-cost / hail-mary to stealth/investigation/combat; crit-fail opens a branch | AGREED — build when the token/free-model situation is settled. |
| 6 | **Nightly self-play ritual** — golden-chronicle transcripts + soak bot against the real relay | PARKED — "some other time" (token budget). |
| 7 | **Cheap delights** — rp_quality → Inspiration; saga export PDF/EPUB; tabbed mobile shell | UNSORTED — not yet ruled on. |

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

## Free-model route — status 2026-09-13

- **muse-spark\* uses `/v1/responses`** (not `/chat/completions` — that wire 500s). Hermes knows this (`muse-spark` → `codex_responses` in `hermes_cli/models.py`).
- **Works & bills $0: `muse-spark-1.3-contributor-free`** (provider `opencode-zen`; key `OPENCODE_ZEN_API_KEY`, now also in `/opt/data/.env`).
- **Gate:** `-contributor*` = Meta's data-training tier ("trains on your prompts and completions"; do not use for confidential data). Hermes refuses unattended use until `security.allow_data_training_tiers_noninteractive: true` is set — **user consent pending**.
- Non-contributor `muse-spark-1.3` = paid; account balance is empty (401). Keyless free tier: only inside the OpenCode app (400 otherwise).
- Probes: `/opt/data/scratch/probe_muse{_responses,_final,_quality,_retry}.sh`.

### To flip (once consented)

```bash
hermes config set model.default muse-spark-1.3-contributor-free
hermes config set model.provider opencode-zen
hermes config set security.allow_data_training_tiers_noninteractive true
```

Revert: `hermes config set model.default deepseek-v4.1-flash && hermes config set model.provider opencode-go`.
