"""robot_runs as immutable finalized-recording provenance (ADR-007 §10)

Revision ID: d820064cc715
Revises: a4c8e2f19d3b
Create Date: 2026-10-02

A RobotRunRecord now exists only for a recording that was published with a
canonical RobotRunManifest and verified by REGISTER_ROBOT_RUN. It drops
lifecycle status, dataset membership, raw-log linkage, recording URIs,
free-form metadata and updated_at, and references the recording and
manifest ArtifactRecords instead.

Existing robot_runs rows cannot satisfy the new NOT NULL provenance columns
because no RobotRunManifest exists for them. This migration never deletes
them: it refuses to run while the table is non-empty. Resetting that dev
state (ADR-007 §23) is an explicit operator decision made before upgrading.
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects.postgresql import JSONB

revision: str = "d820064cc715"
down_revision: str | None = "a4c8e2f19d3b"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    existing = (
        op.get_bind().execute(sa.text("SELECT count(*) FROM robot_runs")).scalar()
    )
    if existing:
        raise RuntimeError(
            f"robot_runs contains {existing} row(s) created before RobotRunManifest "
            "registration existed; they cannot be migrated to the ADR-007 §10 "
            "schema. Reset that dev state explicitly (ADR-007 §23), then re-run "
            "the upgrade and re-register recordings via the Recording Publisher "
            "+ REGISTER_ROBOT_RUN."
        )

    op.drop_index("ix_robot_runs_status", table_name="robot_runs")
    op.drop_index("ix_robot_runs_dataset_id", table_name="robot_runs")
    op.drop_index("ix_robot_runs_raw_log_id", table_name="robot_runs")
    for column in (
        "status",
        "dataset_id",
        "dataset_version",
        "raw_log_id",
        "rosbag_uri",
        "mcap_uri",
        "metadata",
        "updated_at",
    ):
        op.drop_column("robot_runs", column)

    op.alter_column("robot_runs", "created_at", new_column_name="registered_at")
    op.alter_column("robot_runs", "started_at", nullable=False)
    op.alter_column("robot_runs", "ended_at", nullable=False)

    op.add_column(
        "robot_runs", sa.Column("recording_format", sa.String(32), nullable=False)
    )
    op.add_column(
        "robot_runs", sa.Column("source_clock", sa.String(64), nullable=False)
    )
    op.add_column(
        "robot_runs",
        sa.Column(
            "recording_artifact_id",
            sa.String(128),
            sa.ForeignKey("artifacts.artifact_id", ondelete="RESTRICT"),
            nullable=False,
            unique=True,
        ),
    )
    op.add_column(
        "robot_runs",
        sa.Column(
            "manifest_artifact_id",
            sa.String(128),
            sa.ForeignKey("artifacts.artifact_id", ondelete="RESTRICT"),
            nullable=False,
            unique=True,
        ),
    )
    op.add_column(
        "robot_runs", sa.Column("manifest_checksum", sa.String(255), nullable=False)
    )


def downgrade() -> None:
    for column in (
        "manifest_checksum",
        "manifest_artifact_id",
        "recording_artifact_id",
        "source_clock",
        "recording_format",
    ):
        op.drop_column("robot_runs", column)

    op.alter_column("robot_runs", "ended_at", nullable=True)
    op.alter_column("robot_runs", "started_at", nullable=True)
    op.alter_column("robot_runs", "registered_at", new_column_name="created_at")

    op.add_column(
        "robot_runs",
        sa.Column(
            "status",
            sa.String(32),
            nullable=False,
            server_default=sa.text("'completed'"),
        ),
    )
    op.add_column("robot_runs", sa.Column("dataset_id", sa.String(128), nullable=True))
    op.add_column(
        "robot_runs", sa.Column("dataset_version", sa.String(128), nullable=True)
    )
    op.add_column("robot_runs", sa.Column("raw_log_id", sa.String(128), nullable=True))
    op.add_column("robot_runs", sa.Column("rosbag_uri", sa.Text, nullable=True))
    op.add_column("robot_runs", sa.Column("mcap_uri", sa.Text, nullable=True))
    op.add_column(
        "robot_runs",
        sa.Column(
            "metadata", JSONB, nullable=False, server_default=sa.text("'{}'::jsonb")
        ),
    )
    op.add_column(
        "robot_runs",
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("now()"),
        ),
    )
    op.create_index("ix_robot_runs_status", "robot_runs", ["status"])
    op.create_index("ix_robot_runs_dataset_id", "robot_runs", ["dataset_id"])
    op.create_index("ix_robot_runs_raw_log_id", "robot_runs", ["raw_log_id"])
