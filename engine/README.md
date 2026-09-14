# Lorebound Engine (rebuild)

The rebuilt game engine: an AI Dungeon Master system where the model narrates
and proposes, while code owns all state (spec: `../docs/REBUILD_SPEC.md`).

Status: built by the rebuild worker chain (2026-09-13/14). As-built notes and
the verification report live in `../docs/REBUILD_NOTES.md` and
`../docs/REBUILD_VERIFICATION.md` (created by the finalize/verify cards).

## Layout

- `src/engine/` — the engine package (see `ARCHITECTURE.md` for the module map)
- `tests/` — pytest suite, run from `engine/`
- `evals/` — the eval harness and scenario suites (spec §9)
- `ARCHITECTURE.md` — contracts, decisions, ownership, gates

## Commands (from `engine/`)

```bash
# tests (venv lives in the MAIN repo tree; worktrees use this absolute path)
/opt/data/Lorebound-AI-DND/engine/.venv/bin/python -m pytest tests -q
/opt/data/Lorebound-AI-DND/engine/.venv/bin/ruff check src tests

# play (stub narrator needs no API key; real models are BYOK)
.venv/bin/python -m engine play --stub
.venv/bin/python -m engine play --provider openai --model gpt-4o-mini --api-key-env OPENAI_API_KEY

# evals (§9 harness)
.venv/bin/python -m engine.evals
```

## Bring your own key (BYOK)

Keys are runtime inputs only — passed via env var name (`--api-key-env`) or
config at launch, never written to the DB, logs, or prompts. Supported wire
families: OpenAI chat/completions-compatible (incl. local servers), Anthropic
messages, Gemini generateContent. Weak/no tool-calling models degrade to a
strict JSON-in-text protocol with regeneration (§7).
