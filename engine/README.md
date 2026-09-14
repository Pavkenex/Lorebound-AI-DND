# Lorebound Engine (rebuild)

The rebuilt game engine: an AI Dungeon Master system where the model narrates
and proposes, while code owns all state (build spec: `../docs/REBUILD_SPEC.md`,
cited below as `§N`).

Status: built by the rebuild worker chain (2026-09-13/14). As-built notes,
decisions, known gaps and the integration path for the existing app live in
`../docs/REBUILD_NOTES.md`; the clean-clone verification report is
`../docs/REBUILD_VERIFICATION.md` (written by the verify card, R10).

## Quickstart (no key, no network)

The runtime is **stdlib-only**; the only dev tools are `pytest` and `ruff`.
There is no editable install, so the `python -m engine …` forms need `src/` on
`PYTHONPATH`:

```bash
cd engine
python3 -m venv .venv                  # or: uv venv
.venv/bin/pip install pytest ruff      # dev tools only — runtime needs neither

# play offline with the deterministic stub narrator on a scratch campaign DB
PYTHONPATH=src .venv/bin/python -m engine play --stub --db /tmp/campaign.db
```

Inside the loop: any line is an action; `/state` prints the JSON snapshot,
`/help` lists commands, `/quit` saves and leaves (EOF saves too). Each turn
prints the narration, the `(verdict: …)` mechanics line the narration must
honor, and any narrator/consistency notes. A raw 12-turn session is checked in
at `evals/artifacts/stub_play_transcript.md`.

## Commands (from `engine/`)

```bash
# tests + lint (the venv lives in the MAIN repo tree; worktrees use the absolute path)
/opt/data/Lorebound-AI-DND/engine/.venv/bin/python -m pytest tests -q
/opt/data/Lorebound-AI-DND/engine/.venv/bin/ruff check src tests evals
# add -o addopts="" to pytest to see the "N passed" summary line (pyproject sets -q)

# play — stub is the default and needs no key; live models are BYOK (§7)
PYTHONPATH=src .venv/bin/python -m engine play --stub --db /tmp/campaign.db
PYTHONPATH=src .venv/bin/python -m engine play --db /tmp/campaign.db \
    --provider openai --model gpt-4o-mini --api-key-env OPENAI_API_KEY
PYTHONPATH=src .venv/bin/python -m engine state --db /tmp/campaign.db   # snapshot as JSON

# evals (§9) — evals/__init__.py puts src/ on sys.path, so this works as-is
.venv/bin/python -m evals                 # core suite (default)
.venv/bin/python -m evals --suite e2e     # pipeline-level e2e
.venv/bin/python -m evals --suite all     # both
.venv/bin/python -m evals --list          # names + descriptions
.venv/bin/python -m evals --json          # machine-readable report
.venv/bin/python -m evals --db-dir /tmp/dbs --seed 13   # keep the throwaway DBs
.venv/bin/python -m evals --scenario evals/scenarios/conservation.json
```

Each run prints a per-scenario PASS/FAIL list and a final `RESULT: OK` /
`RESULT: FAIL` line, and exits 1 when any scenario fails (0 otherwise).

## One turn, end to end (§1, §5)

Player input → rules-first intent classification → Pass A mechanics (dice,
checks, eligibility; pure code, no LLM) → context assembly under a hard token
budget → Pass B narrator (live adapter or stub) returning narration + dialogue
+ proposed state deltas → Pass C validation/commit (code clamps, rejects,
applies) → Pass D consistency scan against pinned facts and freshly committed
state, with one bounded regeneration or a targeted sentence patch. The model is
told the outcome; it never decides whether something worked.

The stub narrator reads the same assembled prompt a live model gets, so the
offline path exercises the real pipeline (§10.1).

## Bring your own key (BYOK, §7)

Keys are runtime inputs — named by env var (`--api-key-env VAR`) and read at
launch; they are never written to the DB, the logs, or the prompt. Provider
modes wrap four wire families (aliases in parentheses):

| `--provider` | wire | default base URL |
|---|---|---|
| `openai` (`openai_compat`, `openai-compatible`) | OpenAI chat/completions | `https://api.openai.com/v1` |
| `anthropic` (`claude`) | Anthropic messages | `https://api.anthropic.com` |
| `gemini` (`google`) | Gemini generateContent | `https://generativelanguage.googleapis.com` |
| `local` | any OpenAI-compatible server | none — `--base-url` is required |

On first use an adapter runs a one-call capability canary and caches the
verdict (native tool calls vs JSON-in-text) per key+model in the DB. Capability
detection never raises: a failed probe costs prose quality, never the game.
Models without reliable tool calling degrade to the strict JSON envelope
(`providers/jsonproto.py`: extract → repair → bounded regeneration on parse
failure). Tuning knobs — context window, prompt-budget split, salience weights,
memory windows, mood half-life, fact rate limit — are plain dataclasses in
`config.py`, passed per launch.

## Layout

```
engine/
  pyproject.toml      — pythonpath=src, pytest, ruff (frozen)
  ARCHITECTURE.md     — module map, locked contracts, decisions, gates
  src/engine/         — the package (stdlib-only)
    models.py         — cross-module dataclasses/enums (frozen)      [§2,§5]
    config.py         — EngineConfig / BudgetConfig / defaults       [§4]
    similarity.py     — Similarity protocol + lexical/embedding impl [§3.1]
    store/            — schema.sql + SQLite access + row API         [§2]
    resolve.py        — Pass A mechanics: dice, checks, bands        [§5A,§6]
    validate.py       — Pass C validator: legality, clamps, commit   [§6]
    memory.py         — NPC memory, chronicle, saga, leads, ledger,
                        moods, world facts + MemoryBundle            [§3]
    providers/        — BYOK adapters, capability probe, JSON codec  [§7]
    context.py        — context assembly + budget controller         [§4]
    pipeline.py       — turn orchestrator; Pass B/C/D                [§1,§5]
    play.py           — play session + stub narrator                 [§1,§10.1]
    cli.py, __main__.py — `python -m engine …` playable surface      [§10.1]
    fixtures/         — demo world + fixture seeding
  tests/              — pytest suite (782 tests; `pythonpath=src` handles imports)
  evals/              — eval harness + scenarios (core `scenarios/`, e2e `scenarios/e2e/`)
    e2e.py            — pipeline-level e2e runner (§9)
    artifacts/        — checked-in evidence output (stub-play transcript)
```

`ARCHITECTURE.md` is the authoritative module map and contract list; where this
README and that file disagree, `ARCHITECTURE.md` + the spec win — report the
discrepancy instead of guessing.
