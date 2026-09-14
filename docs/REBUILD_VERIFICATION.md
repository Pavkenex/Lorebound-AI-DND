# Rebuild verification — clean-clone report (R10)

Independent verification of the rebuilt engine (`engine/`), performed from fresh
git clones of `git@github.com:Pavkenex/Lorebound-AI-DND.git` — not from the
in-place working tree. Written by the R10 worker (`t_b548e65a`); every command in
this document is copy-pasteable from a clone and was actually run.

What this report is: revision identity, the gate commands and their observed
results, spec-level mechanism spot checks (each with its command), the mutation
meta-check that proves those spot checks can fail, and the old app's untouched
status. What it is **not**: a live-provider test (no keys were used anywhere) and
no judgement of prose quality — see "Non-claims" below.

## 1. Revision verified

| ref | hash | role |
|---|---|---|
| R9 recorded pushed hash (`docs/REBUILD_NOTES.md` evidence log, R9 metadata) | `3ffef75236e871e473cdc8c373a4bf3f96c4022d` | what R10 was asked to verify |
| clone A `/opt/data/scratch/rb-verify` (`rev-parse HEAD`, `origin/main`) | `3ffef75236e871e473cdc8c373a4bf3f96c4022d` | R9's pushed engine, verified in §2–§6 |
| clone B `/opt/data/scratch/rb-verify-r10` (HEAD) | `3ab4d66656a9b0c2a957e59bb9fbae3a3f1d0fd2` | revision R10 re-verified after adding its probe scripts (see §7) |
| rebuild scaffold (baseline for "old app untouched") | `534c43ee80695092fd4c414a2a6ddf900edaa9ca` | engine/ created here |

Clone A asserted equal to the R9 hash: `git -C /opt/data/scratch/rb-verify
rev-parse HEAD` → `3ffef75236e871e473cdc8c373a4bf3f96c4022d`; `rev-parse
origin/main` → the same; `git status --short --branch` → clean.

## 2. Fresh-clone gates

Environment (both clones): `uv venv` → CPython 3.13.5, then `uv pip install pytest
ruff` — nothing else. Run from `<clone>/engine`:

| command | observed result (clone A; clone B repeats every row — see §8) |
|---|---|
| `.venv/bin/ruff check src tests evals` | `All checks passed!` (exit 0) |
| `.venv/bin/python -m pytest tests -o addopts="" -q` | `782 passed in 40.89s` (exit 0) |
| `.venv/bin/python -m pytest tests --collect-only` | `782 tests collected` = 782 ran, 0 skipped |
| `.venv/bin/python -m evals` | core suite `5/5`, `RESULT: OK` (exit 0) |
| `.venv/bin/python -m evals --suite e2e` | e2e suite `5/5`, `RESULT: OK` (exit 0) |
| `.venv/bin/python -m evals --suite all --json` | `ok: true`; core 5/5 (assertions 28/22/13/20/19), e2e 5/5 (assertions 36/23/20/18/22; `catch_rate` 1.0 on both bait scenarios; `e2e-model-matrix.matrix.equal = true`) |

Note `-o addopts=""`: `pyproject.toml` sets `-q`, which suppresses pytest's
summary line; the flag is only about seeing counts, not about relaxing anything.

## 3. Mechanism spot checks

Proof scripts (committed at `3ab4d66`, live next to the R9 transcript):

* `engine/evals/artifacts/r10_probe_mechanisms.py` — 14 probes / 76 checks. Drives
  the public engine surface against throwaway SQLite DBs and re-derives the
  spec's claims from raw rows + arithmetic; it never reads a test expectation.
* `engine/evals/artifacts/r10_probe_mutations.py` — the meta-check in §4.

Run all probes (clone A and clone B):

```
cd <clone>/engine
.venv/bin/python evals/artifacts/r10_probe_mechanisms.py      # 14 probes, 76 checks
```

Observed: every check prints `ok`; `RESULT: OK — all 14 mechanism probes passed`
(exit 0). Single-mechanism form: add `--only <substring of the probe name>`.

| # | mechanism | spec | command (from `<clone>/engine`) | observed |
|---|---|---|---|---|
| 1 | state store: WAL + foreign keys, 15 tables, idempotent DDL | §2 | `... --only probe_store` | 4/4 ok — `journal_mode=wal`, `foreign_keys=ON`, 15 tables, re-open is a no-op |
| 2 | NPC salience top-K, per-NPC scoping, lazy recompute, relevance term | §3.1 | `... --only probe_salience` | 7/7 ok — top-K == k; foreign NPC entries never leak; older → lower score; facts decay slower than an equally old grievance |
| 3 | chronicle tail window (12) + verbatim cap (3), FIFO, unbounded storage | §3.2 | `... --only probe_chronicle` | 4/4 ok |
| 4 | relationship ledger: magnitude decay-class rule, `anchor + Σ delta·e^(−rate·age)`, append-only reads | §3.5 | `... --only probe_ledger` | 5/5 ok — recomputation from raw rows reproduces `total` and every category meter |
| 5 | mood half-life decay (6 turns), baseline from the personality, ledger separation, over-range clamp | §3.7 | `... --only probe_moods` | 5/5 ok — 0.65 → 0.40 (1 half-life) → 0.275 (2); zero ledger rows written |
| 6 | contradiction scan (antonym flip) + auto-pin heuristics + validator/store agreement | §3.6 | `... --only probe_facts` | 5/5 ok |
| 7 | Pass A bands (nat-20/nat-1 unconditional, cost band at margin 0–2) + eligibility gates (absent/dead target → blocked, no roll) | §5A, §6 | `... --only probe_pass_a` | 11/11 ok |
| 8 | validator conservation (overdraft refused whole), stat/hp/relationship clamps, inventory conservation, fact rate limit | §6 | `... --only probe_validator` | 8/8 ok — `+100` stat clamps to `+27`; `+90` per-event clamps to `+40`; the meter clamps from the *decayed* slack |
| 9 | budget controller: mechanics + pinned facts never dropped, pressure drops chronicle/NPC memory, window respected, telemetry records it | §4 | `... --only probe_budget` | 5/5 ok |
| 10 | Pass D: dead-NPC speech caught → one regeneration → patch when regeneration re-offends | §5D, §8 | `... --only probe_pass_d` | 5/5 ok — bait text never ships; clean prose is not regenerated |
| 11 | lead state-machine gates (`unheard → resolved` refused, `unheard → rumored` accepted + history row) | §3.4 | `... --only probe_lead_gates` | 2/2 ok |
| 12 | degraded JSON-in-text chain (fenced extraction, repair note, bounded regeneration, unknown delta dropped with a note) | §5B, §7 | `... --only probe_json_protocol` | 7/7 ok |
| 13 | full offline loop: seeded determinism, one verdict line per turn, one row per turn in turn_log/telemetry/chronicle, rules-first intent classification | §1, §9, §10 | `... --only probe_offline_loop` | 6/6 ok |
| 14 | `decay_class` seam between validator and memory (recorded, not endorsed — see §7) | §3.5 | `... --only probe_decay_class_seam` | 2/2 ok — memory tags `durable` by magnitude; the validator writes `slow` |

## 4. Probe sensitivity (mutation meta-check)

A probe harness that only ever passes proves nothing. `r10_probe_mutations.py`
copies `src/` into a scratch tree, applies one targeted mutation per case to the
mechanism a probe claims to cover, and runs the probes against the mutated copy —
each case must exit 1 with `RESULT: FAIL`. The unmutated control copy runs first
and must exit 0.

```
cd <clone>/engine
.venv/bin/python evals/artifacts/r10_probe_mutations.py
```

Observed: `RESULT: OK — control clean, all 5 mutations detected` (exit 0).

| mutation | probe reaction (observed) |
|---|---|
| `resolve`: margin 1–2 no longer a cost band | `FAIL band success_at_cost (margin 0/1)` — roll 12/13 became plain `success` |
| `memory`: mood decay factor frozen at 1.0 | `FAIL one half-life decays half the offset…  got 0.65` |
| `memory`: ledger decay factor removed | `FAIL recomputing anchor + Σ(delta·e^(−rate·age)) reproduces 'total'` |
| `validate`: currency conservation removed | `FAIL an overdraft is refused outright… balance=-34` |
| `pipeline`: Pass D ignores dead-NPC speech | `FAIL a dead NPC speaking is caught and regenerated once` + the bait text reached the player |

## 5. Old app untouched + regression

| check | command | observed |
|---|---|---|
| backend/frontend bytes since the scaffold | `git -C <clone> diff --stat 534c43e..HEAD -- backend frontend` | empty (exit 0) |
| everything the rebuild touched | `git -C <clone> log --name-only --format= 534c43e..HEAD` | `engine/` 92 records, `docs/` 2 records, nothing else |
| rebuild size | `git -C <clone> diff --shortstat 534c43e..HEAD` | 70 files changed, +22 329 / −210 |
| old app still green | `cd <clone>/backend && /opt/data/Lorebound-AI-DND/backend/.venv/bin/python -m pytest tests -o addopts="" -p no:warnings -q` | `445 passed in 21.08s` (exit 0) |

## 6. Human surface (CLI smoke from the clone)

`PYTHONPATH=src .venv/bin/python -m engine play --stub --db <tmp>` driven with four
scripted inputs (see `reverify_log.txt` §7 for the raw run). Four turns, four
code-owned verdict lines:

```
(verdict: No mechanics resolve this dialogue turn; narration may proceed freely.)
(verdict: Investigation check vs dc 12: SUCCESS (19 vs 12, margin 7) — the action works as intended.)
(verdict: No mechanics resolve this exploration turn; narration may proceed freely.)
(verdict: No mechanics resolve this exploration turn; narration may proceed freely.)
```

The checked-in 12-turn transcript still matches its recorded digest:
`md5sum evals/artifacts/stub_play_transcript.md` →
`b3fe0d66a353ffeb119709bec2579ac7`.

## 7. Discrepancies, notes, resolutions

1. **`decay_class` derivation seam (open, documented — not introduced by R10).**
   `engine/src/engine/validate.py::_apply_relationship` (line ~1022) writes the
   delta's tagged class when it is one of `DECAY_CLASSES`, else
   `DEFAULT_DECAY_CLASS = "slow"`. `engine/src/engine/memory.py::append_delta`
   derives the class from magnitude (≥25 durable, ≥10 slow, else fast). Same
   −30 betrayal: memory path → `durable`, validator path → `slow`; `durable`/`fast`
   are unreachable through the validator, i.e. in real play. Probe 14 records this
   divergence explicitly (it asserts both halves), so it cannot drift silently.
   **Resolution: left open on purpose** — changing either side changes committed
   data, and I2 (`t_2e5797de`, comment #9) flagged it as a decision, not a bug
   fix. Also listed under "known gaps" in `docs/REBUILD_NOTES.md`.
2. **Card tooling vs. project `addopts`.** The card's suggested
   `pytest tests -q` is already `-q` via `pyproject.toml`; counts need
   `-o addopts=""`. Cosmetic, noted so the next reader is not surprised by a
   missing summary line.
3. **R10's own scripts are part of the verified revision.** The probes had to
   exist in a clone to be "copy-pasteable from the clone", so they were pushed
   (`3ab4d66`) and clone B re-ran every check at that revision: see §8. The
   engine tree is identical between `3ab4d66` and the commit that adds this
   document.
4. **Known gaps re-confirmed, unchanged** (full list in `docs/REBUILD_NOTES.md`):
   no live BYOK call anywhere; no travel/location delta kind (movement intents
   stay exploration and the player does not move); §9 craft axis (prose scoring)
   unimplemented; `python -m engine` needs `PYTHONPATH=src` (no editable install);
   the CLI `evals` subcommand is core-only.

## 8. Re-verification of the final revision (clone B)

Because R10's probe scripts must live in a clone to be copy-pasteable from one,
they were pushed (`3ab4d66`, R9's `3ffef75` + the two probe files) and every
check was re-run from a **second fresh clone** of that revision
(`/opt/data/scratch/rb-verify-r10`, `rev-parse HEAD` =
`3ab4d66656a9b0c2a957e59bb9fbae3a3f1d0fd2`; clone dir removed before cloning).
Fresh `uv venv` (CPython 3.13.5) + `uv pip install pytest ruff` (pytest 9.1.1,
ruff 0.16.7).

| command | clone B result |
|---|---|
| `.venv/bin/ruff check src tests evals` | `All checks passed!` (exit 0) |
| `.venv/bin/python -m pytest tests -o addopts="" -q` | `782 passed in 43.25s` (exit 0) |
| `.venv/bin/python -m pytest tests --collect-only` | `782 tests collected` |
| `.venv/bin/python -m evals` | core `5/5`, `RESULT: OK` |
| `.venv/bin/python -m evals --suite e2e` | e2e `5/5`, `RESULT: OK` |
| `.venv/bin/python -m evals --suite all --json` | `ok: true`, identical per-scenario assertion counts to clone A |
| `.venv/bin/python evals/artifacts/r10_probe_mechanisms.py` | `RESULT: OK — all 14 mechanism probes passed` (76/76 checks) |
| `.venv/bin/python evals/artifacts/r10_probe_mutations.py` | `RESULT: OK — control clean, all 5 mutations detected` |
| `md5sum evals/artifacts/stub_play_transcript.md` | `b3fe0d66a353ffeb119709bec2579ac7` (matches R9's recorded digest) |
| `git diff --stat 534c43e..HEAD -- backend frontend` | empty; `--shortstat` → 72 files, +23 176 / −210 |
| `cd backend && <main>/backend/.venv/bin/python -m pytest tests -o addopts="" -p no:warnings -q` | `445 passed in 20.90s` |
| CLI smoke (`play --stub`, 4 scripted inputs) | 4 turns, 4 verdict lines — identical text to §6 |

Raw log: `/opt/data/scratch/r10/reverify_log.txt` (scratch, not committed). The
commit that adds this document contains only `docs/` changes, so the engine
verified above is exactly the engine shipped at the document's commit:
`git diff --stat 3ab4d66..<doc commit> -- engine` must be empty.

## 9. Non-claims

* No live provider request was ever made — all provider paths were exercised
  through the stdlib fake server / scripted stub transports. BYOK behaviour
  against a real endpoint is untested.
* Prose quality was not judged: the `§9` craft axis is unimplemented and the stub
  narrator emits templates. Nothing here validates narration quality.
* Determinism is proven for the seeded RNG + stub narrator only.
* The probes exercise the documented public surface; they are spot checks, not a
  substitute for the 782-test suite (which ran in full, per §2).
