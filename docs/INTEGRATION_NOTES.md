# Integration Notes — rebuilt engine in the live app (phase 2, as-built)

What actually shipped when the rebuilt engine (`engine/`, Python 3.13, stdlib-only) was
wired into the live FastAPI + Next.js app **in-process**. The settled spec is
`docs/INTEGRATION_PLAN.md`; the owner's deploy steps are `docs/INTEGRATION_DEPLOY.md`.
Readers who only want to deploy can stop at the runbook.

As-built status after the P6 ship (merge `b7806fe`) and the P10 round-2 ship (merge
`89dd375`; battery results in §7, §10 and the finalize blocks below). The P11
connected-AI amendment is §11 (and the plan header):

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
| P11 | Pilot requires a connected AI — keyless/stub play removed | landed `719e77e` (server) + this commit (frontend/docs) — see §11 |

## 1. Decisions (settled in the plan — not revisited)

1. **In-process import**: the engine ships inside the backend image
   (`pip install /opt/engine`) and is imported as `app.modules.engine.*`. No new
   service, no new Coolify resource.
2. **BYOK keys are runtime-only, never persisted** — no DB column, no log line, no
   prompt, no cache key.
3. **Providers: `openai-compatible` only.** The engine's
   Anthropic/Gemini adapters remain tested but are not app choices. (P11: the old
   `stub` default is retired as well — connected AI or nothing, see §11.)
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
  P11: `provider`'s ORM default is `""` (unset) — the old `"stub"` default is retired,
  and no migration is involved (a client-side default only).
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
(677 lines, 18 tests), `test_engine_router.py` (985 lines, 37 tests), and
`test_engine_content_prefs.py` (486 lines, 25 tests). P11 rewrote the stub-based
turn-flow tests around a scripted fake adapter (`_ScriptedAdapter` + a live
connection row) and left the key-hygiene assertions standing (`test_ship.py` had
been updated earlier for the repo-root build context, P1).

## 3. One turn, end to end (as built)

```
pilot UI (P4)                                      + X-Provider-Key when a key is set
  → POST /engine/campaigns/{id}/turns {text}       + X-Content-Prefs (player boundaries) ─┐
  → FastAPI: require_engine_mode → require_user → scope_campaign (404 cross-account)
  → router.content_prefs(header) → (ContentPrefs, readable)   [§10]
  → bridge.take_turn(db, user_id, campaign_id, text, provider_key=<header>,
                     content_prefs=<parsed>, content_prefs_invalid=<not readable>)
      → connection prefs resolve the provider (P11: unset or legacy "stub" ⇒ 400
        connect_your_ai — there is no keyless path; a live provider needs the key)
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
   it lives in server memory for that call only. P11: an unconnected account — provider
   unset or the retired `stub` default — raises the same `connect_your_ai`
   (HTTP 400, exact detail string `connect_your_ai`) as a live provider without a key;
   there is no keyless case left.
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
| `POST /engine/campaigns/{id}/turns` `{text}` + optional `X-Provider-Key` | one turn, payload in §3; 400 `connect_your_ai` when no model is connected (P11 — provider unset, legacy `stub`, or a live provider without a key) |
| `GET /engine/campaigns/{id}/state` | read-only snapshot; never creates or seeds a DB (a missing file is 404) |
| `GET/PUT /engine/connection` | non-secret prefs, partial update, no key field; PUT accepts `openai`/`openai-compatible` only (P11: `stub` → `unsupported_provider`), GET normalizes `stub`/unset to `""` |
| `POST /engine/connection/check` + `X-Provider-Key` | `{reachable, native_tools, detail}`; always 200 for provider outcomes; 400 `connect_your_ai` when nothing is connected — no key, unset provider or a legacy `stub` row (P11: there is nothing to probe); deliberately does **not** cache a verdict (the first live turn does) |

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

1. Follow `docs/INTEGRATION_DEPLOY.md` (flags + volume + redeploy + the
   connect-and-play verify; P11: an unconnected turn must answer 400
   `connect_your_ai` before anything else).
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

---

## 11. Connected AI required (P11) — as-built

Owner ruling 2026-09-14 (verbatim): *"the AI is there already, it shouldn't be
playable without it. You can remove that."* Keyless/stub play is gone from the
player surface; the engine package's own `stub` adapter survives as
engine-internal dev/test machinery only and is unreachable from the app. The
plan carries the dated amendment in its header.

Server — `backend/app/modules/engine/`:

- `bridge._resolve_provider` (the single resolution point) treats unset **and** a
  legacy `"stub"` row exactly like a live provider without a key →
  `EngineBridgeError("connect_your_ai")` → 400 `{"detail":"connect_your_ai"}`.
  Fresh rows read unset (`models.EngineConnectionRow.provider` ORM default `""`
  — a client-side default only, no migration) and `connection_view` normalizes
  `stub`/unset to `""` in GET responses. `POST /engine/connection/check` with
  nothing connected answers the same 400 (nothing to probe);
  `PUT /engine/connection` rejects `stub` through the existing
  `unsupported_provider` path. `SUPPORTED_PROVIDERS` stays `("openai",
  "openai-compatible")`. The internal `live` flag is gone from the turn
  payload/capability — every player turn is live, so `capability.mode` is always
  `"live"` and the degraded banner keys off `native_tools`/`degraded` alone.
- Runtime-key rules are untouched (§4): no key column, no key in errors/logs, and
  the key still travels only in `X-Provider-Key`.

Frontend — `/chronicle` (`frontend/lib/engine.ts`, `app/chronicle/page.tsx`):

- `ENGINE_PROVIDERS = ["openai-compatible"]`; the "Stub — no model call (free)"
  option, the `provider !== "stub"` form gate, the stub capability-chip
  tone/title/label, the "Stub by default…" header line and the old
  `connectionLabel` stub branch are all gone. `capabilityChip()` returns `null`
  before the first turn and otherwise only ever describes a live model.
- `playGate({keyStored, provider, connectNeeded})` closes the play line (input +
  submit disabled) until a key is kept in this browser AND the account's saved
  connection names a real provider; one inline notice — "Connect your AI to play
  — the chronicle narrates with your model." — with a button into the connect
  card. The server's 400 `connect_your_ai` surfaces as that same notice (never a
  raw error) and withdraws the echoed action; browsing (list, transcript, state
  panel, connect card) stays usable. This is the 2026-09-14 live finding's fix: a
  key stored beside an unset/legacy-`stub` connection can no longer silently
  write stub turns — the gate blocks and the card shows the not-connected state.
- `keyAfterConnectionSave(typed, stored)`: Save with an empty key field KEEPS the
  stored key (the other half of the finding) — only the explicit Clear button
  forgets it. "Your key stays in this browser only." copy unchanged.

Tests / evidence:

- Backend, `test_engine_router.py`: `test_turns_without_a_connected_model_are_refused`
  (fresh row + legacy `"stub"` row + a runtime key → 400, empty detail),
  `test_connection_check_without_a_connected_model_asks_to_connect` (both row
  shapes, nothing probed), `_store_provider` seeds legacy rows past the PUT
  validation; `test_engine_bridge.py::test_unconnected_and_stub_rows_cannot_play`
  at the bridge level; the scripted-adapter suites keep full create→turn→state
  coverage (`test_three_live_turns_advance_the_campaign`, suggestions, lock,
  heal); `test_engine_content_prefs.py::test_an_unconnected_turn_is_refused_before_any_content_work`
  proves the refusal precedes any content work (no prompt, no note, no echo).
- Frontend, `lib/engine.test.ts`: `playGate` (incl. key stored + provider unset /
  `"stub"` → blocked + notice), `keyAfterConnectionSave`, `providerConnected`,
  `capabilityChip` nullability, `connectionLabel` "no model connected",
  `probeVerdictText` without the stub branches.
- Counts and live over-HTTP evidence: recorded in the P11 finalize block below.

---

### P11 finalize block (connected-AI amendment, filled during the card)

- Backend: `pytest tests` (backend venv, `-o addopts=""`) → **530 passed**, 30.7s;
  `ruff check backend` → All checks passed. Engine package UNTOUCHED (`git status`
  shows only `backend/`, `frontend/`, `docs/`): engine suite **799 passed**;
  `python -m evals --suite all` → core **5/5** + e2e **5/5**, `RESULT: OK`.
- Frontend: `npm test` → **75 pass** (engine module 36), `npm run typecheck`
  clean, `npm run build` clean **flag-off** (27.5s) and **flag-on** (27.5s).
- Live over-HTTP e2e (real `uvicorn` from this tree, `ENGINE_MODE=1`, scratch app
  DB + fresh engine dir, local fake OpenAI-compatible endpoint): **17/17 legs
  passed** — register → create → unconnected turn **400 `connect_your_ai`** →
  unconnected check 400 → `PUT provider=stub` rejected
  (`provider 'stub' is not available; supported: openai, openai-compatible`) →
  legacy-`stub` row seeded → GET normalizes to `""` → legacy turn **still 400** →
  connect (`openai-compatible` + fake base URL) → check **200 reachable** → turn
  **200** with `capability.mode == "live"`, `native_tools: true` → connected but
  keyless turn 400 again → key hygiene: key present in the fake's log
  (it DID travel, via `Authorization`) and absent from the app DB, the engine
  data dir and the server log. Driver `/opt/data/scratch/p11/p11_live_e2e.py`;
  transcript `/opt/data/scratch/p11/p11_live_e2e.txt`.
- Browser probe (production build, flag-on, real backend + fake provider; DOM
  asserts + screenshots in `/opt/data/kanban-evidence/`):
  `p11-1-not-ready.png` — key stored, provider unset: input and `Act ↵` disabled,
  the connect notice on the play line, card reads "no model connected · a key is
  kept in this browser" plus the "No provider is saved for your account yet"
  warning; `p11-2-connected.png` — Save with the key field EMPTY kept the stored
  key (`localStorage['lorebound.engine.key']` unchanged before/after) and opened
  the gate ("Saved — openai-compatible · p11-fake.", notice gone, input enabled);
  `p11-3-turn.png` — a real turn rendered the fake provider's prose with the
  `◈ p11-fake` capability chip and no error banner; `p11-4-mobile-notready.png` —
  "Clear key" is the only explicit forget (gate returns), shot at 390×844.
- Commits/push: `719e77e` (server) + the frontend/docs commit at the tip; pushed
  to `origin/main`; `git log origin/main..HEAD` empty afterwards. Owner redeploys
  manually (Coolify) — the runbook's §3 now verifies the 400 first.

---

## 12. The story game: honest AI states (P12) — as-built

P11 applied the owner ruling to the `/chronicle` pilot; this card applies it to
the **main story game** (`/adventure` + Settings → Tale-spinner (AI)), which had
its own three silent-degrade paths — all producing the reported "same answer
forever": (a) no connection → the stub's "the room holds its breath…"
paragraph; (b) a configured-but-failing provider → the cover line "The
chronicler's quill falters…"; (c) the frontend's canned
`fixtures.fallbackNarration` on any failed `/act`. All three are gone.

**One truth (item 1).** `backend/app/modules/ai/settings_store.py::resolve_provider`
is now the single resolution used by BOTH `/ai/settings` (the panel) and `/act`
(the play path): a complete saved row → that endpoint; else the environment
(`AI_PROVIDER=openai-compatible` + `OPENAI_COMPAT_BASE_URL`/`_MODEL`); else —
nothing. `/ai/settings` reports it as `connected` + `active_provider` /
`active_source` (`settings|env|default`) / `active_model` / `active_base_url` /
`active_reason` (`unset|stub|incomplete|misconfigured|unsupported`), and the
legacy `stub` value is normalized to not-connected in every read. `stub` is no
longer a saveable provider: `PUT /ai/settings` rejects it through the
`unsupported_provider` shape (validated in the handler, not a `Literal`, so the
offered set stays data-driven). `env_provider_name()` reports `""` for the
retired `stub` (the deploy config's default) so the panel never re-introduces
stub copy. `active_provider` is intentionally `""` when nothing is connected —
`providerConnected("")` is false, so the pilot's `playGate` keeps working
unchanged.

**No silent degrade on the player path (item 2).** `Engine._beat_pipeline`
raises `ActFailure` instead of narrating cover prose: an unconnected turn
answers `400 {"detail":"connect_your_ai"}` (the P11 shape) and a provider
failure answers
`502 {"detail":{"code":"provider_failed","message":"<scrubbed>","retryable":true}}`
(`turn_failed` for anything else — both carry the machinery's words, scrubbed of
key-shaped material by `_scrub_provider_error`, capped at 200 chars). `/act`
does not checkpoint/persist a failed turn, so nothing is applied and a retry of
the same action+key is a normal replay. `_beat_pipeline` resolves the provider
through the same `resolve_provider` and passes it explicitly — the legacy stub
survives only as `providers.StubProvider` for test plumbing (`tests/conftest.py`
fixture `stub_play_provider`, opt-in per module) and env fallback for tests
(`AI_PROVIDER=stub` ⇒ the stub, unreachable from a player surface).
`/actions/submit` (the unauthenticated legacy surface) resolves the environment
provider and refuses the same 400 when there is none.

**Frontend (item 3).** Settings: the built-in-storyteller PLAY mode is gone —
the card always saves `openai-compatible`, states "Right now: your endpoint
(model) — from your settings" (or exactly why nothing is connected), keeps Save
(blank key keeps the stored one) + Test connection + Remove stored key, and says
plainly that a saved-but-incomplete endpoint reads as not connected.
`/adventure`: fetches `/ai/settings` and gates the act row with ONE notice
(`data-gate`: `connect|demo|offline`) — "✋ Connect your AI to play — the
chronicle narrates with your model." plus the reason and a link into Settings;
the signed-out sample pages are labelled `demo` ("Sample tale — a demo, not your
own"), the backend-unreachable case is `offline` (Retry reloads), and a
connected account shows "✎ Narrated by your endpoint (model)"
(`[data-ai="connected"]`). `lib/api.ts`'s `submitAction`/`rollCheck` return
`ActOutcome` (no fixture branch — `fixtures.fallbackNarration` is deleted), and
failures render `actFailureText()` verbatim in the shared `ErrorBanner` with
"Retry narration", which resends the SAME words with the SAME idempotency key
(`newActionKey()`), keeping the input otherwise. The Tutorial overlay is
curated UI instruction (act/dice/journal) and makes no claims about the world —
nothing there pretends to be narration.

**Tests (item 4).** Backend: `tests/test_ai_settings_api.py` rewritten —
unconfigured/legacy-stub/incomplete/misconfigured reads, doc==runtime agreement
(`_assert_doc_matches_runtime`) across six states, `PUT stub` rejected, refusal
on both a free-text and an authored beat with the state snapshot unchanged,
`502 provider_failed` machine-readable + retry-safe (same key lands after the
recovery), `/actions/submit` refusal, and `_scrub_provider_error` unit legs;
plus the `stub_play_provider` opt-in in 17 `/act` HTTP modules.
Frontend: `lib/ai.test.ts` (12 tests: gate, reasons, connection line, failure
wording). Engine suite untouched.

### P12 finalize block (filled during the card)

- Backend: `pytest tests` (backend venv, `-o addopts=""`) → **536 passed**, 33.6s;
  `ruff check app tests` → All checks passed. Engine package UNTOUCHED (the diff
  touches `backend/`, `frontend/`, `docs/` only): engine suite **799 passed**;
  `python -m evals --suite all` → `RESULT: OK`.
- Frontend: `npm test` → **85 pass** (`lib/ai.test.ts` adds 12), `npm run
  typecheck` clean, `npm run build` clean **flag-off** and **flag-on**
  (`NEXT_PUBLIC_ENGINE_MODE=1`, the shape the deployed service builds).
- Live over-HTTP e2e (real `uvicorn` from this tree, scratch app DB, local fake
  OpenAI-compatible endpoint; driver `/opt/data/scratch/p12/p12_live_e2e.py`,
  transcript `/opt/data/scratch/p12/p12_live_e2e.txt`, md5 5635a0df…):
  **23/23 legs passed** — unconnected `/act` refused on both a free-text and an
  authored beat with the state snapshot byte-identical afterwards → `PUT
  provider=stub` → 400 `provider 'stub' is not available; supported:
  openai-compatible` → legacy `stub` row seeded directly → GET reads
  `connected:false`, `active_reason:"stub"` and the turn still refuses →
  connect (`openai-compatible` + fake base) → doc `active_model:"p12-fake"` →
  the turn narrates the MODEL's prose → fake answers 500 → **502
  `provider_failed`**, scrubbed message, nothing persisted, no cover prose, no
  key in the body → same action + same key after the heal → **200** → `GET
  /ai/settings` `has_key:true` and never echoes the key → `/actions/submit`
  without an env provider → 400 `connect_your_ai` → key hygiene (key reached
  the provider via `Authorization`, absent from the server log; the account's
  own key IS stored server-side by design — that is the player's credential for
  their endpoint, write-only).
- Browser probe (Next dev :3001 + backend :8015 + fake :8023, scratch account;
  DOM asserts + screenshots in `/opt/data/kanban-evidence/`, md5s in the card):
  `p12-ui-1-settings-not-connected.png` — "Right now: nothing connected — no
  endpoint is saved yet.", Save disabled, no storyteller option anywhere;
  `p12-ui-2-adventure-gated.png` — `data-gate="connect"` notice + reason + link,
  act input disabled; `p12-ui-3-demo-signed-out.png` — signed out reads
  `data-gate="demo"` ("Sample tale — a demo, not your own"); `p12-ui-4` — Save
  in Settings → "Right now: your endpoint (p12-fake) — from your settings.";
  `p12-ui-5-adventure-played.png` — gate gone, "✎ Narrated by your endpoint
  (p12-fake)", the fake's prose as narration; `p12-ui-6-provider-failure.png` —
  the 500 rendered verbatim in the shared banner + "Kept: “I settle my pack on
  my shoulders.”" + Retry; `p12-ui-7-retry-landed.png` — Retry (same key)
  landed the narration, banner gone; `p12-ui-8/9` — Test connection verdicts
  (answered in 1 ms / "OpenAI-compatible request failed: HTTP Error 500");
  `p12-ui-10/11` — a called Investigation check: "The die waits…" → thrown →
  "Investigation check — vs DC 13 · 19 = 19 · success" (the new `rollCheck`
  shape). The `offline` gate branch is unit-covered (`lib/ai.test.ts`), not
  browser-driven.
- Commits/push: `873fc43` (backend) + `e81ef92` (frontend) + `df70b1a` (docs) +
  `496972e` (failure wording, tip); pushed `01a7431..496972e` to `origin/main`;
  `git log origin/main..HEAD` empty afterwards. Owner redeploys manually
  (Coolify) — the runbook's §3 now walks the story game's four states too.

### P7 finalize block (post-deploy live verification, filled during the card)

- Deploy verified live on the owner's redeploy: the frontend chunk fingerprint
  moved (5 added / 3 removed, the `/chronicle` chunk among them) and
  `openapi.json` carries the five `/engine/*` paths — the running build is the
  phase-2 tip `3327c8d`. ("Stub play" no longer exists to verify: P11 made the
  pilot require a connected model, so the keyed turn below is the live flow.)
- No-key legs (Phase A, `p7_live_verify.py`, transcript `p7_live_verify.txt`,
  md5 `70686241…`): **19/19** — backend health; openapi lists the pilot; the nav
  carries Chronicle; `/chronicle` SSR renders the pilot, not the flag-off notice;
  scratch register + campaign create; **unconnected pilot turn refused
  `400 connect_your_ai` with the state snapshot unchanged**; `GET
  /engine/connection` reads unconnected; **`PUT provider=stub` rejected**
  (`provider 'stub' is not available; supported: openai, openai-compatible`); a
  stray `api_key` in the PUT body is neither stored nor echoed; a *live provider
  without a key* is refused the same way; story side: `GET /ai/settings` names
  `unset`, and `/act` refuses `connect_your_ai` with nothing persisted.
- Live BYOK turns (Phase B, `p7_live_turn.py`, transcript `p7_live_turn.txt`,
  md5 `21b942ae…`): **15/15**. The sandbox is unreachable from the deployed
  backend (NAT), so the run goes through a public quick tunnel to a controlled
  OpenAI-compatible fake on this box with a dummy runtime key — three real turns
  against the deployed stack: canary verdict `reachable + native tools` → turn 1
  `200 0.2s mode=live native_tools=true degraded=false`, narration is the model's
  prose, and a real called check rides the payload (`Perception check vs dc 12:
  SUCCESS (19 vs 12, margin 7)`, roll 17) → turn 2 with `X-Content-Prefs: nsfw
  on` flips the applied echo → turn 3 with a malformed prefs header applies
  defaults + the visible `CONTENT_DEFAULTS_NOTE` → state advanced three turns →
  **the runtime key is absent from every response** and **arrives at the provider
  via `Authorization`** alongside the engine tool schemas → the NSFW prompt
  carries the uncensored directive + the hard minors exclusion, the default
  prompt the standard directive.
- Degraded / failure / recovery legs on the same live stack
  (`p7_live_degraded.py` 7/8 + `p7_live_healed.py` 3/3): a tool-less model (no
  `report_capability` call) caches `native_tools=false` and the turn still answers
  `200 mode=live degraded=true` with prose via the JSON fallback; a provider
  answering `500` yields the honest `502 {"detail":"provider HTTP 500: …"}`, key
  scrubbed, turn counter unchanged; after the heal a fresh model name re-probes
  and the native path answers `200 native_tools=true`.
- Live UI (real browser, scratch account; files in `/opt/data/kanban-evidence/`):
  `t_da57fd43-ui-chronicle-turn.png` (md5 `7b6e3e26…`) — a played pilot turn with
  "◈ p7-ui-model", the echoed action, the model's prose, the mechanics line, the
  suggestion chips and "a key is kept in this browser"; the fake's request log
  also shows the browser store's content prefs riding the prompt (reduced/off
  directives), so the request-time prefs read is proven from the real client.
  `t_da57fd43-ui-adventure-gate.png` (md5 `69d520e4…`) — `data-gate="connect"` +
  the P12 wording, act input disabled. `t_da57fd43-ui-degraded-banner.png`
  (md5 `172beee3…`) — the tool-less model: "⚠ Compatibility mode — … using its
  JSON fallback. Turns work; the prose is plainer." + the `compatibility` chip.
  The full request log is kept at `t_da57fd43-fake-provider-log.jsonl`
  (md5 `0493c9f6…`).
- Live finding (needs the owner's ruling; not fixed here — `engine/` and
  `backend/app/modules/engine/` are frozen by the standing owner directive): in
  the JSON-fallback path a reply whose *text* is empty (model answered only with
  tool calls, or nothing) is retried once, then shipped as empty prose — the turn
  commits, the feed shows `◈ narrator: json: the reply was empty; regenerating` /
  `… regeneration exhausted, using the reply as prose`, and that action's
  chronicle line has no narration. Reproduced live twice (API and UI). A real
  tool-less model answers with text, so it takes a model returning empty content —
  rare, but the player silently loses a turn's prose when it happens.
- Remaining owner steps: the real BYOK turn on a phone (`/chronicle` → Connect
  your AI → base URL + model + key → Save → Test connection → play a turn), craft
  notes on real-model prose, and the story game's connected legs (`/adventure` and
  Settings → Tale-spinner with the same key). This card changed docs only — no
  deploy is needed for it.
