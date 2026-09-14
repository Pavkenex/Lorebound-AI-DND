# Engine architecture & build contracts (rebuild 2026-09-13)

**Authority:** `../docs/REBUILD_SPEC.md` §N is the build spec. This file maps
modules to spec sections, freezes the cross-module contracts, and defines the
environment, ownership, and gates for the rebuild worker chain. If card text
and this file disagree, this file + the spec win; surface any discrepancy in
your completion notes instead of silently diverging.

## Locked layout

```
engine/
  pyproject.toml      — pythonpath=src, pytest, ruff (FROZEN)
  src/engine/
    models.py          — cross-module dataclasses/enums (FROZEN contract)      [§2,§5]
    config.py          — EngineConfig, BudgetConfig, defaults                 [§4]
    similarity.py      — Similarity protocol + lexical/embedding impls        [§3.1]
    store/             — schema.sql + db access + migrations + generic row API[§2]
    resolve.py         — mechanics resolver / Pass A: dice, checks, bands     [§5A,§6]
    validate.py        — state validator: legality, clamps, rules             [§6]
    memory.py          — NPC memory+salience, chronicle, relationships,
                         moods, saga digest, world facts                      [§3]
    providers/         — BYOK adapters + capability probe + JSON fallback    [§7]
    context.py         — context assembly + budget controller                 [§4]
    pipeline.py        — turn orchestrator; Pass B/C/D                        [§1,§5]
    play.py            — play session + stub narrator + demo world            [§1,§10.1]
    cli.py             — `python -m engine …` playable surface                [§10.1]
  tests/               — pytest suite             (pythonpath=src handles imports)
  evals/               — eval harness + scenarios                             [§9]
```

## Frozen contracts

1. **`models.py`** holds every cross-module dataclass/enum. Import from there;
   do not redefine shapes locally.
2. **Pass B envelope.** The narrator (live adapter or stub) returns a
   `ProposalSet`: `narration: str`, `npc_dialogue: list[dict]`,
   `deltas: list[Delta]`. The model NEVER mutates state directly; code commits.
3. **Delta kinds and payloads** (`Delta.kind` → `target` → `data` keys):

   | kind | target | data keys |
   |---|---|---|
   | `mood` | npc_id | `valence_delta`, `arousal_delta`, `cause` |
   | `relationship` | npc_id | `category` (trust/affection/respect/fear/debt), `delta` (−40..+40 per event), `reason` |
   | `fact` | "" | `statement`, `tags` (list), `pinned` (bool, default False), `source` |
   | `lead_transition` | lead_id | `new_stage`, `justification` |
   | `inventory_add` | item_id | `qty`, `flags` (dict, optional) |
   | `inventory_remove` | item_id | `qty` |
   | `currency` | "" | `amount` (negative spends; conservation-checked) |
   | `hp` | character_id | `delta`, `cause` |
   | `stat` | character_id | `stat`, `delta` |
   | `status_add` / `status_remove` | character_id | `status`, `cause` (optional) |

4. **`store/__init__.py`** — the generic row API (`Store`), exact signatures in
   the file. Domain modules call it; nobody else opens sqlite directly.
5. **`schema.sql`** — the data contract (all tables, idempotent DDL).
6. **Providers**: adapters expose `complete(ChatRequest) -> ChatResponse` and
   `capabilities() -> ProviderCaps`. When native tool calls are unavailable,
   degrade to the JSON-in-text protocol (§7) using the same delta schema:
   strict parse → repair → regenerate-on-parse-failure, bounded.
7. **Context priority** (highest first): system/ruleset (cacheable) → scene
   state → this turn's mechanical outcome → pinned facts → NPC memory (present
   NPCs, top-K) → relevant leads → chronicle tail → saga digest. Over budget:
   drop from the bottom. NEVER drop the mechanical outcome or pinned facts.

## Decisions (locked; extend, never silently contradict)

- **Stdlib-only runtime.** Dev deps: `pytest`, `ruff`. HTTP via
  `urllib.request`. No third-party imports in `src/`.
- **SQLite via stdlib `sqlite3`**; WAL mode; `foreign_keys=ON`; single
  campaign per DB (spec assumes single-player).
- **Outcome bands** (continuity with the live game's rulings): nat-20 ⇒
  `CRITICAL` (unconditional success, never a cost); nat-1 ⇒
  `CRITICAL_FAILURE`; margin 0–2 for non-crits ⇒ `SUCCESS_AT_COST`; below ⇒
  `FAILURE`; else `SUCCESS`. Failure is "fail forward" in narration.
- **Salience defaults** (spec §3.1 formula, config-overridable):
  `w1=0.35` recency, `w2=0.25` |sentiment|, `w3=0.25` scene relevance,
  `w4=0.10` `log1p(reinforced)`; minus exp-decayed penalty by type class:
  factual ≈ 0 (near-permanent), promise 0.02, secret_shared 0.05,
  grievance/kindness/observed 0.15 (per-turn half-life style decay).
- **Mood**: transient valence/arousal pair with half-life decay (default 6
  turns), reset pressure from scene events; personality holds the static
  baseline (never mutated at runtime).
- **Relationships**: append-only ledger; current = base anchor + Σ decayed
  deltas; decay classes `durable` (0/turn), `slow` (0.01/turn), `fast`
  (0.08/turn).
- **Saga digest**: hierarchical `SESSION (~30 turns) → ARC → CAMPAIGN`; all
  levels archived (retrievable, never discarded); campaign injected by
  default; pinned facts referenced by id and EXEMPT from summarization; the
  summarizer is injectable — deterministic model-free builder by default,
  optional cheap LLM call via an adapter when configured.
- **Similarity**: `LexicalSimilarity` is the default; `EmbeddingSimilarity`
  activates only when an embed callable is supplied. Tests never need
  embeddings or network.
- **Stub narrator**: deterministic, template-based, same `ProposalSet`
  envelope as live models. Used by `--stub`, tests, and evals.
- **BYOK**: keys are runtime inputs (param or env name), never persisted;
  never log secrets. Tests make NO network calls (stdlib fake HTTP server).
- **Non-goals this round**: UI/frontend integration, multiplayer, live
  third-party API calls in CI, perf tuning.

## Ownership map (one writer per set)

| Card | Owns |
|---|---|
| R1 store | `src/engine/store/**`, `tests/test_store*.py` |
| R2 resolve+validate | `src/engine/resolve.py`, `src/engine/validate.py`, `tests/test_resolve*.py`, `tests/test_validate*.py` |
| R3 memory | `src/engine/memory.py`, `tests/test_memory*.py` |
| R4 providers | `src/engine/providers/**`, `tests/test_providers*.py` |
| R5 context | `src/engine/context.py`, `tests/test_context*.py` |
| R6 evals-core | `evals/**`, `tests/test_evals*.py` |
| R7 pipeline/play/cli | `src/engine/pipeline.py`, `src/engine/play.py`, `src/engine/cli.py`, `src/engine/fixtures/**`, `tests/test_pipeline*.py`, `tests/test_play*.py` |
| R8 evals-e2e | `evals/**` (adds e2e scenarios + runner), `tests/test_evals_e2e*.py` |
| R9 finalize | `README.md`, `ARCHITECTURE.md` (as-built notes), `../docs/REBUILD_NOTES.md`, small fixes anywhere in `engine/` |
| R10 verify | `../docs/REBUILD_VERIFICATION.md`, fix-forward commits |

**Orchestrator-frozen files** (edit only if REQUIRED; call it out loudly in
your completion notes): `models.py`, `config.py`, `similarity.py`,
`pyproject.toml`, this file, `schema.sql`.

## Environment

- REPO: `/opt/data/Lorebound-AI-DND` (branch `main`). Parallel cards work in
  git worktrees at `<repo>/.worktrees/<task-id>`; serial cards work in the
  main tree. CWD is set per card.
- venv: `/opt/data/Lorebound-AI-DND/engine/.venv` — exists in the MAIN tree
  only. From `engine/` inside your workspace run:
  - tests: `/opt/data/Lorebound-AI-DND/engine/.venv/bin/python -m pytest tests -q`
  - lint: `/opt/data/Lorebound-AI-DND/engine/.venv/bin/ruff check src tests`
- Commit small steps to your own branch/worktree. **NEVER push.** Never touch
  `backend/` or `frontend/`.

## Gates (every card)

Focused test module(s) first, then the FULL engine suite green; ruff clean.
Evidence = exact commands run + observed results + commit hashes in the
completion result. A result without real command output is not accepted.
