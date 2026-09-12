# Lorebound AI DND

Persistent role-playing world where you can attempt anything and the world remembers what happened.

Modular monolith: FastAPI backend + Next.js frontend, Postgres + Redis via Docker Compose.

## Quickstart

```bash
cp .env.example .env
docker compose up --build
```

- Game: http://localhost:3000
- API: http://localhost:8001 (docs at /docs, health at /health)
- Mailhog-free local dev: backend unit tests run without Docker.

## Play it locally (no models required)

The whole vertical slice runs on the deterministic **stub provider** — no API
keys, no network. Two terminals:

```bash
# terminal 1 — backend (SQLite by default, AI_PROVIDER=stub)
cd backend
python3 -m venv .venv && source .venv/bin/activate
pip install -e ".[dev]"
uvicorn app.main:app --port 8001

# terminal 2 — frontend
cd frontend
npm ci && npm run dev          # http://localhost:3000
```

Then in the browser: **Sign in → register** (any email, password 8+), hit
**New Journey**, and play. What works end to end:

- The Lantern Inn: talk to Marla (she remembers thefts, brawls, and questions
  across visits), inspect the notice board / ledger / cellar door, steal or
  fight, leave and return.
- The mystery: three clues (ledger, cart tracks, lanterns) and five
  skill-gated solution paths (persuasion, intimidation, investigation, stealth,
  swordsmanship) — through the road ambush and the monastery cellar to the
  epilogue; the campaign flips to *completed*.
- Saving: manual saves and autosaves (checkpoints on travel, combat, theft,
  key dialogue) round-trip the whole live state; **Load** restores it server-side.
- Screens: character, skills (mastery XP earned in play), journal (lead graph
  reveals with progress), map, inventory, companions — all live.
- Character creation: **Forge your own hero** (6 stages) replaces the default
  protagonist on the live sheet.
- Real dice: checks roll the 3D d20 in the feed (see *The dice* below).

To use a real model instead of the stub, set `AI_PROVIDER=openai-compatible`
plus `OPENAI_COMPAT_BASE_URL` / `OPENAI_COMPAT_MODEL` (see `.env.example`).
Optional: `AI_COST_PROMPT_PER_1K` / `AI_COST_COMPLETION_PER_1K` make the cost
meter report real USD; the stub honestly reports 0.

When the backend is unreachable the UI degrades to local fixture pages (every
screen shows "backend unreachable") — nothing breaks, but play is not saved.

## Local backend dev (no Docker)

```bash
cd backend
python3 -m venv .venv && source .venv/bin/activate
pip install -e ".[dev]"
pytest -q          # full suite incl. the start-to-finish journey test
ruff check app tests
uvicorn app.main:app --reload
```

## Verify (CI gates)

```bash
cd backend && .venv/bin/python -m pytest tests -q && .venv/bin/ruff check app tests
cd frontend && npm run typecheck && npm test && npm run build
```

## Layout

- `backend/app/core/` — config, database, security, shared event bus.
- `backend/app/modules/<domain>/` — one bounded context per directory (see OWNERSHIP.md).
- `backend/app/modules/play/` — the live play layer: per-campaign session state,
  the action engine (`POST /act`), state view (`GET /state`), screen payloads,
  and 6-stage character creation.
- `backend/app/content/` — authored Ravenford seed data (JSON) + IP glossary.
- `frontend/` — Next.js + TypeScript adventure UI.
- `.github/workflows/ci.yml` — lint + unit tests + migration check.

## Engine invariants (GDD)

- The AI is never the database: model output arrives as typed *proposals*, the engine decides.
- Player text is an attempted action only; it can never establish a world fact directly.
- Time, HP, inventory, XP, reputation, and quest state are authoritative engine values.

## The dice

Checks roll a real 3D d20 in the adventure feed — an icosahedral die drawn on
canvas from projected geometry, zero dependencies. It tumbles with decaying
angular velocity, hops, and settles exactly on the rolled face; a natural 20
gets a golden burst and a natural 1 a dire slam with ash. `Settings → The dice`
previews all three. Engine math: `frontend/lib/dice3d.ts`; unit tests:
`cd frontend && npm test` (Node ≥ 22.6 — TypeScript stripped natively).
