"""Recording-only Scene source projection; drop DatasetVersion source fields

Revision ID: c3f1a7d5e902
Revises: d210d539ba49
Create Date: 2026-10-04

ADR-007 A4 step 7: every canonical Scene is built from a registered
RobotRun recording, so the Scene source projection is the RobotRun alone.

scenes
    drop source_kind, external_format, ck_scenes_source_projection and the
    all-or-none ck_scenes_declared_window; rename source_unit_key ->
    unit_key; robot_run_id and the window columns become NOT NULL;
    add ck_scenes_segment_window (non-empty window)
dataset_versions
    drop raw_source_root_uri, source_dataset_id, source_dataset_version
datasets
    drop type
artifacts
    the ArtifactKinds of the removed raw-log / legacy Scene producers no
    longer exist

The upgrade converts recording rows in place and invents no value. It
refuses to run while an external Scene or an artifact of a removed kind
exists: neither can be represented afterwards, and the intended path for
such development state is a reset, not a fabricated conversion. Dropping
the DatasetVersion / Dataset columns discards their values; the downgrade
restores the columns empty (``datasets.type`` as ``custom``).
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "c3f1a7d5e902"
down_revision: str | None = "d210d539ba49"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

_REMOVED_ARTIFACT_KINDS = (
    "legacy_scene_manifest",
    "raw_log_manifest",
    "raw_log_frame_index",
    "raw_sensor_frame",
    "scene_sample_manifest",
    "scene_segment_index",
)
_WINDOW_COLUMNS = (
    ("window_clock", sa.String(64)),
    ("window_start_timestamp_ns", sa.BigInteger()),
    ("window_end_timestamp_ns", sa.BigInteger()),
)


def _refuse_if(sql: str, message: str) -> None:
    count = op.get_bind().execute(sa.text(sql)).scalar_one()
    if count:
        raise RuntimeError(f"{message} ({count} row(s)); reset development state")


def upgrade() -> None:
    _refuse_if(
        "SELECT count(*) FROM scenes WHERE source_kind <> 'recording'",
        "scenes holds Scenes that are not recording-derived",
    )
    _refuse_if(
        "SELECT count(*) FROM artifacts WHERE kind IN ("
        + ", ".join(f"'{k}'" for k in _REMOVED_ARTIFACT_KINDS)
        + ")",
        "artifacts holds records of removed legacy Scene artifact kinds",
    )

    op.drop_constraint("ck_scenes_source_projection", "scenes", type_="check")
    op.drop_constraint("ck_scenes_declared_window", "scenes", type_="check")
    op.drop_column("scenes", "source_kind")
    op.drop_column("scenes", "external_format")
    op.alter_column("scenes", "source_unit_key", new_column_name="unit_key")
    op.alter_column(
        "scenes", "robot_run_id", existing_type=sa.String(128), nullable=False
    )
    for name, type_ in _WINDOW_COLUMNS:
        op.alter_column("scenes", name, existing_type=type_, nullable=False)
    op.create_check_constraint(
        "ck_scenes_segment_window",
        "scenes",
        "window_end_timestamp_ns > window_start_timestamp_ns",
    )

    op.drop_column("dataset_versions", "raw_source_root_uri")
    op.drop_column("dataset_versions", "source_dataset_id")
    op.drop_column("dataset_versions", "source_dataset_version")
    op.drop_column("datasets", "type")


def downgrade() -> None:
    op.add_column(
        "datasets",
        sa.Column(
            "type", sa.String(64), nullable=False, server_default=sa.text("'custom'")
        ),
    )
    op.alter_column("datasets", "type", server_default=None)
    op.add_column(
        "dataset_versions",
        sa.Column("source_dataset_version", sa.String(128), nullable=True),
    )
    op.add_column(
        "dataset_versions",
        sa.Column("source_dataset_id", sa.String(128), nullable=True),
    )
    op.add_column(
        "dataset_versions", sa.Column("raw_source_root_uri", sa.Text(), nullable=True)
    )

    op.drop_constraint("ck_scenes_segment_window", "scenes", type_="check")
    for name, type_ in _WINDOW_COLUMNS:
        op.alter_column("scenes", name, existing_type=type_, nullable=True)
    op.alter_column(
        "scenes", "robot_run_id", existing_type=sa.String(128), nullable=True
    )
    op.alter_column("scenes", "unit_key", new_column_name="source_unit_key")
    op.add_column("scenes", sa.Column("external_format", sa.String(64), nullable=True))
    op.add_column(
        "scenes",
        sa.Column(
            "source_kind",
            sa.String(16),
            nullable=False,
            server_default=sa.text("'recording'"),
        ),
    )
    op.alter_column("scenes", "source_kind", server_default=None)
    op.create_check_constraint(
        "ck_scenes_declared_window",
        "scenes",
        "(window_clock IS NULL AND window_start_timestamp_ns IS NULL "
        "AND window_end_timestamp_ns IS NULL) OR "
        "(window_clock IS NOT NULL AND window_start_timestamp_ns IS NOT NULL "
        "AND window_end_timestamp_ns > window_start_timestamp_ns)",
    )
    op.create_check_constraint(
        "ck_scenes_source_projection",
        "scenes",
        "(source_kind = 'external' AND external_format IS NOT NULL "
        "AND robot_run_id IS NULL) OR "
        "(source_kind = 'recording' AND robot_run_id IS NOT NULL "
        "AND external_format IS NULL)",
    )
