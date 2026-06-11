"""add scene GT fields: annotation_count, has_ground_truth, ground_truth_source

Revision ID: a1b2c3d4e5f6
Revises: e1f2a3b4c5d6
Create Date: 2026-06-11

"""
from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "a1b2c3d4e5f6"
down_revision: str | None = "e1f2a3b4c5d6"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column(
        "scenes",
        sa.Column(
            "annotation_count",
            sa.Integer(),
            nullable=False,
            server_default=sa.text("0"),
        ),
    )
    op.add_column(
        "scenes",
        sa.Column(
            "has_ground_truth",
            sa.Boolean(),
            nullable=False,
            server_default=sa.text("false"),
        ),
    )
    op.add_column(
        "scenes",
        sa.Column(
            "ground_truth_source",
            sa.String(128),
            nullable=True,
        ),
    )


def downgrade() -> None:
    op.drop_column("scenes", "ground_truth_source")
    op.drop_column("scenes", "has_ground_truth")
    op.drop_column("scenes", "annotation_count")
