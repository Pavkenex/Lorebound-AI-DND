"""Alembic environment: imports every models module so autogenerate and the
initial create_all migration see the full metadata (Stream A, t_308fff7c)."""
from __future__ import annotations

import os
import sys
from logging.config import fileConfig

from alembic import context
from sqlalchemy import engine_from_config, pool

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

config = context.config
if config.config_file_name is not None:
    fileConfig(config.config_file_name)

from app.core.database import Base  # noqa: E402

# Every models module, so Base.metadata is complete. Sibling streams are
# optional: env must keep working even if one is mid-edit.
for _mod in (
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
):
    try:
        __import__(_mod)
    except Exception:
        pass

target_metadata = Base.metadata


def _url() -> str:
    return os.environ.get("DATABASE_URL", "postgresql+psycopg://lorebound:lorebound@localhost:5432/lorebound")


def run_migrations_offline() -> None:
    context.configure(url=_url(), target_metadata=target_metadata, literal_binds=True)
    with context.begin_transaction():
        context.run_migrations()


def run_migrations_online() -> None:
    cfg = config.get_section(config.config_ini_section, {}) | {"sqlalchemy.url": _url()}
    connectable = engine_from_config(cfg, prefix="sqlalchemy.", poolclass=pool.NullPool)
    with connectable.connect() as connection:
        context.configure(connection=connection, target_metadata=target_metadata)
        with context.begin_transaction():
            context.run_migrations()


if context.is_offline_mode():
    run_migrations_offline()
else:
    run_migrations_online()
