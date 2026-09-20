"""add episodes table, dataset_versions.episode_count

Revision ID: fc7491fe60a0
Revises: d4b6f0a1c923
Create Date: 2026-09-13

"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects.postgresql import JSONB

revision: str = "fc7491fe60a0"
down_revision: str | None = "d4b6f0a1c923"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    # ── episodes ──────────────────────────────────────────────────────────────
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
            "outcome",
            sa.String(32),
            nullable=False,
            server_default=sa.text("'unknown'"),
        ),
        sa.Column("episode_manifest_uri", sa.Text, nullable=True),
        sa.Column(
            "observation_channels",
            JSONB,
            nullable=False,
            server_default=sa.text("'[]'::jsonb"),
        ),
        sa.Column(
            "action_channels",
            JSONB,
            nullable=False,
            server_default=sa.text("'[]'::jsonb"),
        ),
        sa.Column("control_frequency_hz", sa.Float, nullable=True),
        sa.Column(
            "frame_count", sa.Integer, nullable=False, server_default=sa.text("0")
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
    op.create_index(
        "ix_episodes_dataset", "episodes", ["dataset_id", "dataset_version"]
    )
    op.create_index("ix_episodes_raw_log_id", "episodes", ["raw_log_id"])
    op.create_index("ix_episodes_robot_id", "episodes", ["robot_id"])
    op.create_index("ix_episodes_robot_run_id", "episodes", ["robot_run_id"])
    op.create_index("ix_episodes_mission_id", "episodes", ["mission_id"])
    op.create_index("ix_episodes_status", "episodes", ["status"])

    # ── dataset_versions.episode_count ───────────────────────────────────────
    op.add_column(
        "dataset_versions",
        sa.Column(
            "episode_count", sa.Integer(), nullable=False, server_default=sa.text("0")
        ),
    )


def downgrade() -> None:
    op.drop_column("dataset_versions", "episode_count")
    op.drop_table("episodes")
