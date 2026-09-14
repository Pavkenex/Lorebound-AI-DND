# Integration Notes — rebuilt engine in the live app (phase 2, as-built)

What actually shipped when the rebuilt engine (`engine/`, Python 3.13, stdlib-only) was
wired into the live FastAPI + Next.js app **in-process**. The settled spec is
`docs/INTEGRATION_PLAN.md`; the owner's deploy steps are `docs/INTEGRATION_DEPLOY.md`.
Readers who only want to deploy can stop at the runbook.

As-built status after the P6 ship (merge `b7806fe`) and the P10 round-2 ship (merge
`89dd375`; battery results in §7, §10 and the finalize blocks below):

| Card | Scope | State |
|---|---|---|
| P1 | Packaging — engine installs into the backend (image + dev env) | landed `3fb40ed`, `485d307` |
| P2 | Bridge module — campaign lifecycle, turn wrapper, runtime-key flow | landed `070e96f`, `b41ee25`, `af98d73` |
| P3 | HTTP router — flag-gated `/engine` surface, auth, key header | landed `6844b6d`, `7161a4b`, `30dc11d`, `5b98f46` |
| P4 | Frontend pilot — `/chronicle` behind `NEXT_PUBLIC_ENGINE_MODE` | landed `e94fc38`, `04f4af7`; merged `b7806fe` |
| P5 | Deploy docs + compose/deploy-artifact re-verification | this document + `INTEGRATION_DEPLOY.md` |
| P6 | Ship — merge the frontend, full batteries, push | merged `b7806fe`; batteries + push recorded below |
| P7 | Post-deploy live verify + first real BYOK turn | **parked** until a deploy sets `ENGINE_MODE=1` |
| P8 | Content boundaries — `X-Content-Prefs` → engine prompt policy (plan §11) | landed `2d6c002` (see §10) |
| P9 | Frontend boundaries line on `/chronicle` | landed `935a51e`, `32e0ba5`; merged `89dd375` (see §10) |
| P10 | Ship round 2 — merge, batteries, push | merged `89dd375`; batteries + push in the P10 finalize block |

## 1. Decisions (settled in the plan — not revisited)

1. **In-process import**: the engine ships inside the backend image
   (`pip install /opt/engine`) and is imported as `app.modules.engine.*`. No new
   service, no new Coolify resource.
2. **BYOK keys are runtime-only, never persisted** — no DB column, no log line, no
   prompt, no cache key.
3. **Providers: `openai-compatible` only** (plus the `stub` default). The engine's
   Anthropic/Gemini adapters remain tested but are not app choices.
4. **Engine turns run through the engine's provider layer**, never `app/modules/ai`
   (which stores keys in Postgres and stays untouched for the legacy narrator).

## 2. File map (what changed, where)

Engine side — packaging moved in P1 (`engine/pyproject.toml`), and P8 added the
content-policy seam (`play.PlaySession.start` / `pipeline.Orchestrator` keyword
`content_policy=`, the `scene["content_policy"]` key, the always-present system
line plus its `accounting["sections"]["content_policy"]` row — see §10). No other
engine runtime code changed in phase 2:

- `engine/pyproject.toml` — explicit `setuptools.build_meta` backend; `store/schema.sql`
  declared as package data (`[tool.setuptools.package-data] engine = [...]`) so a
  non-editable install ships the file `engine.store` reads on every connect.
- `engine/src/engine/{context,pipeline,play}.py` — the P8 policy line (docs in the
  module docstrings; tests in `engine/tests/test_content_policy.py`).

Backend (`backend/`):

- `app/modules/engine/__init__.py` — package marker.
- `app/modules/engine/paths.py` (43 lines) — `{ENGINE_DATA_DIR}/{campaign_id}.db`;
  campaign ids are validated as 36 hex/dash characters *before* touching the filesystem;
  the data directory is created on demand; `DEFAULT_DATA_DIR = "./engine_data"`.
- `app/modules/engine/models.py` (57 lines) — the two Postgres side tables:
  `engine_campaigns` (campaign_id PK → `campaigns.id` CASCADE, world, engine_version,
  last_turn_at, created_at) and `engine_connection_settings` (user_id PK → `users.id`,
  provider, base_url, model, timeout_s, updated_at). **There is no key column, by design.**
- `app/modules/engine/bridge.py` (436 lines) — campaign create/list row helpers, the
  per-campaign writer lock, connection prefs (partial update; no key parameter), the
  one-turn wrapper `take_turn(...)`, response shaping (turn payload), `get_state`.
- `app/modules/engine/router.py` (346 lines) — the flag-gated HTTP surface; one
  router-level `require_engine_mode` dependency; `X-Provider-Key` read per request;
  bridge-error → HTTP error map; boundary re-redaction of provider details.
- `app/core/config.py` — `ENGINE_MODE: bool = False`, `ENGINE_DATA_DIR: str = "./engine_data"`.
- `app/core/schema.py` — `app.modules.engine.models` added to the boot model list, so
  `ensure_schema()`'s `create_all` creates the two new tables on a fresh or existing DB.
- `app/main.py` — `include_optional("app.modules.engine.router")` (a missing engine
  install degrades to a 404 pilot, never a boot failure) + an `app.openapi` wrapper that
  filters `/engine*` out of `/openapi.json` (and `/docs`) while the flag is off.

Deploy artifacts:

- `backend/Dockerfile` — build context is now the **repo root**; `COPY engine /opt/engine`,
  then `backend/{pyproject.toml,README.md,app}`, then
  `RUN pip install --no-cache-dir -e . /opt/engine`.
- `.dockerignore` (repo root, new) — keeps `.git`, `.env`, venvs, caches, `node_modules`
  and `.worktrees` out of the bigger context.
- `docker-compose.yml` — backend `build: {context: ., dockerfile: backend/Dockerfile}`,
  `ENGINE_MODE`/`ENGINE_DATA_DIR` envs, `engine_data` volume mounted at
  `/code/engine_data`.
- the CI workflow (`workflows/ci.yml`) — installs `./engine` alongside `./backend[dev]`,
  since the backend suite imports the engine.

Tests: `backend/tests/test_engine_packaging.py` (71 lines, 5 tests), `test_engine_bridge.py`
(603 lines, 17 tests), `test_engine_router.py` (839 lines, 35 tests — 57 phase-2 tests in
total), and `test_ship.py` updated for the repo-root context.

## 3. One turn, end to end (as built)

```
pilot UI (P4)                                      + X-Provider-Key when a key is set
  → POST /engine/campaigns/{id}/turns {text}       + X-Content-Prefs (player boundaries) ─┐
  → FastAPI: require_engine_mode → require_user → scope_campaign (404 cross-account)
  → router.content_prefs(header) → (ContentPrefs, readable)   [§10]
  → bridge.take_turn(db, user_id, campaign_id, text, provider_key=<header>,
                     content_prefs=<parsed>, content_prefs_invalid=<not readable>)
      → connection prefs (non-secret) resolve provider (default "stub"; key required only when live)
      → per-campaign threading.Lock (single writer)
      → PlaySession.start(db_path=…/{campaign_id}.db, adapter=…, provider=…,
                          content_policy=prefs.describe_for_prompt())
      → engine.act(): intent → Pass A–D → capability canary on first live use
        (verdict cached in the campaign DB's provider_caps table — no key material)
  → payload: turn, narration, dialogue[{npc_id,name,text}], mechanics, suggestions,
             capability{provider,model,mode,native_tools,degraded}, state, system_lines,
             content{nsfw,violence,horror,romance,language}   [§10]
  → last_turn_at updated in Postgres; state committed in the campaign SQLite file (WAL)
```

Sync endpoint in FastAPI's threadpool (same shape as the legacy `/act`), 1–2 provider
calls for a live turn.

## 4. Runtime-key flow (hard rules, and where each is enforced)

1. The key lives in the browser (`localStorage`, P4) and is sent per request as
   `X-Provider-Key` over the app's HTTPS/HTTP origin.
2. `router.py` reads `request.headers.get("X-Provider-Key")` per call and passes it to
   `bridge.take_turn(..., provider_key=…)`. The router never logs request bodies or
   headers.
3. `bridge._resolve_provider` hands it to `build_adapter(cfg, api_key=provider_key)`;
   it lives in server memory for that call only. Stub needs no key; a live provider with
   no key raises `connect_your_ai` (HTTP 400, exact detail string `connect_your_ai`).
4. Provider failures surface as `EngineBridgeError("provider_error", detail=<engine-
   redacted text>)` → HTTP 502; `router._scrub` re-redacts the detail with the exact
   runtime key as defense in depth. The raw exception is never re-raised with the key.
5. Nothing has a key-shaped storage slot: `EngineConnectionRow` has no key column; the
   capability cache key is `provider|model|base_url|api_mode`; `PUT /engine/connection`
   has no key field and **ignores unknown fields on purpose** (pydantic's 422 body echoes
   the offending input, so `extra="forbid"` would bounce a stray `api_key` back in a
   response).
6. Proofs (offline, scripted transports): `test_engine_router.py` pins that the key
   appears in no response body, no Postgres row, no campaign file under
   `ENGINE_DATA_DIR`, and no captured log line — including a hostile provider that
   echoes the `Authorization` header back inside its error text. `test_engine_bridge.py`
   carries the same key-never-stored assertions at the bridge layer.

## 5. HTTP surface and error map (as built)

| Route | Notes |
|---|---|
| `POST /engine/campaigns` `{name}` | 201 `{id,name,world,last_turn_at}`; creates the app row, side-row, seeded engine DB |
| `GET /engine/campaigns` | caller's engine-only campaigns (`seed_key == "engine"` + side-row), played-first |
| `POST /engine/campaigns/{id}/turns` `{text}` + optional `X-Provider-Key` | one turn, payload in §3 |
| `GET /engine/campaigns/{id}/state` | read-only snapshot; never creates or seeds a DB (a missing file is 404) |
| `GET/PUT /engine/connection` | non-secret prefs, partial update, no key field |
| `POST /engine/connection/check` + `X-Provider-Key` | `{reachable, native_tools, detail}`; always 200 for provider outcomes, 400 `connect_your_ai` without a key; deliberately does **not** cache a verdict (the first live turn does) |

Flag off: every `/engine/*` path answers the framework's own 404 (before auth, before
body validation, identical for anonymous and signed-in callers), and `/openapi.json`
lists nothing under `/engine`. Errors: 400 for `connect_your_ai` / `invalid_prefs` /
`unsupported_provider` / `unknown_world` / `bad_campaign_id`; 404 `campaign not found`
for `no_campaign` / `not_an_engine_campaign` (never reveals foreign ids); 502 scrubbed
`provider_error`; anything unmapped is 400, never a 500.

## 6. Storage layout

- **Postgres**: the existing `campaigns` row (owner, name, `seed_key='engine'`) + the two
  new side tables. `ensure_schema()` creates them on boot (`create_all` handles *missing
  tables*), so the deploy needs no migration step.
- **Per campaign, one SQLite file**: `{ENGINE_DATA_DIR}/{campaign_id}.db` (WAL), holding
  the whole engine state. Deploy: `ENGINE_DATA_DIR=/code/engine_data` on a persistent
  volume; dev default `./engine_data`.
- No alembic revision was added for the two new tables (the repo has revisions for only
  a few tables and no deploy runs `alembic upgrade head`). If a migration step is ever
  added to deploys, generate the revision first.

## 7. Verification evidence (this phase)

- `cd backend && .venv/bin/python -m pytest tests -o addopts="" -rA -p no:warnings`
  → **502 passed** (72.4s); `.venv/bin/ruff check app tests` → clean.
- `cd engine && .venv/bin/python -m pytest tests -o addopts="" -q -p no:warnings`
  → **788 passed** (69.6s); `.venv/bin/ruff check src tests` → clean.
- **Compose parse + wiring** (`PyYAML`, scratch probe): `docker-compose.yml` parses;
  backend `context: .` + `dockerfile: backend/Dockerfile`, `ENGINE_MODE`/`ENGINE_DATA_DIR`
  set, `engine_data` volume mounted at `/code/engine_data`; frontend build `./frontend`.
- **Image-build simulation** (no Docker daemon available): the four `COPY` sources were
  staged exactly as the Dockerfile consumes them and installed the image way
  (`python3.13 -m venv` + `pip install --no-cache-dir -e . ./engine`) — both packages
  built and installed, `engine/store/schema.sql` present under site-packages (non-editable
  install), `app.main` imports, and 16/16 checks ran green over real HTTP: `/health`,
  `/openapi.json` flag-on/flag-off, register → create campaign → stub turn (turn 1,
  `capability.mode == "stub"`, no key) → `/state`, and flag-off `/engine/*` → 404.
- **Live frontend bundle probe** (read-only): the currently deployed `/_next` chunk
  inlines `130.61.50.108:8001` for `NEXT_PUBLIC_API_URL`, confirming Coolify's frontend
  service env vars do reach `npm run build` — so `NEXT_PUBLIC_ENGINE_MODE=1` will be baked
  into the pilot bundle the same way.
- Evidence files (scratch, outside the repo): `/opt/data/scratch/p5/` —
  `imgsim_driver.py` / `imgsim_driver.txt` (16/16), `probe_compose.py`, `install.log`;
  board copy: `/opt/data/kanban-evidence/t_60c1948d-p5-evidence.md`.
- **P6 ship batteries on merged `main`** (merge `b7806fe`; commands exactly as the
  card's tooling): backend **502 passed** + `ruff check app tests` clean; engine
  **788 passed** + `ruff check src tests` clean; frontend `npm run typecheck` clean,
  `npm test` **64/64**, `npm run build` clean flag-off (17 routes, `/chronicle`
  5.93 kB) and clean again with `NEXT_PUBLIC_ENGINE_MODE=1`; stub e2e over real HTTP
  **16/16** — register → login → create campaign → stub turn (turn 1,
  `capability.mode == "stub"`, a shown `perception` check with
  `mechanics.band == "success"`) → `/state` (`location='The Salt Gate yard'`) →
  flag-on `/openapi.json` lists `/engine/campaigns` (58 paths) → the connection
  view has no key field → a canary `X-Provider-Key` sent against a dead provider
  answers 200 (`reachable: false`) and 502 (scrubbed detail) with the key absent
  from both bodies, and the canary appears in **0** files across the app DB, the
  engine data dir and both server logs. Driver:
  `/opt/data/scratch/p6/p6_ship_e2e.py`; output `p6_ship_e2e.txt` (md5
  `c8cdd614dc5501fe74acb043abd2192f`); board copy in
  `/opt/data/kanban-evidence/t_ad425576-p6-evidence.md`.

## 8. Known gaps (deliberate, or later cards)

1. **First live BYOK call never made** — every live-provider path so far ran against
   scripted/fake transports. P7 is exactly this: one cheap-model turn after deploy,
   checking `capability.native_tools` and the degraded banner.
2. **No idempotency keys on the pilot** — a retried `POST …/turns` runs a second turn
   (the legacy `/act` has `Idempotency-Key`; the engine pilot is out of scope by plan §2).
3. **Single-writer lock is in-process** — a `threading.Lock` per campaign id. Correct for
   the single backend container; running more than one backend replica would let two
   processes write one campaign file.
4. **Travel/location mechanic** is an engine gap (plan §8) — the demo fixture's location
   set is what the pilot has.
5. **Legacy narrator retirement** not started: `app/modules/ai` (stored-key BYOK) and the
   legacy play stack stay live and untouched; engine turns never route through them.
6. **Suggestions** are derived from open leads in the state view (no model-generated
   suggestions).
7. **`ENGINE_DATA_DIR` absolute-path discipline**: unset/blank falls back to the relative
   `./engine_data` (dev-friendly). A deploy that forgets the env var writes inside the
   container filesystem — the runbook's §5 check covers it.

## 9. Post-deploy checklist

1. Follow `docs/INTEGRATION_DEPLOY.md` (flags + volume + redeploy + the stub verify).
2. Prove the landing with the chunk-hash poller (`coolify-deploys` skill) rather than
   eyeballing; hard-refresh before judging the frontend.
3. Then un-park **P7**: one live BYOK turn (cheap model) + the degraded-banner check.

## 10. Content boundaries on the engine path (P8, plan §11)

The pilot reads the player's content limits and the engine narrator prompt always
carries them. Round 2's frontend card (P9) renders the line; this section is the
backend/engine contract it renders.

**Request.** `POST /engine/campaigns/{id}/turns` accepts an optional
`X-Content-Prefs` header — the same JSON vocabulary the legacy play surface reads
(`violence`/`horror`/`romance`/`language` at `off|reduced|standard`, plus the
`nsfw` master switch; `low|clean|mild` alias to `reduced`). The router validates it
with `narrator.prefs.ContentPrefs` itself, so the two surfaces cannot drift.

**Outcome per input.**

| Header | Applied | Prompt | Payload |
|---|---|---|---|
| absent / empty | defaults | default directive | `content` = defaults, no note |
| readable JSON object | those prefs | their directive | `content` = applied values |
| unreadable (bad JSON, non-object, out-of-vocabulary level, wrong type) | **defaults** (safe: nsfw off, standard caps) | default directive | `content` = defaults **+ `CONTENT_DEFAULTS_NOTE`** in `system_lines` |

The note exists because the legacy narrator *silently dropped* a malformed
payload (a player's NSFW-off setting could just vanish); the pilot never does.
Wording: ``content: settings could not be read — standard boundaries applied``
(`bridge.CONTENT_DEFAULTS_NOTE`, the string a UI can match on).

**Engine seam.** `bridge.take_turn(...)` renders `prefs.describe_for_prompt()`
**verbatim** and hands it to the engine as the prompt's content policy:
`PlaySession.start(..., content_policy=…)` → `Orchestrator(..., content_policy=…)`
→ `scene["content_policy"]` → `ContextAssembler`, which appends it as the system
message's last line — after the ruleset, so the cacheable prefix stays
byte-identical; never truncated by the system ceiling; never dropped under budget
pressure; accounted separately as `accounting["sections"]["content_policy"]`. A
campaign with no policy assembles the pre-P8 prompt byte for byte (the engine
suite pins that). The directive is **prompt-only**: it never reaches Postgres, the
campaign SQLite file, or a log line, and it is re-read on every turn (flipping NSFW
mid-campaign changes the next turn, not the last one).

**Payload echo.** `payload["content"]` carries the *applied* boundaries —
`{nsfw, violence, horror, romance, language}` — never the raw header, so aliases
arrive canonical (`mild` → `reduced`) and unknown header keys never surface.

**Frontend (P9).** The pilot sends the header on every `/engine/*` request — turn,
state, connection, create — from `frontend/lib/engine.ts::engineRequestHeaders()`;
the value is read at REQUEST time from the store's slot `lorebound-prefs-v1`
(`storedContentPrefs()` — a captured closure would go stale the moment Settings
flips NSFW mid-session). Values are normalized to the canonical vocabulary on the
way out (`contentPrefsHeader()`), so a fresh browser sends the store defaults and a
partial stored payload cannot leak a non-canonical level. The turn's applied echo
arrives as `payload.content`; the card's "Story boundaries" line renders it via
`appliedContent()` (tolerant — an absent echo falls back to the store values and
the page never breaks) and surfaces the server's note when defaults were applied
(`boundariesNotice()` matches `CONTENT_DEFAULTS_NOTE` exactly, plus a generic
`could not be read`/`defaults` scan; the echo itself carries no flag field).

**Tests.** `engine/tests/test_content_policy.py` (11) and
`backend/tests/test_engine_content_prefs.py` (25, offline: recording fake adapter,
in-memory app DB, throwaway `ENGINE_DATA_DIR`). The backend module pins the parser
contract, the directive verbatim in the *recorded live prompt* (defaults, custom,
NSFW, per-turn re-read), the fallback note, the applied-prefs echo, and the
hygiene set: the header payload absent from `caplog.text`, from every app-DB row
and from the campaign file, with a canary string smuggled into the header. The
engine module pins the three assembly properties plus the byte-identical
no-policy prompt. The frontend side has its own §11 block in
`frontend/lib/engine.test.ts` (+8 tests; suite total 72/72): the request-time
store read, header normalization (incl. the partial-`{nsfw:true}` payload), the
tolerant echo read and the note wording/paths.

**P8 evidence** (commands + observed results; logs under `/opt/data/scratch/p8_gates/`):

- `cd engine && .venv/bin/python -m pytest tests -o addopts="" -q -rA -p no:warnings`
  → **799 passed** (788 pre-P8; +11); `.venv/bin/ruff check src tests evals` → clean.
- `cd engine && .venv/bin/python -m evals --suite all` → `RESULT: OK` (core 5/5,
  e2e 5/5); `evals/artifacts/r10_probe_mechanisms.py` → 14/14;
  `r10_probe_mutations.py` → control clean, all 6 mutations detected.
- `cd backend && .venv/bin/python -m pytest tests -o addopts="" -q -rA -p no:warnings`
  → **527 passed** (502 pre-P8; +25); `.venv/bin/ruff check app tests` → clean.
- **Live over-HTTP probe** against a real `uvicorn` from this tree
  (`ENGINE_MODE=1`, scratch app DB + fresh engine dir, port 8003): a canonical
  header (`violence:reduced, horror:off, romance:off, language:reduced,
  nsfw:true`) echoes `content` exactly with no note; a malformed payload answers
  the defaults **plus** the note in `system_lines`; an absent header answers the
  defaults with no note — 3/3 turns, stub provider, no key. Driver
  `/opt/data/scratch/p8/p8_content_prefs_e2e.py`, transcript
  `p8_content_prefs_e2e.txt` (md5 `b6419f48a928dd70110c14e6f6e36638`).
- P8 mutation probes (`/opt/data/scratch/p8_mutate/probe_mutations.py`): 7
  deliberate removals of the new guards (policy never appended / policy made
  truncatable / raw policy type accepted / header ignored / policy not threaded /
  silent drop restored / header logged) — every one fails the new tests
  (`probe_final.txt`: "RESULT: OK — every mutation detected").
- Empty-policy byte-identity outside the unit tests: the checked-in 12-turn stub
  transcript still hashes `b3fe0d66a353ffeb119709bec2579ac7` (the R9/R10 record),
  i.e. the offline play path is unchanged when no policy is threaded.

---

### P6 finalize block (ship, filled during the ship card)

- Frontend merge: `b7806fe` (`git merge --no-ff p2/frontend`); `git diff
  db6f664..b7806fe -- backend engine` is **empty** — the merge is frontend-only
  (`/chronicle` route + `loading.tsx`, `lib/engine.ts` + its test, `engineApi` in
  `lib/api.ts`, the flag-gated shell link, globals.css).
- Batteries on merged `main` (`b7806fe`): backend **502 passed**, engine **788
  passed**, both `ruff` clean; frontend `typecheck` clean, **64/64** tests,
  `npm run build` clean **flag-off and flag-on**; stub e2e **16/16** over real HTTP.
- Pushed hash: this P6 ship commit (`git log -1 --format=%H`; `origin/main` tip
  after the push). The push advanced `origin/main` from `5b98f46` to it, carrying
  `e5e2ded`, `db6f664`, `e94fc38`, `04f4af7` and merge `b7806fe`.
- Key-absence test reference (`backend/tests/test_engine_router.py`):
  `test_turn_passes_the_runtime_key_through_and_persists_nowhere` — proves the
  header reached the adapter, then asserts the key is in no response body, no
  app-DB row (`_db_text()`), no campaign file (`_data_dir_text()`);
  `test_provider_failure_is_502_scrubbed_and_never_logged` — 502 detail arrives
  with the `***` marker and the key is absent from `caplog.text`, the DB and the
  data dir, against a hostile provider that echoes the `Authorization` header;
  `test_put_ignores_unknown_fields_gracefully_and_never_echoes_a_key` — a stray
  `api_key` in the PUT body is ignored, never echoed; no key-shaped field exists.

---

### P10 finalize block (round-2 ship, filled during the ship card)

- Merge: `89dd375` (`git merge --no-ff p2/content-prefs`; merge base `e6ec68d`).
  `git diff 2d6c002..89dd375` is **frontend-only** — 6 files, +332/−11, no
  conflicts.
- Batteries on merged `main` (`89dd375`): backend **527 passed** (46.4s) +
  `ruff check app tests` clean; engine **799 passed** (54.4s) +
  `ruff check src tests evals` clean; `python -m evals --suite all` → core
  **5/5** + e2e **5/5**; `r10_probe_mechanisms.py` → **14/14**;
  `r10_probe_mutations.py` → control clean, **6/6** mutations detected.
  Frontend: `npm run typecheck` clean, `npm test` **72/72**, `npm run build`
  clean **flag-off and flag-on** (`/chronicle` 6.67 kB / 110 kB First Load).
  Logs: `/opt/data/scratch/p10/` (`backend_pytest.log`, `engine_pytest.log`,
  `engine_gates.log`, `frontend_builds.log`).
- **Content-prefs e2e on the merged tree** (real `uvicorn` from this tree,
  `ENGINE_MODE=1`, stub provider, no key, scratch app DB + fresh engine dir;
  port 8004): **4/4** — a custom header (`violence:reduced, horror:off,
  romance:off, language:reduced, nsfw:true`) echoes `content` exactly with no
  note; a malformed payload answers the defaults **plus** `CONTENT_DEFAULTS_NOTE`
  in `system_lines`; an absent header answers the defaults with no note; legacy
  aliases (`mild`/`low`/`clean`) arrive canonical (`reduced`) in the echo.
  Driver `/opt/data/scratch/p10/p10_content_prefs_e2e.py`; transcript
  `p10_content_prefs_e2e.txt` (md5 `da8128c35f7ede9a40a034c97f55d22e`).
- Runbook review: `docs/INTEGRATION_DEPLOY.md` needs no new flag, secret, volume
  or step — round 2 rides the existing `ENGINE_MODE=1` pilot path (the boundaries
  are request headers). One illustrative amendment: §3's stub-turn response
  comment now lists the `content` block beside the other payload keys. Rollback
  (§4) is unchanged.
- Push: this ship commit (round-2 tip; `git log -1 --format=%H`). `git push
  origin main` advanced `origin/main` from `4e738b3` to it, and
  `git log origin/main..HEAD` is empty after the push.
