# Integration Deploy Runbook — engine pilot (phase 2)

Owner steps to switch the rebuilt engine on in the live app, verify it, and roll it
back. Written against `main` at `5b98f46` (P1–P3 landed; the frontend pilot merges in
P6). The spec is `docs/INTEGRATION_PLAN.md` §6, and `docs/INTEGRATION_NOTES.md` records
what the code actually shipped. No new Coolify resource, no database migration step: the
engine runs **in-process** inside the existing backend container.

Two reminders before you start:

- **Deploys are manual.** A push alone changes nothing; the site serves the old build
  until you click **Redeploy** on the service. After the frontend redeploy, hard-refresh
  (Ctrl+Shift+R) before judging anything.
- Both flags default to **off**. Every step below is additive: with the flags unset the
  live game behaves exactly as it does today.

---

## 1. Backend service — FastAPI

- [ ] **Build config.** In the Coolify service → Build settings, make sure:
  - Build pack: **Dockerfile**
  - **Base Directory: the repository root** (not `backend/`)
  - **Dockerfile Location: `/backend/Dockerfile`**

  This is the one setting that changed in this phase: `backend/Dockerfile` now expects
  the repo root as its build context (it copies `engine/` in). If the base directory is
  still `backend/`, the build fails at `COPY engine /opt/engine`.

- [ ] **Environment variables** (service → Environment Variables):
  - `ENGINE_MODE=1`
  - `ENGINE_DATA_DIR=/code/engine_data`

- [ ] **Persistent volume.** Add a persistent storage mount at **`/code/engine_data`**
  (Coolify → Storages → add volume/bind). Each pilot campaign's game state is one SQLite
  file (`<campaign-uuid>.db`) in that directory; without a persistent mount they are lost
  on every redeploy. The directory is created on demand — an empty mount is fine.

- [ ] **Redeploy.** The first build with the new context runs, in order:
  ```
  COPY engine /opt/engine
  COPY backend/pyproject.toml backend/README.md ./
  COPY backend/app ./app
  RUN pip install --no-cache-dir -e . /opt/engine
  ```
  Watch the build log for those four steps (this is the first time this Dockerfile runs
  in the image builder — see §5).

## 2. Frontend service — Next.js

- [ ] **Environment variable:** `NEXT_PUBLIC_ENGINE_MODE=1`.
- [ ] **Redeploy**, then **hard-refresh** the site.

  `NEXT_PUBLIC_*` values are compiled into the browser bundle during the image build —
  the deployment already works this way for `NEXT_PUBLIC_API_URL` (the live bundle has
  the API host inlined into `/_next/static/chunks/*.js`). Setting the variable on this
  service is therefore enough; no Dockerfile change is needed. The pilot's route/nav link
  simply does not exist in the bundle while the flag is absent.

## 3. Verify (stub provider — no key, no model call)

A fresh account's connection defaults to `stub`, so the whole loop below needs no
provider key and no AI spend. Replace `<host>` with the live host (`130.61.50.108`
today: backend `:8001`, frontend `:3000`, Coolify dashboard `:8000`).

- [ ] **Backend health**
  ```bash
  curl -s http://<host>:8001/health
  # {"status":"ok","service":"lorebound-backend"}
  ```
- [ ] **Pilot is advertised** (flag on):
  ```bash
  curl -s http://<host>:8001/openapi.json | grep -c '/engine/'
  # >0 with ENGINE_MODE=1, 0 without
  ```
- [ ] **Register** (or use your account and `/auth/login`):
  ```bash
  curl -s -X POST http://<host>:8001/auth/register \
    -H 'Content-Type: application/json' \
    -d '{"email":"pilot-check@example.com","password":"change-me-123","display_name":"Pilot Check"}'
  # 201 {"user":{...},"token":{"access_token":"..."}}
  ```
  ```bash
  TOKEN=...   # token.access_token from above (or POST /auth/login, form-encoded)
  ```
- [ ] **Create a pilot chronicle**
  ```bash
  curl -s -X POST http://<host>:8001/engine/campaigns \
    -H "Authorization: Bearer $TOKEN" -H 'Content-Type: application/json' \
    -d '{"name":"Pilot check"}'
  # 201 {"id":"<uuid>","name":"Pilot check","world":"demo","last_turn_at":null}
  ID=<uuid>
  ```
- [ ] **One stub turn** (no `X-Provider-Key` header — that is the point):
  ```bash
  curl -s -X POST http://<host>:8001/engine/campaigns/$ID/turns \
    -H "Authorization: Bearer $TOKEN" -H 'Content-Type: application/json' \
    -d '{"text":"I take stock of the road ahead."}'
  # 200: {"turn":1,"narration":"…","dialogue":[…],"capability":{"mode":"stub",…},
  #       "state":{…},"mechanics":{…},"suggestions":[…],"system_lines":[…],
  #       "content":{"nsfw":false,"violence":"standard",…}}  # applied boundaries
  ```
- [ ] **State snapshot**
  ```bash
  curl -s http://<host>:8001/engine/campaigns/$ID/state -H "Authorization: Bearer $TOKEN"
  # 200 {"turn":1,"location":…,"present_npcs":[…],…}
  ```
- [ ] **Campaign file exists** (inside the backend container / on the mounted volume):
  `/code/engine_data/$ID.db` (plus `-wal`/`-shm` siblings while playing).
- [ ] **Frontend:** open the site, sign in — the pilot route (`/chronicle` and its nav
  link) is visible with `NEXT_PUBLIC_ENGINE_MODE=1`, and hidden without it. The connect
  panel lives on that route; do not paste a real provider key until you are ready for the
  P7 live-BYOK check (see `docs/INTEGRATION_NOTES.md` §"known gaps").

## 4. Rollback

- [ ] Unset `ENGINE_MODE` (or set `ENGINE_MODE=0`) on the backend service and/or unset
  `NEXT_PUBLIC_ENGINE_MODE` on the frontend service, then redeploy.

  Effects: every `/engine/*` path answers the framework's 404 again (before auth, before
  body validation), `/openapi.json` stops listing the pilot, and the frontend bundle no
  longer carries the route. The legacy game and its stored settings are untouched either
  way. Leave the `engine_data` volume mounted — it holds pilot campaigns for whenever the
  flag goes back on.

## 5. What the first build must prove (and where to look if it fails)

The build cannot be exercised in the dev container (no Docker daemon), so the first
Coolify build is the true test. What it proves, in order:

1. **Repo-root context is configured** — otherwise `COPY engine /opt/engine` errors with
   "not found" while the log shows a `backend/`-relative path.
2. **`engine/` (stdlib-only, py3.13) builds and installs on `python:3.13-slim`** —
   `Successfully built lorebound-engine`. A packaging regression shows up here, e.g.
   `engine/store/schema.sql` missing from the wheel (it is declared as package data).
3. **The backend installs with both packages** — `Successfully installed … lorebound-engine
   … lorebound-backend`.
4. **The app imports with the engine present** — the container starts and `/health`
   answers; a missing engine install degrades to "pilot 404s" (the router is an optional
   stream), not a boot crash.
5. **`ENGINE_MODE=1` reaches the process** — `/openapi.json` lists `/engine/*` and §3's
   stub turn succeeds.
6. **The volume is writable** — the campaign `.db` appears under `/code/engine_data` and
   survives a second redeploy (re-fetch `/state` after redeploying again).

Troubleshooting:

| Symptom | Cause / fix |
|---|---|
| Build fails at `COPY engine /opt/engine` | Base directory is not the repo root (§1). |
| Build fails at `pip install` with "requires a different Python" | Builder image is not Python 3.13 (`backend/Dockerfile` pins `python:3.13-slim`). |
| Container healthy but `/engine/*` 404s | `ENGINE_MODE` not set/empty/`0` on the backend service, or the redeploy did not rebuild. Check `/openapi.json`. |
| Turn answers 400 `connect_your_ai` | The account's saved provider is a live one but no key was sent — expected for a live provider; switch the connection to `stub` (or send the key from the pilot UI). |
| Campaign turns work, but the campaign is gone after a redeploy | `/code/engine_data` is not a persistent mount (§1). |
| Frontend never shows the pilot | `NEXT_PUBLIC_ENGINE_MODE` was not present at image build (or an old bundle is cached — hard-refresh first). It must be built into the bundle; a runtime-only env var cannot turn it on. |

## 6. Where this leaves the phase

- P7 (post-deploy live verify + the first real BYOK turn) is parked until this runbook
  has been executed with `ENGINE_MODE=1`; it is the owner-gated card on the board.
- The engine pilot is additive and reversible; the legacy narrator/play stack and its
  legacy stored-key settings stay untouched (retirement is a later card).
