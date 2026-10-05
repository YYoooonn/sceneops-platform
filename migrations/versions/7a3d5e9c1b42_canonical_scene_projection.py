"""scenes as a projection of one canonical SceneManifest revision (ADR-007 §13.3)

Revision ID: 7a3d5e9c1b42
Revises: 51b9e7204713
Create Date: 2026-10-02

``scenes`` becomes canonical Scene membership that pins exactly one
registered SceneManifest revision (``manifest_artifact_id`` +
``manifest_checksum``). It drops lifecycle status, raw-log / segment
linkage, origin / generation fields, lineage and generation JSONB copies,
manifest / world-state / artifact-root URIs, free-form metadata and the
sample-centric counts, and gains source-kind / producer / source-time
projections with foreign keys to its DatasetVersion, manifest artifact and
(for recording-derived Scenes) RobotRun, plus the source's declared time
window where it has one.

``scene_run_records`` replaces ``scene_manifest_uri`` with a revision pin; a
per-scene row must pin, a job-level row must not.

``dataset_versions`` loses the Scene quality cache (readiness is derived
from run records of each Scene's current revision) and its Scene summary
columns are renamed to the observation-centric vocabulary.

Existing Scene rows and per-scene run rows reference manifests that are not
canonical SceneManifest revisions and cannot be migrated. Like the
RobotRun migration, this one never deletes them: it refuses to run while
any exist. Resetting that dev state (ADR-007 §23) is an explicit operator
decision.
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects.postgresql import JSONB

revision: str = "7a3d5e9c1b42"
down_revision: str | None = "51b9e7204713"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

_DV_QUALITY_CACHE_INDEXES = (
    "ix_dataset_versions_latest_validation_run_id",
    "ix_dataset_versions_latest_profile_run_id",
    "ix_dataset_versions_validation_status",
    "ix_dataset_versions_should_block_pipeline",
)
_DV_SUMMARY_RENAMES = (
    ("sample_count", "keyframe_count"),
    ("frame_count", "observation_count"),
    ("channels", "observed_channels"),
)


def _refuse_unmigratable_rows() -> None:
    bind = op.get_bind()
    scenes = bind.execute(sa.text("SELECT count(*) FROM scenes")).scalar()
    scene_runs = bind.execute(
        sa.text("SELECT count(*) FROM scene_run_records WHERE scene_id IS NOT NULL")
    ).scalar()
    if scenes or scene_runs:
        raise RuntimeError(
            f"scenes has {scenes} row(s) and scene_run_records has {scene_runs} "
            "per-scene row(s) from before canonical SceneManifest registration; "
            "they cannot be migrated to the ADR-007 §13.3 schema. Reset that "
            "dev state explicitly (ADR-007 §23), then re-run the upgrade."
        )


def _create_canonical_scenes_table() -> None:
    op.create_table(
        "scenes",
        sa.Column("scene_id", sa.String(128), primary_key=True),
        sa.Column("dataset_id", sa.String(128), nullable=False),
        sa.Column("dataset_version", sa.String(128), nullable=False),
        sa.Column("source_kind", sa.String(16), nullable=False),
        sa.Column("external_format", sa.String(64), nullable=True),
        sa.Column(
            "robot_run_id",
            sa.String(128),
            sa.ForeignKey("robot_runs.run_id", ondelete="RESTRICT"),
            nullable=True,
        ),
        sa.Column("source_unit_key", sa.String(256), nullable=False),
        sa.Column("producer_fingerprint", sa.String(71), nullable=False),
        sa.Column(
            "manifest_artifact_id",
            sa.String(128),
            sa.ForeignKey("artifacts.artifact_id", ondelete="RESTRICT"),
            nullable=False,
        ),
        sa.Column("manifest_checksum", sa.String(71), nullable=False),
        sa.Column("window_clock", sa.String(64), nullable=True),
        sa.Column("window_start_timestamp_ns", sa.BigInteger(), nullable=True),
        sa.Column("window_end_timestamp_ns", sa.BigInteger(), nullable=True),
        sa.Column(
            "observed_channels",
            JSONB(),
            nullable=False,
            server_default=sa.text("'[]'::jsonb"),
        ),
        sa.Column("observation_count", sa.Integer(), nullable=False),
        sa.Column("keyframe_count", sa.Integer(), nullable=False),
        sa.Column("annotation_count", sa.Integer(), nullable=False),
        sa.Column(
            "registered_at",
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
        sa.ForeignKeyConstraint(
            ["dataset_id", "dataset_version"],
            ["dataset_versions.dataset_id", "dataset_versions.version"],
            ondelete="RESTRICT",
            name="fk_scenes_dataset_version",
        ),
        sa.CheckConstraint(
            "(source_kind = 'external' AND external_format IS NOT NULL "
            "AND robot_run_id IS NULL) OR "
            "(source_kind = 'recording' AND robot_run_id IS NOT NULL "
            "AND external_format IS NULL)",
            name="ck_scenes_source_projection",
        ),
        sa.CheckConstraint(
            "(window_clock IS NULL AND window_start_timestamp_ns IS NULL "
            "AND window_end_timestamp_ns IS NULL) OR "
            "(window_clock IS NOT NULL AND window_start_timestamp_ns IS NOT NULL "
            "AND window_end_timestamp_ns > window_start_timestamp_ns)",
            name="ck_scenes_declared_window",
        ),
    )
    op.create_index("ix_scenes_dataset", "scenes", ["dataset_id", "dataset_version"])
    op.create_index(
        "ix_scenes_recording_scope",
        "scenes",
        ["dataset_id", "dataset_version", "robot_run_id"],
    )
    op.create_index(
        "ix_scenes_manifest_artifact_id", "scenes", ["manifest_artifact_id"]
    )


def _create_legacy_scenes_table() -> None:
    op.create_table(
        "scenes",
        sa.Column("scene_id", sa.String(128), primary_key=True),
        sa.Column("dataset_id", sa.String(128), nullable=True),
        sa.Column("dataset_version", sa.String(128), nullable=True),
        sa.Column("raw_log_id", sa.String(128), nullable=True),
        sa.Column("segment_id", sa.String(128), nullable=True),
        sa.Column("status", sa.String(32), nullable=False),
        sa.Column("origin_type", sa.String(64), nullable=False),
        sa.Column("generation_method", sa.String(64), nullable=False),
        sa.Column("parent_scene_id", sa.String(128), nullable=True),
        sa.Column("lineage", JSONB(), nullable=True),
        sa.Column("generation", JSONB(), nullable=True),
        sa.Column("scene_manifest_uri", sa.Text(), nullable=True),
        sa.Column("world_state_manifest_uri", sa.Text(), nullable=True),
        sa.Column("artifact_root_uri", sa.Text(), nullable=True),
        sa.Column("sample_count", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("frame_count", sa.Integer(), nullable=False, server_default="0"),
        sa.Column(
            "channels", JSONB(), nullable=False, server_default=sa.text("'[]'::jsonb")
        ),
        sa.Column("started_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("ended_at", sa.DateTime(timezone=True), nullable=True),
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
        sa.Column(
            "metadata", JSONB(), nullable=False, server_default=sa.text("'{}'::jsonb")
        ),
        sa.Column("annotation_count", sa.Integer(), nullable=False, server_default="0"),
        sa.Column(
            "has_ground_truth", sa.Boolean(), nullable=False, server_default="false"
        ),
        sa.Column("ground_truth_source", sa.String(128), nullable=True),
    )
    op.create_index("ix_scenes_dataset", "scenes", ["dataset_id", "dataset_version"])
    op.create_index("ix_scenes_generation_method", "scenes", ["generation_method"])
    op.create_index("ix_scenes_origin_type", "scenes", ["origin_type"])
    op.create_index("ix_scenes_parent_scene_id", "scenes", ["parent_scene_id"])
    op.create_index("ix_scenes_raw_log_id", "scenes", ["raw_log_id"])
    op.create_index("ix_scenes_status", "scenes", ["status"])


def upgrade() -> None:
    _refuse_unmigratable_rows()

    # scenes: the table is empty (checked above), so it is replaced whole.
    op.drop_table("scenes")
    _create_canonical_scenes_table()

    # scene_run_records: revision pin instead of a manifest URI.
    op.drop_column("scene_run_records", "scene_manifest_uri")
    op.add_column(
        "scene_run_records",
        sa.Column(
            "manifest_artifact_id",
            sa.String(128),
            sa.ForeignKey(
                "artifacts.artifact_id",
                ondelete="RESTRICT",
                name="fk_scene_run_records_manifest_artifact_id",
            ),
            nullable=True,
        ),
    )
    op.add_column(
        "scene_run_records",
        sa.Column("manifest_checksum", sa.String(71), nullable=True),
    )
    op.create_check_constraint(
        "ck_scene_run_records_revision_pin",
        "scene_run_records",
        "(scene_id IS NULL AND manifest_artifact_id IS NULL "
        "AND manifest_checksum IS NULL) OR "
        "(scene_id IS NOT NULL AND manifest_artifact_id IS NOT NULL "
        "AND manifest_checksum IS NOT NULL)",
    )
    op.create_index(
        "ix_scene_run_records_revision",
        "scene_run_records",
        ["scene_id", "manifest_artifact_id", "created_at"],
    )

    # dataset_versions: no Scene quality cache; observation-centric summary.
    for index in _DV_QUALITY_CACHE_INDEXES:
        op.drop_index(index, table_name="dataset_versions")
    for column in (
        "latest_validation_run_id",
        "validation_status",
        "should_block_pipeline",
        "validation_report_uri",
        "latest_profile_run_id",
        "profile_report_uri",
    ):
        op.drop_column("dataset_versions", column)
    for old, new in _DV_SUMMARY_RENAMES:
        op.alter_column("dataset_versions", old, new_column_name=new)


def downgrade() -> None:
    bind = op.get_bind()
    scenes = bind.execute(sa.text("SELECT count(*) FROM scenes")).scalar()
    scene_runs = bind.execute(
        sa.text("SELECT count(*) FROM scene_run_records WHERE scene_id IS NOT NULL")
    ).scalar()
    if scenes or scene_runs:
        raise RuntimeError(
            "canonical scenes / revision-pinned scene runs cannot be represented "
            "by the previous schema; reset that state before downgrading."
        )

    for old, new in _DV_SUMMARY_RENAMES:
        op.alter_column("dataset_versions", new, new_column_name=old)
    op.add_column(
        "dataset_versions", sa.Column("profile_report_uri", sa.Text(), nullable=True)
    )
    op.add_column(
        "dataset_versions",
        sa.Column("latest_profile_run_id", sa.String(128), nullable=True),
    )
    op.add_column(
        "dataset_versions", sa.Column("validation_report_uri", sa.Text(), nullable=True)
    )
    op.add_column(
        "dataset_versions",
        sa.Column("should_block_pipeline", sa.Boolean(), nullable=True),
    )
    op.add_column(
        "dataset_versions",
        sa.Column("validation_status", sa.String(32), nullable=True),
    )
    op.add_column(
        "dataset_versions",
        sa.Column("latest_validation_run_id", sa.String(128), nullable=True),
    )
    op.create_index(
        "ix_dataset_versions_latest_validation_run_id",
        "dataset_versions",
        ["latest_validation_run_id"],
    )
    op.create_index(
        "ix_dataset_versions_latest_profile_run_id",
        "dataset_versions",
        ["latest_profile_run_id"],
    )
    op.create_index(
        "ix_dataset_versions_validation_status",
        "dataset_versions",
        ["validation_status"],
    )
    op.create_index(
        "ix_dataset_versions_should_block_pipeline",
        "dataset_versions",
        ["should_block_pipeline"],
    )

    op.drop_index("ix_scene_run_records_revision", table_name="scene_run_records")
    op.drop_constraint(
        "ck_scene_run_records_revision_pin", "scene_run_records", type_="check"
    )
    op.drop_column("scene_run_records", "manifest_checksum")
    op.drop_column("scene_run_records", "manifest_artifact_id")
    op.add_column(
        "scene_run_records", sa.Column("scene_manifest_uri", sa.Text(), nullable=True)
    )

    op.drop_table("scenes")
    _create_legacy_scenes_table()
