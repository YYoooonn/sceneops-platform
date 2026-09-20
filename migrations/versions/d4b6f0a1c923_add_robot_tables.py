"""add robot/robot_run/mission/robot_state tables

Revision ID: d4b6f0a1c923
Revises: f3a9c7d2b118
Create Date: 2026-08-28

"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects.postgresql import JSONB

revision: str = "d4b6f0a1c923"
down_revision: str | None = "f3a9c7d2b118"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    # ── robots ────────────────────────────────────────────────────────────────
    op.create_table(
        "robots",
        sa.Column("robot_id", sa.String(128), primary_key=True),
        sa.Column("name", sa.String(255), nullable=True),
        sa.Column("platform", sa.String(128), nullable=True),
        sa.Column(
            "status",
            sa.String(32),
            nullable=False,
            server_default=sa.text("'registered'"),
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
        sa.Column(
            "metadata", JSONB, nullable=False, server_default=sa.text("'{}'::jsonb")
        ),
    )
    op.create_index("ix_robots_status", "robots", ["status"])

    # ── robot_runs ────────────────────────────────────────────────────────────
    op.create_table(
        "robot_runs",
        sa.Column("run_id", sa.String(128), primary_key=True),
        sa.Column(
            "robot_id",
            sa.String(128),
            sa.ForeignKey("robots.robot_id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column(
            "status",
            sa.String(32),
            nullable=False,
            server_default=sa.text("'recording'"),
        ),
        sa.Column("dataset_id", sa.String(128), nullable=True),
        sa.Column("dataset_version", sa.String(128), nullable=True),
        sa.Column("raw_log_id", sa.String(128), nullable=True),
        sa.Column("rosbag_uri", sa.Text, nullable=True),
        sa.Column("mcap_uri", sa.Text, nullable=True),
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
            "metadata", JSONB, nullable=False, server_default=sa.text("'{}'::jsonb")
        ),
    )
    op.create_index("ix_robot_runs_robot_id", "robot_runs", ["robot_id"])
    op.create_index("ix_robot_runs_status", "robot_runs", ["status"])
    op.create_index("ix_robot_runs_dataset_id", "robot_runs", ["dataset_id"])
    op.create_index("ix_robot_runs_raw_log_id", "robot_runs", ["raw_log_id"])

    # ── missions ──────────────────────────────────────────────────────────────
    op.create_table(
        "missions",
        sa.Column("mission_id", sa.String(128), primary_key=True),
        sa.Column(
            "robot_id",
            sa.String(128),
            sa.ForeignKey("robots.robot_id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column(
            "robot_run_id",
            sa.String(128),
            sa.ForeignKey("robot_runs.run_id", ondelete="SET NULL"),
            nullable=True,
        ),
        sa.Column(
            "status",
            sa.String(32),
            nullable=False,
            server_default=sa.text("'pending'"),
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
            "metadata", JSONB, nullable=False, server_default=sa.text("'{}'::jsonb")
        ),
    )
    op.create_index("ix_missions_robot_id", "missions", ["robot_id"])
    op.create_index("ix_missions_robot_run_id", "missions", ["robot_run_id"])
    op.create_index("ix_missions_status", "missions", ["status"])

    # ── robot_states ──────────────────────────────────────────────────────────
    op.create_table(
        "robot_states",
        sa.Column("state_id", sa.String(128), primary_key=True),
        sa.Column(
            "robot_id",
            sa.String(128),
            sa.ForeignKey("robots.robot_id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column(
            "robot_run_id",
            sa.String(128),
            sa.ForeignKey("robot_runs.run_id", ondelete="CASCADE"),
            nullable=True,
        ),
        sa.Column(
            "mission_id",
            sa.String(128),
            sa.ForeignKey("missions.mission_id", ondelete="SET NULL"),
            nullable=True,
        ),
        sa.Column("scene_id", sa.String(128), nullable=True),
        sa.Column("timestamp_us", sa.BigInteger, nullable=False),
        sa.Column("position", JSONB, nullable=True),
        sa.Column("orientation", JSONB, nullable=True),
        sa.Column(
            "rotation_format",
            sa.String(32),
            nullable=False,
            server_default=sa.text("'quaternion_wxyz'"),
        ),
        sa.Column("velocity", JSONB, nullable=True),
        sa.Column("acceleration", JSONB, nullable=True),
        sa.Column("steering", sa.Float, nullable=True),
        sa.Column("throttle", sa.Float, nullable=True),
        sa.Column("brake", sa.Float, nullable=True),
        sa.Column("battery", sa.Float, nullable=True),
        sa.Column("operation_state", sa.String(32), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("now()"),
        ),
        sa.Column(
            "metadata", JSONB, nullable=False, server_default=sa.text("'{}'::jsonb")
        ),
    )
    op.create_index("ix_robot_states_scene_id", "robot_states", ["scene_id"])
    op.create_index("ix_robot_states_mission_id", "robot_states", ["mission_id"])
    op.create_index(
        "ix_robot_states_robot_id_timestamp_us",
        "robot_states",
        ["robot_id", "timestamp_us"],
    )
    op.create_index(
        "ix_robot_states_robot_run_id_timestamp_us",
        "robot_states",
        ["robot_run_id", "timestamp_us"],
    )


def downgrade() -> None:
    op.drop_table("robot_states")
    op.drop_table("missions")
    op.drop_table("robot_runs")
    op.drop_table("robots")
