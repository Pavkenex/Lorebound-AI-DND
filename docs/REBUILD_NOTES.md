# Rebuild as-built notes — engine v0.1

Finalize card **R9**, 2026-09-14. Board: `default` (card graph in
`docs/REBUILD_PLAN.md`). Build spec: `docs/REBUILD_SPEC.md` (cited as `§N`).
Contracts and the module map: `engine/ARCHITECTURE.md` ("As-built notes"
section has the deviations-from-stubs list and the final inventory). Clean-clone
verification: `docs/REBUILD_VERIFICATION.md` (R10).

Scope of the rebuild: `engine/` only. `backend/` and `frontend/` (the live app)
were not touched — see "Regression" below.

## 1. Outcome and gates (evidence)

Everything below was run on the main tree at the commits listed in the evidence
log (§6). Gate commands are reproducible from `engine/`:

| gate | command | observed result |
|---|---|---|
| engine tests | `.venv/bin/python -m pytest tests -o addopts="" -q` | **782 passed** in 41.75s (exit 0) |
| no silent skips | `-v` run cross-checked against `--collect-only` | 782 collected = 782 ran, 0 skipped, 0 non-PASSED |
| lint | `.venv/bin/ruff check src tests evals` | `All checks passed!` (exit 0) |
| evals — core (§9) | `.venv/bin/python -m evals` | 5/5 scenarios, `RESULT: OK` (exit 0) |
| evals — e2e (§9) | `.venv/bin/python -m evals --suite e2e` | 5/5 scenarios, `RESULT: OK` (exit 0) |
| evals — machine report | `.venv/bin/python -m evals --suite all --json` | `{"ok": true, "suites": {core, e2e}}`, exit 0 |
| old app regression | `cd backend && .venv/bin/python -m pytest tests -o addopts="" -p no:warnings -q` | **445 passed** in 21.50s |
| old app untouched | `git diff --stat 534c43e..HEAD -- backend frontend` | empty (only `docs/` + `engine/` changed: 67 files, +21,694/−192) |
| stub-play transcript | `evals/artifacts/stub_play_transcript.md` | 12 turns, 12 verdict lines, ends with the `state_view` JSON; md5 `b3fe0d66a353ffeb119709bec2579ac7` (reruns byte-identical) |

Eval detail from the `--json` report: core — `boundary-clamps` 28 assertions,
`conservation` 22, `contradiction-bait` 13, `dead-npc-lock` 20, `lead-gates` 19.
e2e — `contradiction-battery` 36 assertions with `catch_rate` 1.0,
`death-permanence` 23 / `catch_rate` 1.0, `memory-retrieval` 20,
`model-matrix` 18 with `matrix.equal: true` across native/flaky/degraded
sessions (10 tables + turn/prompt digests compared), `telemetry-budget` 22
(window 1200 = 960 + 240 reserve, 2 telemetry rows with
`prompt_tokens == used`).

## 2. Spec §-coverage (mechanism → module → evidence)

Test ids are from `tests/`; eval scenarios from `evals/scenarios/` (core) and
`evals/scenarios/e2e/`. All of them are green in the gate runs above.

| § | mechanism | module | evidence |
|---|---|---|---|
| §1 | turn lifecycle, intent classification (rules-first: dialogue/action/exploration/meta, target resolution from the store) | `pipeline.py` | `test_pipeline.py::test_take_turn_writes_turn_log_telemetry_and_chronicle`; `test_classify_intent_rules[...]` |
| §2 | state store: 15 tables, idempotent DDL, migrations, generic row API, WAL + `foreign_keys=ON`, `BEGIN IMMEDIATE` transactions | `store/` | `test_store.py`, `test_store_roundtrip.py`; `test_contract_smoke.py::test_store_surface` |
| §3.1 | NPC memory + salience (recency/sentiment/relevance/reinforcement − decay-by-type), top-K **per present NPC** | `memory.py` + `context.py` | `test_memory.py::test_salience_ordering_recent_promise_vs_stale_grievance_vs_fact`, `::test_reinforcement_raises_salience`; `test_context.py::test_npc_memory_respects_top_k_and_salience_order`; e2e `memory-retrieval` |
| §3.2 | chronicle tail: 12 structured, last 3 verbatim, FIFO | `memory.py` | `test_memory.py::test_chronicle_tail_is_the_newest_window_newest_last` |
| §3.3 | saga digest: SESSION (~30 turns) → ARC → CAMPAIGN, archived levels, pinned facts by id, injectable summarizer | `memory.py` | `test_memory_saga.py::test_session_summary_only_at_the_cadence`, `::test_campaign_level_appears_only_once_an_arc_closes`; `test_context_budget.py::test_saga_detail_is_dropped_before_the_campaign_digest` |
| §3.4 | leads state machine, transitions validated in code (no skipping to resolved) | `validate.py` + `memory.py` | `test_validate.py::test_lead_transition_machine_is_exhaustively_enforced[...]`; core eval `lead-gates` |
| §3.5 | relationship ledger: append-only, current = anchor + Σ decayed deltas (durable 0 / slow 0.01 / fast 0.08 per turn) | `memory.py` + `validate.py` | `test_memory_relations.py::test_currents_anchor_at_disposition_base_and_decay_per_class`, `::test_old_grievance_decays_but_the_relationship_history_remains` |
| §3.6 | world/pinned facts, auto-pin-worthy statements, contradiction checks (`contradicts[]` analogue: marker + anchor heuristics) | `memory.py` + `validate.py` | `test_memory_facts.py`, `test_validate_contradiction.py::test_contradiction_scan_flags_antonym_flips`; core eval `contradiction-bait`; e2e `contradiction-battery` |
| §3.7 | moods: transient valence/arousal, half-life decay toward the personality baseline, never coupled to the ledger | `memory.py` + `validate.py` | `test_memory_relations.py::test_mood_half_life_is_configurable`, `::test_mood_spike_does_not_move_the_long_term_relationship` |
| §4 | context assembly + budget controller: fixed priority order, drop-from-the-bottom, never drop mechanics/pinned facts, per-section accounting | `context.py` | `test_context_budget.py::test_extreme_pressure_keeps_the_outcome_and_the_pinned_facts`, `::test_lower_salience_npc_memory_is_dropped_first`; e2e `telemetry-budget` |
| §5A | Pass A mechanics in pure code: dice expressions, skill checks, outcome bands (nat-20 CRITICAL, nat-1 CRITICAL_FAILURE, margin 0–2 SUCCESS_AT_COST, else SUCCESS/FAILURE), eligibility blocks (absent/dead target) | `resolve.py` | `test_resolve.py::test_resolve_check_bands[...]`; `test_resolve_action.py::test_absent_target_blocks_the_action`, `::test_dead_target_blocks_the_action_without_rolling` |
| §5B | Pass B narrator envelope (`narration`, `npc_dialogue`, `deltas`) via native tool calling, degrading to the JSON-in-text protocol | `pipeline.py` + `providers/` | `test_providers_jsonproto.py`, `test_pipeline.py`; e2e `model-matrix` (native/flaky/degraded) |
| §5C | Pass C validator commit: reject/clamp illegal deltas, commit legal ones, TurnLog + telemetry | `validate.py` | `test_validate_commit.py`; `test_integration_core.py::test_resolver_effects_commit_through_the_validator` |
| §5D | Pass D consistency scan (dead NPC speech, pinned-fact antonym/numeric/negation flips, rejected restatements) → one bounded regeneration, else targeted sentence/dialogue patch | `pipeline.py` | `test_pipeline_consistency.py::test_dead_npc_speech_triggers_exactly_one_regeneration`, `::test_regeneration_never_happens_twice`, `::test_regeneration_does_not_double_apply_deltas`; e2e `contradiction-battery` (`catch_rate` 1.0) |
| §6 | validator rules: ±100 relationship clamp, stat floors/ceilings, resource conservation vs the store, death/permanence, fact rate limit (`fact_rate_limit_per_turn`), lead legality | `validate.py` | `test_validate.py::test_currency_overdraft_is_rejected_and_balance_unchanged`; core evals `conservation`, `boundary-clamps`; e2e `death-permanence` |
| §7 | BYOK adapters (OpenAI-compatible / Anthropic / Gemini), one-call capability canary cached per key+model, JSON fallback with bounded repair + regeneration | `providers/` | `test_providers_registry.py::test_probe_reports_degraded_when_no_tool_call_comes_back`, `::test_probe_never_raises_on_transport_failure`; `test_providers_server.py` (stdlib fake wire); e2e `model-matrix` |
| §8 | anti-hallucination safeguards (the mechanisms above, composed) | cross-module | e2e `contradiction-battery` 9/9 baits caught + shipped clean; `death-permanence` (dead NPC never speaks or moves); `test_pipeline_consistency.py` |
| §9 | eval harness: scenario DSL, throwaway SQLite per scenario, telemetry per step, bait/catch-rate, model matrix, plus **meta-tests that prove the runner fails on wrong expectations/broken checks/missed baits/leftover scripts** | `evals/` | `test_evals_harness.py`, `test_evals_core_scenarios.py`, `test_evals_e2e.py` (32 tests, incl. source-mutation probes) |
| §10 | build order pay-off: playable with stubbed narration before any model call; CLI surface; fixture world | `play.py`, `fixtures/`, `cli.py` | `test_play.py`, `test_play_cli.py::test_play_runs_offline_and_saves`, `::test_play_prints_the_mechanics_verdict_line`; `evals/artifacts/stub_play_transcript.md` |

Coverage summary: all ten spec sections have code + executable evidence. The
gaps that remain are listed explicitly in §4 — none of them is a missing
mechanism from §0–§10; they are seams and un-exercised paths.

## 3. Decisions (as-built, beyond the locked list in `ARCHITECTURE.md`)

1. **Two eval suites, one report shape.** Core step-DSL scenarios stay directly
   under `evals/scenarios/`; the pipeline-level e2e layer lives in
   `evals/scenarios/e2e/` + `evals/e2e.py` and is selected with
   `--suite e2e|all`. `run_all()` discovery is untouched (exactly the 5 core
   scenarios), so the landed core contract did not move.
2. **e2e scenario files declare `E2E` (not `SCENARIO`)**, and the e2e-only check
   builtins (`round`, `sqrt`) are scoped to the e2e namespace so core check
   semantics stay byte-identical.
3. **Bait turns are first-class.** An e2e turn marked `bait` must be detected,
   regenerated (or patched) and shipped clean; `catch_rate` is computed from
   those turns and is a snapshot field a reviewer reads first.
4. **Degraded (tool-less) retry is model-layer, not Pass D.** It is 1 narrate
   call / 2 adapter requests with `parse_failures = 1` and pipeline
   `regenerations = 0`; Pass-D regeneration is the separate pipeline counter.
   (`test_evals_e2e.py` pins the difference.)
5. **The stub narrator reads the assembled prompt, never the store**, so the
   offline path exercises exactly the surface a live narrator gets (spec
   §0 "weakest reasonable model" thinking). It invents no dialogue: a template
   cannot know who may speak, and guessing is how a dead NPC gets a line.
6. **The fixture row shape is shared** between `engine.fixtures` and the eval
   harness (`_FIXTURE_MODELS`); a test asserts the two mappings match, so a
   fixture can never drift from the scenario loader.
7. **Determinism is a gate, not a preference.** Seeded RNG from the world seed,
   no sleeps, no network: provider tests use a stdlib fake HTTP server; e2e uses
   scripted transports; a scenario's script must be fully consumed
   (`script_remaining == 0`), so a drifted script fails the run.
8. **R9 surfaced the mechanics verdict on the human surface.** `resolve.py`
   already produced `MechanicalOutcome.verdict_line` (consumed by the evals);
   the CLI now prints it per turn as `(verdict: …)`, which is what makes the
   stub-play transcript readable as evidence.
9. **Frozen-file discipline held.** Since the scaffold commit `534c43e`, the
   only frozen-file change is one line of `schema.sql` (quoting
   `saga_levels."references"`, `e13ec08`); `models.py`, `config.py`,
   `similarity.py`, `pyproject.toml` are byte-identical to the scaffold.

## 4. Known gaps (honest list)

1. **No live BYOK call has ever been made.** Every provider test runs against a
   stdlib fake HTTP server; the e2e model matrix uses scripted transports.
   Request/response shapes are pinned both directions, but the first real
   model call (auth, rate limits, real token accounting, prose quality) is
   unexercised.
2. **`decay_class` derivation — RESOLVED post-rebuild (owner decision 1a, 2026-09-14,
   commit `519c838`).** `validate.py::_apply_relationship` now derives the class
   from the APPLIED (post-clamp) magnitude via
   `RelationshipLedger.default_decay_class` (≥25 durable, ≥10 slow, else fast)
   when the delta carries no valid tag; an explicit narrator tag still wins.
   Both write paths now share one rule. Follow-ups: +6 validator-side test cases
   (suite 788), `boundary-clamps`' Marla probe now uses the `fast` rate
   (0.08/turn), probe 14 asserts the unified rule (3/3), and a 6th mutation case
   covers a revert to the flat `"slow"` default.
3. **No player-travel mechanism.** There is no `move`/location delta kind in
   `DELTA_KINDS`, and no module writes `characters.location_id` except the
   validator's character writer (used by hp/stat/inventory deltas). Observed:
   three movement actions ("walk to the market", "head to the gatehouse",
   "climb down into the well") all resolve as `exploration`, no roll, and the
   session still ends at `yard`. A follow-up must add the delta kind + a
   validator handler (the schema already carries `location_id` and
   `connections`).
4. **§9's craft axis is unimplemented.** Correctness (state, contradictions,
   permanence) is covered by tests and evals; narration quality (voice,
   pacing, non-repetitiveness) has no human/LLM-judge scoring. The stub
   narrator's prose is template filler and is not representative of live
   narration quality — do not judge the product's prose by the transcript.
5. **Streaming is not implemented.** Adapters are one-shot
   request/response; `ProviderCaps.streaming` is a declared/diagnostic slot
   only. A UI that wants token streaming will need adapter work.
6. **Packaging:** there is no editable install / console script
   (`pyproject.toml` is frozen), so `python -m engine …` needs
   `PYTHONPATH=src` (documented in `engine/README.md`). `python -m evals` works
   as-is because `evals/__init__.py` puts `src/` on `sys.path`.
7. **CLI `evals` subcommand runs the core suite only** (`python -m engine evals
   [--root|--scenario|--seed|--db-dir|--json|--list]` delegates to
   `run_scenarios`); `--suite core|e2e|all` lives on `python -m evals`.
8. **Mood "reset pressure from scene events" (§3.7) is realized indirectly:**
   scene events spike mood only through proposed `mood` deltas, and time decay
   pulls the value back toward the personality baseline. There is no
   scene-change (location/actor-change) reset trigger; add one only if
   playtesting asks for it.
9. **Single campaign per DB, no auth, no multi-user.** Matches the spec's
   single-player assumption; the app integration must own accounts.
10. **Relationship clamp read path:** per-category meters **include** the
    `NPC.disposition_base` anchor (anchor counted once in `"total"`), and decay
    is applied at read time, so any expectation written against the raw ledger
    sum is wrong one turn later (two landed instances already fixed:
    `762ea28`, `0dc0afb`). Re-derive expectations from
    `anchor + Σ delta·exp(-rate·age)`.
11. **No save/migration importer** from the live app's campaigns, and no
    engine-side content pipeline (the demo fixture is the only world).

## 5. Integration path for the existing app (next steps)

What exists today: `backend/` (FastAPI + SQLAlchemy/alembic, 445 green tests)
and `frontend/` (Next.js) serving the live game; `engine/` is a standalone
stdlib package with its own SQLite schema and no service surface. The rebuild
deliberately did **not** wire them together (spec §0 non-goals: UI/frontend
integration, multiplayer, live API calls in CI).

To ship the rebuilt engine into the product, a follow-up must decide and build:

1. **Process boundary (decide first).** Recommended: a thin HTTP service around
   `PlaySession`/`Orchestrator` — `POST /campaigns` (seed a world),
   `POST /campaigns/{id}/turns` (`{input}` → narration, dialogue, verdict,
   state), `GET /campaigns/{id}/state` — because the app already speaks HTTP.
   Alternatives: import `engine` in-process from the FastAPI app (cheapest, but
   mixes the stdlib-only rule with the app's dependency set) or port the engine
   onto the app's SQLAlchemy/Postgres stack (the domain modules are
   store-agnostic behind the row API, but `store/schema.sql` + `sqlite3` are
   SQLite-specific, so this needs a store adapter, not a rewrite).
2. **Campaign lifecycle + auth.** The engine has no accounts: the app must map
   user → campaign DB file (one file per campaign, WAL, one writer per turn)
   and keep the engine's DBs out of the request path's shared state.
3. **BYOK key handling.** Keys stay runtime env inputs, held server-side per
   user, never persisted by the engine and never logged; the app needs a
   settings surface that passes the key into the service per request/session
   and surfaces the probed capability verdict (native tools vs degraded JSON).
4. **UI surfaces to add:** turn input + narration view with the verdict line,
   `/state`-style snapshot panel, campaign list, BYOK setup, and a visible
   failure mode when the provider degrades (the engine already reports it).
5. **Travel/location** (gap §4.3) must land in the engine before a UI can offer
   a map or location picker; today the player is effectively pinned to the
   seeded location.
6. **Deploy:** the engine has no Dockerfile/health endpoint; a follow-up must
   add a container + `/health` and a persistence story for its SQLite files
   (a volume, or a move to the app's Postgres) before it can be deployed
   alongside the app.
7. **Verification:** re-run the gates from a clean clone (that is R10's job for
   this rebuild); after integration, extend the e2e matrix to run the same
   scripted sessions through the service boundary so the API cannot silently
   drift from the pipeline.

Suggested follow-up card order: (1) engine service + API contract + campaign DB
lifecycle; (2) frontend narrator behind a flag through the API; (3) BYOK setup
UI + capability report; (4) accounts → campaigns + auth; (5) deploy artifact +
health gate; (6) playtest round with the §9 craft scoring.

## 6. Evidence log

Commit chain on `main` (R9's own commits last):

```
cad421d engine(cli): print the mechanics verdict line each turn (R9)
3e28081 engine(evals): stub-play transcript artifact (R9)
9142197 test(evals): pin the e2e suite — snapshots, persisted rows, failure probes
af0866d engine(evals): pipeline-level e2e suite — contradictions, permanence, memory, matrix, budget (R8)
4bd7014 engine(play): turn-index the stub's phrasing so turn 1 opens cleanly
2ada29e engine(pipeline): turn orchestrator — intent, Pass A-D, consistency gate (R7)
```

(Full chain from the scaffold: `534c43e` → `eng/*` branch merges `b8aeb03`
`4aa1199` `8c672aa` `b667c22` `d3d8529` `57398fc` → `e817a4c` → `2ada29e` →
`4bd7014` → `af0866d` → `9142197` → `3e28081` → `cad421d` → R9 docs commit.)

Commands and captured results:

```
$ cd engine && .venv/bin/python -m pytest tests -o addopts="" -q
782 passed in 41.75s                                    # exit 0

$ .venv/bin/ruff check src tests evals
All checks passed!                                      # exit 0

$ .venv/bin/python -m evals
evals [core] seed=13: 5/5 scenario(s) passed
  PASS boundary-clamps / conservation / contradiction-bait / dead-npc-lock / lead-gates
RESULT: OK                                              # exit 0

$ .venv/bin/python -m evals --suite e2e
evals [e2e] seed=13: 5/5 scenario(s) passed
  PASS e2e-contradiction-battery / e2e-death-permanence / e2e-memory-retrieval
       / e2e-model-matrix / e2e-telemetry-budget
RESULT: OK                                              # exit 0

$ cd backend && .venv/bin/python -m pytest tests -o addopts="" -p no:warnings -q
445 passed in 21.50s                                    # exit 0

$ git diff --stat 534c43e..HEAD -- backend frontend     # (before R9 docs commits)
<empty>

$ git diff --shortstat 534c43e..HEAD
67 files changed, 21694 insertions(+), 192 deletions(-) # docs/ + engine/ only
```

Stub-play transcript: `engine/evals/artifacts/stub_play_transcript.md`
(md5 `b3fe0d66a353ffeb119709bec2579ac7`) — 12 turns; first output line
`Lorebound engine — stub narrator`; per-turn verdict bands include FAILURE
(turns 3, 5, 7), a natural-1 CRITICAL FAILURE (turn 8), SUCCESS AT A COST
(turns 9, 12) and one blocked action (turn 6, absent target); last lines are
the end-of-session `state_view()` JSON (turn 12, location `yard`, pinned fact
"Marla's brother Dain is dead…") followed by `(campaign saved)`.

Push: `git push origin main` (R9, 2026-09-14) — the pushed hash is recorded in
the R9 card completion (`metadata.pushed_hash`) and re-asserted by R10 against
a clean clone.
