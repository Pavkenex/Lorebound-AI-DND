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

---

## As-built notes (R9, 2026-09-14)

Everything in "Locked layout", "Frozen contracts" and "Decisions" above was
delivered as written; no declared module, class or method was dropped. This
section records what the as-built code added *on top of* the scaffold stubs
(commit `534c43e`, whose bodies raised `NotImplementedError("RN card implements
…")` behind the declared signatures), so a reader can separate the intended
contract from the delivered surface. Evidence for each item is a signature
comparison of `534c43e` vs this tree.

### Deviations from the scaffold stubs (explicit list)

1. `play.PlaySession.start(*, db_path, world, config, narrator)` → adds
   `rng, adapter, provider, auto_seed, probe` (live BYOK narrator, resume an
   existing campaign, test injection).
2. `play.StubNarrator` — the stub had only `narrate`; as-built adds
   `__init__(script=…)`, `calls`, `last_notes`, `stats()` and a prompt-reading
   template narrator with turn-indexed phrasing. `demo_world()` moved its
   content to `fixtures/demo_world.py` (the stub docstring anticipated this)
   and `seed_world()` was added next to it. The narrator still emits no deltas
   by default and invents no dialogue.
3. `pipeline.Orchestrator.__init__` → adds `components, assembler, adapter,
   provider, context_window, effect_rules, ruleset, dc, now` (eval/test
   injection seams, live-adapter wiring, injectable clock).
4. `pipeline.Orchestrator.consistency_check(*, turn, narration, report)` → adds
   `dialogue=`: Pass D scans NPC dialogue as well as prose (the
   dead-NPC-speech contradiction class).
5. `pipeline` gains `LiveNarrator` + `narrator_from_adapter()` — the adapter →
   `NarratorRunner` bridge (prompt/telemetry plumbing, probe once per session).
6. `context.ContextAssembler.__init__` → adds `components=` (accept a
   `MemoryBundle` instead of always building one). Section list, priority
   order, drop-from-the-bottom and drop accounting are as contracted.
7. `resolve.resolve_check(req, *, rng)` → adds `modifier=`;
   `resolve.resolve_action(intent, *, rng, store, turn)` → adds `dc=` and
   `effect_rules=` (per-intent effect hooks). Outcome bands are unchanged.
8. `memory.MemoryBundle` — the R1/I1 `context↔memory` seam shipped as a stub
   (`b2be4a8`); the as-built `MemoryBundle.build(store, config, sim)` is real,
   and `ContextAssembler` consumes exactly that surface.
   `MoodTracker.ensure()` takes `None` baselines (derive from personality)
   instead of `0.0`.
9. `validate.Validator.__init__(store, config)` → adds `sim=`, `stat_bounds=`,
   `policy=`; contradiction inspection is exposed as
   `Validator.contradiction_details()` rather than only being used inside
   `commit()`.
10. `providers/jsonproto.proposals_from_tool_calls(tool_calls)` → takes
    optional `narration=` / `notes=`; adds `proposals_from_payload()`,
    `parse_with_repair()` and `should_regenerate()` (the bounded repair step).
    `providers/registry.build_adapter()` is joined by the canary schema and the
    caps-cache read/write helpers.
11. `evals.harness.run_e2e` — the scaffold stub ("implemented by R8") is now a
    real delegation to `evals/e2e.py` with the same parameters as
    `run_scenarios` (`paths, root, seed, db_dir, narrator`); `run_scenarios`
    gains `root=`. `run_all()` discovery is untouched (5 core scenarios).
12. `cli.py` — the scaffold's single-def stub is the full `play`/`state`/`evals`
    CLI with exit codes 0/1/2 and key hygiene; R9 added the per-turn
    `(verdict: …)` line (the code-owned mechanics statement) to the play render.
13. `store` — the public row API is unchanged; internals were added
    (`_quote`, `_require_id_column`, prepared-parameter binding) and
    `transaction()` now uses `BEGIN IMMEDIATE` (`648f407`).
14. `schema.sql` (frozen) — one line changed: `saga_levels."references"` is
    quoted (`e13ec08`). No table, column or type was added, removed or retyped.
15. `models.py`, `config.py`, `similarity.py`, `pyproject.toml` — **no changes
    since the scaffold** (verified by an empty
    `git diff 534c43e..HEAD -- engine/src/engine/{models,config,similarity}.py
    engine/pyproject.toml`).
16. **Phase 2 (P8, app plan §11) — content boundaries on the engine path.**
    `play.PlaySession.start` and `pipeline.Orchestrator.__init__` gain
    `content_policy=` (keyword-only, default empty); `Orchestrator._scene()`
    carries it as `scene["content_policy"]`, and `context.ContextAssembler`
    appends it to the system message **after** the ruleset — never truncated by
    the system ceiling, never dropped under budget pressure, counted as its own
    `accounting["sections"]["content_policy"]` row. Empty input leaves every
    prompt byte-for-byte unchanged (the pre-P8 baseline). The directive string
    is opaque to the engine; the app renders it with
    `narrator.prefs.ContentPrefs.describe_for_prompt()`. Tests:
    `tests/test_content_policy.py`; the app-side seam is documented in
    `../docs/INTEGRATION_NOTES.md` §10.

### Final module inventory

Source (stdlib-only, `src/engine/`, 8,259 lines incl. schema):

| module | lines | role | spec |
|---|---|---|---|
| `models.py` | 467 | cross-module dataclasses/enums (frozen) | §2,§5 |
| `config.py` | 69 | `EngineConfig`/`BudgetConfig`/`SalienceWeights`/`MemoryConfig` | §3,§4 |
| `similarity.py` | 66 | `Similarity` protocol, lexical + embedding impls | §3.1 |
| `store/__init__.py` + `schema.sql` | 385 + 155 | SQLite access, migrations, 15 tables, generic row API | §2 |
| `resolve.py` | 413 | Pass A: dice expressions, checks, bands, eligibility | §5A,§6 |
| `validate.py` | 1210 | Pass C: clamps, legality, conservation, contradiction, commit | §6 |
| `memory.py` | 1132 | NPC memory + salience, chronicle, saga, leads, ledger, moods, facts, `MemoryBundle` | §3 |
| `providers/` (8 modules) | 1522 | transport policy, OpenAI/Anthropic/Gemini adapters, registry + canary probe, JSON codec | §7 |
| `context.py` | 718 | context assembly, priority order, budget controller, drop accounting | §4 |
| `pipeline.py` | 1172 | turn orchestrator, Pass B/C/D, consistency + regeneration/patch | §1,§5 |
| `play.py` | 339 | `PlaySession`, stub narrator, demo world accessors | §1,§10.1 |
| `cli.py` + `__main__.py` | 244 | `python -m engine play/state/evals` | §10.1 |
| `fixtures/` | 360 | demo world + fixture seeding (same row shape as the eval fixtures) | §10 |

Evals (`evals/`, 4,616 lines): `harness.py` (core DSL/runner), `e2e.py`
(pipeline-level runner: scripted transports, snapshots, bait/catch-rate,
matrix), `__main__.py` (suite CLI), 5 core scenarios + 5 e2e scenarios, and
`artifacts/` (checked-in stub-play transcript).

Tests (`tests/`, 10,431 lines across 34 files — 33 test modules plus
`doubles.py`; `test_providers_server.py` is the stdlib fake HTTP provider
server): 782 tests, all passing, none skipped.

For gate numbers, eval counts, per-mechanism evidence and known gaps see
`../docs/REBUILD_NOTES.md`; the clean-clone verification is
`../docs/REBUILD_VERIFICATION.md`.
