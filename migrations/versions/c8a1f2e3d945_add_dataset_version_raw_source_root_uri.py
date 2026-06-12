"""add dataset_version raw_source_root_uri

Revision ID: c8a1f2e3d945
Revises: fa61176f354f
Create Date: 2026-06-09

"""
from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "c8a1f2e3d945"
down_revision: str | None = "fa61176f354f"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column(
        "dataset_versions",
        sa.Column("raw_source_root_uri", sa.Text(), nullable=True),
    )


def downgrade() -> None:
    op.drop_column("dataset_versions", "raw_source_root_uri")
