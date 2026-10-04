"""Recording-derived Episode projection; revision-pinned Episode run records

Revision ID: b8e4d2a6c917
Revises: c3f1a7d5e902
Create Date: 2026-10-04

ADR-007 step 8 (amendment A6): every canonical Episode is one window of a
registered RobotRun recording, projected from exactly one EpisodeManifest
revision, like a Scene.

episodes
    replaced by the recording projection: robot_run_id (FK robot_runs),
    unit_key, producer_fingerprint, manifest_artifact_id (FK artifacts),
    manifest_checksum, window_clock / window_start_timestamp_ns /
    window_end_timestamp_ns, per-role observed topics and counts;
    (dataset_id, dataset_version) FK dataset_versions. Gone: raw_log_id,
    robot_id, mission_id, status, task, outcome, episode_manifest_uri,
    observation_channels, action_channels, control_frequency_hz,
    frame_count, started_at, ended_at, metadata.
episode_run_records
    episode_manifest_uri -> manifest_artifact_id (FK artifacts) +
    manifest_checksum, with ck_episode_run_records_revision_pin.

The upgrade refuses to run while any legacy Episode or Episode run record
exists: a legacy Episode has no recording window, producer fingerprint or
manifest artifact pointer, so it cannot be represented afterwards without
inventing them. Reset development state instead. The downgrade refuses
symmetrically.
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects.postgresql import JSONB

revision: str = "b8e4d2a6c917"
down_revision: str | None = "c3f1a7d5e902"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

_EMPTY_LIST = sa.text("'[]'::jsonb")
_NOW = sa.text("now()")


def _refuse_if_rows(table: str) -> None:
    count = op.get_bind().execute(sa.text(f"SELECT count(*) FROM {table}")).scalar_one()
    if count:
        raise RuntimeError(
            f"{table} holds {count} row(s) that cannot be converted; reset "
            "development state"
        )


def upgrade() -> None:
    _refuse_if_rows("episodes")
    _refuse_if_rows("episode_run_records")

    op.drop_table("episodes")
    op.create_table(
        "episodes",
        sa.Column("episode_id", sa.String(128), primary_key=True),
        sa.Column("dataset_id", sa.String(128), nullable=False),
        sa.Column("dataset_version", sa.String(128), nullable=False),
        sa.Column(
            "robot_run_id",
            sa.String(128),
            sa.ForeignKey("robot_runs.run_id", ondelete="RESTRICT"),
            nullable=False,
        ),
        sa.Column("unit_key", sa.String(256), nullable=False),
        sa.Column("producer_fingerprint", sa.String(71), nullable=False),
        sa.Column(
            "manifest_artifact_id",
            sa.String(128),
            sa.ForeignKey("artifacts.artifact_id", ondelete="RESTRICT"),
            nullable=False,
        ),
        sa.Column("manifest_checksum", sa.String(71), nullable=False),
        sa.Column("window_clock", sa.String(64), nullable=False),
        sa.Column("window_start_timestamp_ns", sa.BigInteger(), nullable=False),
        sa.Column("window_end_timestamp_ns", sa.BigInteger(), nullable=False),
        sa.Column("observation_topics", JSONB, nullable=False, server_default=_EMPTY_LIST),
        sa.Column("state_topics", JSONB, nullable=False, server_default=_EMPTY_LIST),
        sa.Column("action_topics", JSONB, nullable=False, server_default=_EMPTY_LIST),
        sa.Column("event_topics", JSONB, nullable=False, server_default=_EMPTY_LIST),
        sa.Column("observation_count", sa.Integer(), nullable=False),
        sa.Column("state_count", sa.Integer(), nullable=False),
        sa.Column("action_count", sa.Integer(), nullable=False),
        sa.Column("event_count", sa.Integer(), nullable=False),
        sa.Column(
            "registered_at", sa.DateTime(timezone=True), nullable=False, server_default=_NOW
        ),
        sa.Column(
            "updated_at", sa.DateTime(timezone=True), nullable=False, server_default=_NOW
        ),
        sa.ForeignKeyConstraint(
            ["dataset_id", "dataset_version"],
            ["dataset_versions.dataset_id", "dataset_versions.version"],
            ondelete="RESTRICT",
            name="fk_episodes_dataset_version",
        ),
        sa.CheckConstraint(
            "window_end_timestamp_ns > window_start_timestamp_ns",
            name="ck_episodes_segment_window",
        ),
    )
    op.create_index("ix_episodes_dataset", "episodes", ["dataset_id", "dataset_version"])
    op.create_index(
        "ix_episodes_recording_scope",
        "episodes",
        ["dataset_id", "dataset_version", "robot_run_id"],
    )
    op.create_index(
        "ix_episodes_manifest_artifact_id", "episodes", ["manifest_artifact_id"]
    )

    op.drop_column("episode_run_records", "episode_manifest_uri")
    op.add_column(
        "episode_run_records",
        sa.Column(
            "manifest_artifact_id",
            sa.String(128),
            sa.ForeignKey(
                "artifacts.artifact_id",
                ondelete="RESTRICT",
                name="fk_episode_run_records_manifest_artifact_id",
            ),
            nullable=True,
        ),
    )
    op.add_column(
        "episode_run_records",
        sa.Column("manifest_checksum", sa.String(71), nullable=True),
    )
    op.create_check_constraint(
        "ck_episode_run_records_revision_pin",
        "episode_run_records",
        "(episode_id IS NULL AND manifest_artifact_id IS NULL "
        "AND manifest_checksum IS NULL) OR "
        "(episode_id IS NOT NULL AND manifest_artifact_id IS NOT NULL "
        "AND manifest_checksum IS NOT NULL)",
    )
    op.create_index(
        "ix_episode_run_records_revision",
        "episode_run_records",
        ["episode_id", "manifest_artifact_id", "created_at"],
    )


def downgrade() -> None:
    _refuse_if_rows("episodes")
    _refuse_if_rows("episode_run_records")

    op.drop_index("ix_episode_run_records_revision", table_name="episode_run_records")
    op.drop_constraint(
        "ck_episode_run_records_revision_pin", "episode_run_records", type_="check"
    )
    op.drop_constraint(
        "fk_episode_run_records_manifest_artifact_id",
        "episode_run_records",
        type_="foreignkey",
    )
    op.drop_column("episode_run_records", "manifest_checksum")
    op.drop_column("episode_run_records", "manifest_artifact_id")
    op.add_column(
        "episode_run_records",
        sa.Column("episode_manifest_uri", sa.Text(), nullable=True),
    )

    op.drop_table("episodes")
    op.create_table(
        "episodes",
        sa.Column("episode_id", sa.String(128), primary_key=True),
        sa.Column("dataset_id", sa.String(128), nullable=True),
        sa.Column("dataset_version", sa.String(128), nullable=True),
        sa.Column("raw_log_id", sa.String(128), nullable=True),
        sa.Column("robot_id", sa.String(128), nullable=True),
        sa.Column("robot_run_id", sa.String(128), nullable=True),
        sa.Column("mission_id", sa.String(128), nullable=True),
        sa.Column("status", sa.String(32), nullable=False),
        sa.Column("task", sa.String(255), nullable=True),
        sa.Column(
            "outcome", sa.String(32), nullable=False, server_default=sa.text("'unknown'")
        ),
        sa.Column("episode_manifest_uri", sa.Text, nullable=True),
        sa.Column("observation_channels", JSONB, nullable=False, server_default=_EMPTY_LIST),
        sa.Column("action_channels", JSONB, nullable=False, server_default=_EMPTY_LIST),
        sa.Column("control_frequency_hz", sa.Float, nullable=True),
        sa.Column("frame_count", sa.Integer, nullable=False, server_default=sa.text("0")),
        sa.Column("started_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("ended_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), nullable=False, server_default=_NOW
        ),
        sa.Column(
            "updated_at", sa.DateTime(timezone=True), nullable=False, server_default=_NOW
        ),
        sa.Column("metadata", JSONB, nullable=False, server_default=sa.text("'{}'::jsonb")),
    )
    op.create_index("ix_episodes_dataset", "episodes", ["dataset_id", "dataset_version"])
    op.create_index("ix_episodes_raw_log_id", "episodes", ["raw_log_id"])
    op.create_index("ix_episodes_robot_id", "episodes", ["robot_id"])
    op.create_index("ix_episodes_robot_run_id", "episodes", ["robot_run_id"])
    op.create_index("ix_episodes_mission_id", "episodes", ["mission_id"])
    op.create_index("ix_episodes_status", "episodes", ["status"])
