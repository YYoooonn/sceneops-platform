"""drop dataset_run_records table (DATASET_VALIDATION/DATASET_PROFILE unused)

Revision ID: f4b8e1c9a3d7
Revises: e7c1a9f4d2b6
Create Date: 2026-09-16

SceneOps V2 Request 09 audit: dataset_run_records (RunType.DATASET_VALIDATION /
DATASET_PROFILE, DatasetValidationRunRecord / DatasetProfileRunRecord) never had
a real producer — validate_scene.py / profile_scene.py write to
scene_run_records (SceneValidationRunRecord / SceneProfileRunRecord) and cache
the latest run pointer onto DatasetVersionRecord.scene via
update_scene_summary(); no code path has ever constructed a
DatasetValidationRunRecord / DatasetProfileRunRecord. DatasetRunRepositoryDep
was never injected into any API router/service either. The table had zero
rows of any type across every e2e regression run in this entire cleanup
effort (Requests 06-09), even after DATASET_EXPORT/DATASET_DISTRIBUTION were
already removed from it in prior requests. Dropped entirely — the real
dataset-version quality path is the Scene-level one
(SceneValidationRunRecord/SceneProfileRunRecord + SceneVersionSummary +
Dataset Quality API), which this migration does not touch.
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects.postgresql import JSONB

revision: str = "f4b8e1c9a3d7"
down_revision: str | None = "e7c1a9f4d2b6"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.drop_table("dataset_run_records")


def downgrade() -> None:
    op.create_table(
        "dataset_run_records",
        sa.Column("run_id", sa.String(128), primary_key=True),
        sa.Column("type", sa.String(64), nullable=False),
        sa.Column("status", sa.String(32), nullable=False),
        sa.Column("dataset_id", sa.String(128), nullable=False),
        sa.Column("dataset_version", sa.String(128), nullable=False),
        sa.Column("pipeline_run_id", sa.String(128), nullable=True),
        sa.Column("pipeline_task_run_id", sa.String(128), nullable=True),
        sa.Column("job_id", sa.String(128), nullable=True),
        sa.Column(
            "params", JSONB, nullable=False, server_default=sa.text("'{}'::jsonb")
        ),
        sa.Column("result", JSONB, nullable=True),
        sa.Column("error", JSONB, nullable=True),
        sa.Column("artifact_root_uri", sa.Text(), nullable=True),
        sa.Column("manifest_uri", sa.Text(), nullable=True),
        sa.Column("dataset_manifest_uri", sa.Text(), nullable=True),
        sa.Column("report_uri", sa.Text(), nullable=True),
        sa.Column("scope", sa.String(64), nullable=True),
        sa.Column(
            "summary", JSONB, nullable=False, server_default=sa.text("'{}'::jsonb")
        ),
        sa.Column(
            "metrics", JSONB, nullable=False, server_default=sa.text("'{}'::jsonb")
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
        "ix_dataset_run_records_type_status",
        "dataset_run_records",
        ["type", "status"],
    )
    op.create_index(
        "ix_dataset_run_records_dataset",
        "dataset_run_records",
        ["dataset_id", "dataset_version"],
    )
    op.create_index(
        "ix_dataset_run_records_job_id", "dataset_run_records", ["job_id"]
    )
    op.create_index(
        "ix_dataset_run_records_pipeline_run_id",
        "dataset_run_records",
        ["pipeline_run_id"],
    )
    op.create_index(
        "ix_dataset_run_records_created_at",
        "dataset_run_records",
        ["created_at"],
    )
