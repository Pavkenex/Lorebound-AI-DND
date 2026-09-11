# Lorebound backend

FastAPI modular monolith. See the repo-root README for the one-command run.

## Local dev (no Docker)

```bash
python3 -m venv .venv && source .venv/bin/activate
pip install -e ".[dev]"
pytest -q
uvicorn app.main:app --reload
```
