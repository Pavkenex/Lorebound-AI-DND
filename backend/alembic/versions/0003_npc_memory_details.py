"""Add structured NPC memory columns to npc_memories (kind/sentiment/day/hour).

Revision ID: 0003_npc_memory_details
Revises: 0002_ai_provider_settings
"""
from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "0003_npc_memory_details"
down_revision = "0002_ai_provider_settings"
branch_labels = None
depends_on = None

# Import side effects register the model on Base.metadata (bootstrap style
# shared with 0001/0002: create_all only creates missing tables).
import app.modules.campaign.npc  # noqa: F401
from app.core.database import Base

_COLUMNS = (
    ("kind", "VARCHAR(32) NOT NULL DEFAULT ''"),
    ("sentiment", "INTEGER NOT NULL DEFAULT 0"),
    ("day", "INTEGER NOT NULL DEFAULT 1"),
    ("hour", "INTEGER NOT NULL DEFAULT 0"),
)


def upgrade() -> None:
    Base.metadata.create_all(bind=op.get_bind())
    inspector = sa.inspect(op.get_bind())
    if not inspector.has_table("npc_memories"):
        return
    existing = {c["name"] for c in inspector.get_columns("npc_memories")}
    for column, ddl in _COLUMNS:
        if column not in existing:
            op.execute(f"ALTER TABLE npc_memories ADD COLUMN {column} {ddl}")


def downgrade() -> None:
    inspector = sa.inspect(op.get_bind())
    if not inspector.has_table("npc_memories"):
        return
    existing = {c["name"] for c in inspector.get_columns("npc_memories")}
    for column, _ddl in reversed(_COLUMNS):
        if column in existing:
            with op.batch_alter_table("npc_memories") as batch:
                batch.drop_column(column)
