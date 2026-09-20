"""add execution_key to jobs and pipeline_runs

Revision ID: f3a9c7d2b118
Revises: a1b2c3d4e5f6
Create Date: 2026-08-21

"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "f3a9c7d2b118"
down_revision: str | None = "a1b2c3d4e5f6"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column(
        "jobs",
        sa.Column("execution_key", sa.String(128), nullable=True),
    )
    op.create_index("ix_jobs_execution_key", "jobs", ["execution_key"])

    op.add_column(
        "pipeline_runs",
        sa.Column("execution_key", sa.String(128), nullable=True),
    )
    op.create_index(
        "ix_pipeline_runs_execution_key", "pipeline_runs", ["execution_key"]
    )


def downgrade() -> None:
    op.drop_index("ix_pipeline_runs_execution_key", table_name="pipeline_runs")
    op.drop_column("pipeline_runs", "execution_key")

    op.drop_index("ix_jobs_execution_key", table_name="jobs")
    op.drop_column("jobs", "execution_key")
