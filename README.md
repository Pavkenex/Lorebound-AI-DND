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

## Local backend dev (no Docker)

```bash
cd backend
python3 -m venv .venv && source .venv/bin/activate
pip install -e ".[dev]"
pytest -q
uvicorn app.main:app --reload
```

## Layout

- `backend/app/core/` — config, database, security, shared event bus.
- `backend/app/modules/<domain>/` — one bounded context per directory (see OWNERSHIP.md).
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
