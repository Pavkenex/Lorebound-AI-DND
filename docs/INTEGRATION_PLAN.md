# Integration Plan — Rebuilt Engine → Live App (Phase 2)

Status: **settled** (owner decisions 2026-09-14). This is the spec for the phase-2
cards on kanban board `default` (`created_by='phase2'`, P1–P7). If a card body and
this file disagree, this file wins — note the discrepancy in the card's completion.

Owner decisions (locked):
1. **In-process import** (option b): the engine ships inside the backend container
   and runs in-process. No separate service, no new Coolify resource.
2. **BYOK keys are runtime-only, never persisted.** The key stays in the player's
   browser, travels per request, and lives in server memory for that request only.
   Nothing at rest — no DB column, no log, no prompt. (Audit of the legacy BYOK:
   its call path and stored key do NOT match this; see §4. The legacy path stays
   untouched for the old narrator until retirement.)
3. **Providers: `openai-compatible` only for now** (covers OpenAI + any
   OpenAI-compatible/local server). The engine's Anthropic/Gemini adapters exist
   and stay tested, but are not exposed in the app UI — deferred, not deleted.
4. **Engine turns call through the engine's provider layer** (`engine/providers/`:
   tool-call protocol → capability canary → JSON fallback). The legacy
   `app/modules/ai/providers.py` (plain text, no tools) is NOT on the engine path.
   No shared abstraction now — the contracts differ; unifying would touch the live
   game for zero pilot benefit. The legacy layer retires with the old narrator.

## 1. Architecture (option b)

- `engine/` (Python 3.13, stdlib-only runtime) is installed into the backend image
  (`pip install /opt/engine`) and imported in-process: `app/modules/engine/`.
- The app keeps: Postgres (accounts, campaigns, saves), the existing FastAPI app,
  and — unchanged — the legacy narrator/play stack for the current game modes.
- New env: `ENGINE_MODE` (default off; router 404s when off) and
  `ENGINE_DATA_DIR` (engine SQLite files; mounted volume in deploy).
- Frontend: `NEXT_PUBLIC_ENGINE_MODE` gates the new pilot route — hidden by default.

## 2. One turn, end to end

```
player types in /chronicle
  → POST /engine/campaigns/{id}/turns   {text}      (+ X-Provider-Key header when a live provider is chosen)
  → FastAPI: require_user + scope_campaign (404 on cross-account)
  → bridge: per-campaign lock → build engine session + adapter (runtime key)
  → engine pipeline: Pass A → Pass B (adapter call; tools or JSON fallback) → Pass C validator commit → Pass D scan
  → response: {narration, dialogue, mechanics.verdict_line, suggestions, capability{native_tools,degraded}, state}
  → state committed in {ENGINE_DATA_DIR}/{campaign_id}.db (WAL); last_turn_at updated in Postgres
```

Turn latency = 1–2 provider calls (seconds). Sync endpoint in the threadpool —
same shape as the existing `/act`. Idempotency keys: **out of scope** for the pilot
(noted gap).

## 3. Storage & ownership

- One engine SQLite file per campaign: `{ENGINE_DATA_DIR}/{campaign_id}.db`.
  Single writer per turn enforced by a per-campaign lock in the bridge.
- Postgres: existing `campaigns` row (owner = `owner_user_id`; reuse
  `require_user` + `scope_campaign`) + new side-table `engine_campaigns`
  (campaign_id PK → campaigns.id CASCADE, world, engine_version, last_turn_at).
- New `engine_connection_settings` (user_id PK → users.id, provider, base_url,
  model, timeout_s, updated_at) — **no key column, by design** (see §4).

## 4. BYOK — runtime-only key contract

- Key input lives in the browser (`localStorage`), sent per request via the
  `X-Provider-Key` header over HTTPS; the server holds it in memory for that one
  call and hands it to the engine adapter. It is never written to the engine DB,
  the app DB, logs, or prompts.
- Verified engine-side guarantees to preserve: `build_adapter(cfg, api_key=...)`
  takes the key as a runtime input; the capability cache key is
  `name|model|base_url|api_mode` — explicitly no key material; provider errors are
  scrubbed by the engine's redaction layer.
- Non-secret prefs (provider, base_url, model, timeout_s) ARE stored server-side —
  convenience for phone + desktop; harmless.
- Capability canary: probed on first live use (and via
  `POST /engine/connection/check`), verdict cached in the campaign DB (no key
  material), surfaced in turn responses + UI (badge, degraded banner).
- P3 tests must pin: after real/fake turns the key appears in NO database file,
  NO Postgres row, and NO captured logs; error paths are scrubbed.

Legacy audit summary (why the legacy row is not reused for the engine): legacy
stores the raw key in Postgres (plaintext at rest) and its provider layer cannot
do tool calls or capability probing — acceptable for the old narrator, not for the
engine. The settings *panel* UX is the reusable part.

## 5. HTTP surface (new, flag-gated)

- `POST /engine/campaigns` {name} → 201 campaign
- `GET  /engine/campaigns` → mine
- `POST /engine/campaigns/{id}/turns` {text} (+ `X-Provider-Key`) → turn payload
- `GET  /engine/campaigns/{id}/state` → snapshot
- `GET/PUT /engine/connection` → non-secret prefs (no key field exists)
- `POST /engine/connection/check` (+ `X-Provider-Key`) → {reachable, native_tools, detail}
- When `ENGINE_MODE` is off every `/engine/*` path returns 404.
- Errors: 400 `{"detail":"connect_your_ai"}` when a live provider is selected but
  no key is present; 502 with scrubbed provider errors otherwise.

## 6. Deploy (owner steps; no new container)

1. Repo `main` carries: Dockerfile (build context = repo root, installs `engine/`),
   compose (`engine_data` volume; `ENGINE_MODE`, `ENGINE_DATA_DIR` envs).
2. Coolify → backend service: set `ENGINE_MODE=1`, `ENGINE_DATA_DIR=/code/engine_data`;
   add a persistent volume mounted at `/code/engine_data`; make sure the build
   context/base directory is the **repo root** with Dockerfile `backend/Dockerfile`;
   redeploy.
3. Frontend service: set `NEXT_PUBLIC_ENGINE_MODE=1`; redeploy.
4. Verify: `curl /health`; create a campaign; stub turn succeeds (no key needed —
   stub provider is the default and needs no model call).
5. Rollback: unset `ENGINE_MODE` (and/or `NEXT_PUBLIC_ENGINE_MODE`); redeploy. The
   pilot is additive — the old game is untouched either way.

## 7. Frontend pilot (`/chronicle`, flag-gated)

Connect panel (key → localStorage, "your key stays in this browser"; Save & test
with capability verdict), campaign list, play view (input, narration, dialogue,
verdict chip, suggestion chips), state panel (GET /state), degraded banner when
`native_tools === false`. Existing screens untouched.

## 8. Out of scope (this phase)

Anthropic/Gemini app-side choices; retirement/replacement of the legacy
narrator+play stack; multiplayer; content authoring beyond the demo fixture;
idempotency keys; travel/location mechanic (engine gap — later card).

## 9. Risks & mitigations

- **First live BYOK call never made** (rebuild gap §4.1): P7 is exactly this;
  plan one cheap-model live turn right after deploy.
- **Build-context change** affects Coolify settings: docker daemon is unavailable
  in the dev container, so P1 verifies install + import locally and P5 documents
  the exact Coolify steps; first redeploy is the true test (owner-driven).
- **Engine turn latency**: acceptable single-player; UI shows a pending state.
- **Coexistence**: everything additive behind `ENGINE_MODE`; legacy rows/keys
  untouched; no forced migration.

## 10. Card graph (board `default`, created_by='phase2')

```
P1 packaging (main) → P2 bridge module (main) → P3 router (main) → ┬ P4 frontend pilot (worktree p2/frontend)
                                                                   └ P5 deploy docs (main)
P6 ship: merge P4, full batteries, push (main) → P7 parked: post-deploy live verify + first BYOK turn (owner-gated)
```

Gates used everywhere: backend suite + ruff (backend/.venv); engine suite + ruff
(engine/.venv) must stay green whenever engine files are touched; frontend
`npm run typecheck && npm run build` for P4/P6; stub-provider local e2e; the
key-never-stored test set from §4.

P7 stays **unassigned** (parked) until the owner has redeployed with
`ENGINE_MODE=1`; the phase-2 watch treats it as parked and still reports code
completion when P1–P6 are done.
