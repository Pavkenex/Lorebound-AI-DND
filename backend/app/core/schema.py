"""Schema bootstrap: import every models module, then create_all (idempotent).

Why: a fresh SQLite file (local dev) or a fresh Postgres from compose has no
tables, and the app must be playable without a migration chore. ``create_all``
only creates *missing* tables; Alembic remains the path for evolving schemas
(``alembic upgrade head``).

Optional streams (a module mid-edit) must never block boot, so imports are
best-effort per module.
"""
from __future__ import annotations

import importlib

from app.core.database import Base, engine

_MODEL_MODULES: tuple[str, ...] = (
    "app.modules.auth.models",
    "app.modules.campaign.models",
    "app.modules.campaign.npc",
    "app.modules.campaign.story",
    "app.modules.campaign.world",
    "app.modules.character.models",
    "app.modules.inventory.models",
    "app.modules.rules.models",
    "app.modules.actions.models",
    "app.modules.narrator.models",
    "app.modules.memory.models",
    "app.modules.world.models",
    "app.modules.npc.models",
    "app.modules.story.models",
    "app.modules.journal.models",
    "app.modules.progression.models",
    "app.modules.exploration.models",
    "app.modules.combat.models",
    "app.modules.economy.models",
    "app.modules.history.models",
    "app.modules.play.models",
)


def ensure_schema() -> None:
    """Import all model modules and create any missing tables."""
    for module in _MODEL_MODULES:
        try:
            importlib.import_module(module)
        except Exception:  # noqa: BLE001, S112 - optional stream boundary, by design
            continue
    Base.metadata.create_all(bind=engine)
