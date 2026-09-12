"""Add ai_provider_settings (bring-your-own AI provider, per user).

Revision ID: 0002_ai_provider_settings
Revises: 0001_initial
"""
from __future__ import annotations

from alembic import op

revision = "0002_ai_provider_settings"
down_revision = "0001_initial"
branch_labels = None
depends_on = None

# Import side effects register the model on Base.metadata (bootstrap style
# shared with 0001: create_all only creates missing tables).
import app.modules.ai.settings_store  # noqa: F401
from app.core.database import Base


def upgrade() -> None:
    Base.metadata.create_all(bind=op.get_bind())


def downgrade() -> None:
    op.drop_table("ai_provider_settings")
