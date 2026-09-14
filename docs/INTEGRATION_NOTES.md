# Integration Notes — rebuilt engine in the live app (phase 2, as-built)

What actually shipped when the rebuilt engine (`engine/`, Python 3.13, stdlib-only) was
wired into the live FastAPI + Next.js app **in-process**. The settled spec is
`docs/INTEGRATION_PLAN.md`; the owner's deploy steps are `docs/INTEGRATION_DEPLOY.md`.
Readers who only want to deploy can stop at the runbook.

As-built status at this writing (`main` = `5b98f46`):

| Card | Scope | State |
|---|---|---|
| P1 | Packaging — engine installs into the backend (image + dev env) | landed `3fb40ed`, `485d307` |
| P2 | Bridge module — campaign lifecycle, turn wrapper, runtime-key flow | landed `070e96f`, `b41ee25`, `af98d73` |
| P3 | HTTP router — flag-gated `/engine` surface, auth, key header | landed `6844b6d`, `7161a4b`, `30dc11d`, `5b98f46` |
| P4 | Frontend pilot — `/chronicle` behind `NEXT_PUBLIC_ENGINE_MODE` | worktree branch `p2/frontend`, merged and re-verified by **P6** |
| P5 | Deploy docs + compose/deploy-artifact re-verification | this document + `INTEGRATION_DEPLOY.md` |
| P6 | Ship — merge the frontend, full batteries, push | fills the *P6 finalize* block at the bottom |
| P7 | Post-deploy live verify + first real BYOK turn | **parked** until a deploy sets `ENGINE_MODE=1` |

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

Engine side — only packaging moved; no engine runtime code changed in phase 2:

- `engine/pyproject.toml` — explicit `setuptools.build_meta` backend; `store/schema.sql`
  declared as package data (`[tool.setuptools.package-data] engine = [...]`) so a
  non-editable install ships the file `engine.store` reads on every connect.

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
  → POST /engine/campaigns/{id}/turns {text}  ──────────────────────────────┐
  → FastAPI: require_engine_mode → require_user → scope_campaign (404 cross-account)
  → bridge.take_turn(db, user_id, campaign_id, text, provider_key=<header>) │
      → connection prefs (non-secret) resolve provider (default "stub"; key required only when live)
      → per-campaign threading.Lock (single writer)
      → PlaySession.start(db_path=…/{campaign_id}.db, adapter=…, provider=…)
      → engine.act(): intent → Pass A–D → capability canary on first live use
        (verdict cached in the campaign DB's provider_caps table — no key material)
  → payload: turn, narration, dialogue[{npc_id,name,text}], mechanics, suggestions,
             capability{provider,model,mode,native_tools,degraded}, state, system_lines
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

---

### P6 finalize block (fill in during the ship card)

- Frontend merge hash: `<p6>`
- Batteries on merged `main`: backend `<n> passed`, engine `<n> passed`, frontend
  `typecheck`/`test`/`build` `<result>`, stub e2e `<result>`
- Pushed hash: `<p6>` (`origin/main`), and the key-absence test reference to quote from
  `test_engine_router.py`.
