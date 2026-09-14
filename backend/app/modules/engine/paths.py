"""Campaign file layout for the in-process engine (docs/INTEGRATION_PLAN.md §3).

One engine SQLite file per campaign: ``{ENGINE_DATA_DIR}/{campaign_id}.db``.
The campaign id is validated against the shape the app issues (uuid4 text)
*before* it can reach the filesystem: the allowed character class excludes every
path separator, so a crafted id can never escape the data directory.
"""
from __future__ import annotations

import re
from pathlib import Path

from app.core.config import settings

#: Shape of ``campaigns.id`` (uuid4 text): 36 chars of hex + dashes, nothing else.
CAMPAIGN_ID_RE = re.compile(r"[0-9a-fA-F-]{36}")

#: Used when ENGINE_DATA_DIR is unset/blank (deploys set an absolute volume path).
DEFAULT_DATA_DIR = "./engine_data"


class EnginePathError(ValueError):
    """A campaign id that cannot be mapped to a safe on-disk file path."""


def engine_data_dir() -> Path:
    """Directory holding engine campaign files (settings read at call time)."""
    raw = str(getattr(settings, "ENGINE_DATA_DIR", "") or "").strip()
    return Path(raw or DEFAULT_DATA_DIR)


def campaign_db_path(campaign_id: str) -> Path:
    """``{ENGINE_DATA_DIR}/{campaign_id}.db``; creates the directory if needed.

    Raises :class:`EnginePathError` for anything that is not 36 hex/dash
    characters — no slashes, no dots, no traversal, ever.
    """
    text = str(campaign_id or "").strip()
    if not CAMPAIGN_ID_RE.fullmatch(text):
        raise EnginePathError("campaign id must be 36 characters of [0-9a-fA-F-]")
    directory = engine_data_dir()
    directory.mkdir(parents=True, exist_ok=True)
    return directory / f"{text}.db"
