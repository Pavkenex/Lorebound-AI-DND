"""Initial schema: create every table registered on Base.metadata.

Revision ID: 0001_initial
Revises: None
"""
from __future__ import annotations

from alembic import op

revision = "0001_initial"
down_revision = None
branch_labels = None
depends_on = None

# Import side effects register all models on Base.metadata.
import app.modules.auth.models
import app.modules.campaign.models
import app.modules.campaign.npc
import app.modules.campaign.story
import app.modules.campaign.world
import app.modules.character.models
import app.modules.inventory.models  # noqa: F401
from app.core.database import Base


def upgrade() -> None:
    Base.metadata.create_all(bind=op.get_bind())


def downgrade() -> None:
    Base.metadata.drop_all(bind=op.get_bind())
