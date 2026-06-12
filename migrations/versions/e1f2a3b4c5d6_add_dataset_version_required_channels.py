"""add dataset_version required_channels

Revision ID: e1f2a3b4c5d6
Revises: c8a1f2e3d945
Create Date: 2026-06-09

"""
from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects.postgresql import JSONB

revision: str = "e1f2a3b4c5d6"
down_revision: str | None = "c8a1f2e3d945"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column(
        "dataset_versions",
        sa.Column(
            "required_channels",
            JSONB,
            nullable=False,
            server_default=sa.text("'[]'::jsonb"),
        ),
    )


def downgrade() -> None:
    op.drop_column("dataset_versions", "required_channels")
