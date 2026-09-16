"""drop dead dataset_versions distribution fields

Revision ID: b5f4b88342e2
Revises: fc7491fe60a0
Create Date: 2026-09-13

latest_distribution_run_id / distribution_report_uri never had a writer or
reader anywhere in the codebase (JobType.CHECK_DISTRIBUTION exists but never
wired up to set them — see SceneOps V2 Request 01/04 audits). Dropped rather
than kept as dead schema state.
"""

from __future__ import annotations

from collections.abc import Sequence

from alembic import op

revision: str = "b5f4b88342e2"
down_revision: str | None = "fc7491fe60a0"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.drop_index(
        "ix_dataset_versions_latest_distribution_run_id",
        table_name="dataset_versions",
    )
    op.drop_column("dataset_versions", "latest_distribution_run_id")
    op.drop_column("dataset_versions", "distribution_report_uri")


def downgrade() -> None:
    import sqlalchemy as sa

    op.add_column(
        "dataset_versions",
        sa.Column("distribution_report_uri", sa.Text(), nullable=True),
    )
    op.add_column(
        "dataset_versions",
        sa.Column("latest_distribution_run_id", sa.String(128), nullable=True),
    )
    op.create_index(
        "ix_dataset_versions_latest_distribution_run_id",
        "dataset_versions",
        ["latest_distribution_run_id"],
    )
