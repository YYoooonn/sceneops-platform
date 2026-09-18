"""add episode_run_records table

Revision ID: a4c8e2f19d3b
Revises: f4b8e1c9a3d7
Create Date: 2026-09-18

"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects.postgresql import JSONB

revision: str = "a4c8e2f19d3b"
down_revision: str | None = "f4b8e1c9a3d7"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    # ── episode_run_records ──────────────────────────────────────────────────
    # Unified run record for episode-scoped run types (episode_validation,
    # episode_profile) — same shape as scene_run_records: a handful of typed
    # columns, type-specific fields live in `summary` JSONB.
    op.create_table(
        "episode_run_records",
        sa.Column("run_id", sa.String(128), primary_key=True),
        sa.Column("type", sa.String(64), nullable=False),
        sa.Column("status", sa.String(32), nullable=False),
        sa.Column("episode_id", sa.String(128), nullable=True),
        sa.Column("episode_manifest_uri", sa.Text, nullable=True),
        sa.Column("dataset_id", sa.String(128), nullable=True),
        sa.Column("dataset_version", sa.String(128), nullable=True),
        sa.Column("pipeline_run_id", sa.String(128), nullable=True),
        sa.Column("pipeline_task_run_id", sa.String(128), nullable=True),
        sa.Column("job_id", sa.String(128), nullable=True),
        sa.Column(
            "params", JSONB, nullable=False, server_default=sa.text("'{}'::jsonb")
        ),
        sa.Column("result", JSONB, nullable=True),
        sa.Column("error", JSONB, nullable=True),
        sa.Column("artifact_root_uri", sa.Text, nullable=True),
        sa.Column("manifest_uri", sa.Text, nullable=True),
        sa.Column("report_uri", sa.Text, nullable=True),
        sa.Column(
            "summary", JSONB, nullable=False, server_default=sa.text("'{}'::jsonb")
        ),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("now()"),
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("now()"),
        ),
        sa.Column("started_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("finished_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column(
            "metadata", JSONB, nullable=False, server_default=sa.text("'{}'::jsonb")
        ),
    )
    op.create_index(
        "ix_episode_run_records_type_status",
        "episode_run_records",
        ["type", "status"],
    )
    op.create_index(
        "ix_episode_run_records_episode_id", "episode_run_records", ["episode_id"]
    )
    op.create_index(
        "ix_episode_run_records_dataset",
        "episode_run_records",
        ["dataset_id", "dataset_version"],
    )
    op.create_index(
        "ix_episode_run_records_job_id", "episode_run_records", ["job_id"]
    )
    op.create_index(
        "ix_episode_run_records_pipeline_run_id",
        "episode_run_records",
        ["pipeline_run_id"],
    )


def downgrade() -> None:
    op.drop_table("episode_run_records")
