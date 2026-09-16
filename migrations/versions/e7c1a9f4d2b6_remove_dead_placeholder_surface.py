"""remove dead placeholder surface (labels, scene/dataset export/reconstruction)

Revision ID: e7c1a9f4d2b6
Revises: d2a94f6e8b71
Create Date: 2026-09-15

SceneOps V2 Request 08 audit: none of the following ever had a real
producer/writer anywhere in the codebase (verified via repository search and
zero persisted rows in the local dev DB across every e2e regression run):

- ``label_runs`` (RunType.SCENE_AUTO_LABEL / DATASET_AUTO_LABEL): a full
  vertical slice (DB table, repository, worker store, read-only API) was
  built out for auto-labeling, but the job handlers that would populate it
  (AUTO_LABEL_SCENE / AUTO_LABEL_DATASET, removed in Request 07) were never
  implemented. Table dropped entirely.
- ``scene_run_records.source_scene_id`` / ``target_scene_id`` (scene_comparison),
  ``raw_log_id`` / ``raw_log_manifest_uri`` / ``raw_log_frame_index_uri`` /
  ``world_state_manifest_uri`` (scene_reconstruction), ``package_uri``
  (scene_package_export): columns dedicated to three RunTypes with no
  handler and zero rows. scene_validation/scene_profile (real, have rows)
  never touch these columns. Columns dropped; table kept.
- ``dataset_run_records.export_uri`` / ``output_format`` (dataset_export):
  same pattern — dedicated to a RunType with no handler and zero rows in a
  table otherwise shared with dataset_validation/dataset_profile. Columns
  dropped; table kept.
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects.postgresql import JSONB

revision: str = "e7c1a9f4d2b6"
down_revision: str | None = "d2a94f6e8b71"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    # ── label_runs: drop entirely ───────────────────────────────────────────
    op.drop_table("label_runs")

    # ── scene_run_records: drop columns dedicated to dead run types ────────
    op.drop_index("ix_scene_run_records_source_scene_id", table_name="scene_run_records")
    op.drop_index("ix_scene_run_records_target_scene_id", table_name="scene_run_records")
    op.drop_index("ix_scene_run_records_raw_log_id", table_name="scene_run_records")
    op.drop_column("scene_run_records", "source_scene_id")
    op.drop_column("scene_run_records", "target_scene_id")
    op.drop_column("scene_run_records", "raw_log_id")
    op.drop_column("scene_run_records", "raw_log_manifest_uri")
    op.drop_column("scene_run_records", "raw_log_frame_index_uri")
    op.drop_column("scene_run_records", "world_state_manifest_uri")
    op.drop_column("scene_run_records", "package_uri")

    # ── dataset_run_records: drop columns dedicated to dataset_export ──────
    op.drop_column("dataset_run_records", "export_uri")
    op.drop_column("dataset_run_records", "output_format")


def downgrade() -> None:
    # ── dataset_run_records ─────────────────────────────────────────────────
    op.add_column(
        "dataset_run_records", sa.Column("output_format", sa.String(64), nullable=True)
    )
    op.add_column(
        "dataset_run_records", sa.Column("export_uri", sa.Text(), nullable=True)
    )

    # ── scene_run_records ────────────────────────────────────────────────────
    op.add_column(
        "scene_run_records", sa.Column("package_uri", sa.Text(), nullable=True)
    )
    op.add_column(
        "scene_run_records",
        sa.Column("world_state_manifest_uri", sa.Text(), nullable=True),
    )
    op.add_column(
        "scene_run_records",
        sa.Column("raw_log_frame_index_uri", sa.Text(), nullable=True),
    )
    op.add_column(
        "scene_run_records", sa.Column("raw_log_manifest_uri", sa.Text(), nullable=True)
    )
    op.add_column(
        "scene_run_records", sa.Column("raw_log_id", sa.String(128), nullable=True)
    )
    op.add_column(
        "scene_run_records", sa.Column("target_scene_id", sa.String(128), nullable=True)
    )
    op.add_column(
        "scene_run_records", sa.Column("source_scene_id", sa.String(128), nullable=True)
    )
    op.create_index(
        "ix_scene_run_records_raw_log_id", "scene_run_records", ["raw_log_id"]
    )
    op.create_index(
        "ix_scene_run_records_target_scene_id",
        "scene_run_records",
        ["target_scene_id"],
    )
    op.create_index(
        "ix_scene_run_records_source_scene_id",
        "scene_run_records",
        ["source_scene_id"],
    )

    # ── label_runs ───────────────────────────────────────────────────────────
    op.create_table(
        "label_runs",
        sa.Column("run_id", sa.String(128), primary_key=True),
        sa.Column("type", sa.String(64), nullable=False),
        sa.Column("status", sa.String(32), nullable=False),
        sa.Column("scene_id", sa.String(128), nullable=True),
        sa.Column("scene_manifest_uri", sa.Text(), nullable=True),
        sa.Column("output_scene_manifest_uri", sa.Text(), nullable=True),
        sa.Column("output_label_uri", sa.Text(), nullable=True),
        sa.Column("dataset_id", sa.String(128), nullable=True),
        sa.Column("dataset_version", sa.String(128), nullable=True),
        sa.Column("dataset_manifest_uri", sa.Text(), nullable=True),
        sa.Column("output_dataset_id", sa.String(128), nullable=True),
        sa.Column("output_dataset_version", sa.String(128), nullable=True),
        sa.Column("output_dataset_manifest_uri", sa.Text(), nullable=True),
        sa.Column("labeler_id", sa.String(128), nullable=True),
        sa.Column(
            "labeler_backend", sa.String(64), nullable=False, server_default="vlm"
        ),
        sa.Column("sample_count", sa.Integer(), nullable=True),
        sa.Column("labeled_sample_count", sa.Integer(), nullable=True),
        sa.Column(
            "labeled_scene_count",
            sa.Integer(),
            nullable=False,
            server_default=sa.text("0"),
        ),
        sa.Column(
            "annotation_count", sa.Integer(), nullable=False, server_default=sa.text("0")
        ),
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
        sa.Column(
            "metrics", JSONB, nullable=False, server_default=sa.text("'{}'::jsonb")
        ),
        sa.Column(
            "class_metrics", JSONB, nullable=False, server_default=sa.text("'{}'::jsonb")
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
    op.create_index("ix_label_runs_type_status", "label_runs", ["type", "status"])
    op.create_index("ix_label_runs_scene_id", "label_runs", ["scene_id"])
    op.create_index(
        "ix_label_runs_dataset", "label_runs", ["dataset_id", "dataset_version"]
    )
    op.create_index("ix_label_runs_job_id", "label_runs", ["job_id"])
    op.create_index(
        "ix_label_runs_pipeline_run_id", "label_runs", ["pipeline_run_id"]
    )
    op.create_index("ix_label_runs_created_at", "label_runs", ["created_at"])
