"""Content-sourced Episode units and transition projections

Revision ID: f2c6a9e4b8d1
Revises: b6e2d8f4a1c7
Create Date: 2026-10-26

ADR-015 §3, §6.4, §6.6: an Episode unit is sourced from a registered RobotRun
recording or from a registered SourceContent.

episodes
    + content_id (FK source_contents, RESTRICT); robot_run_id becomes
    nullable; exactly one of them is set, matching source_kind
    ('recording' -> robot_run_id, 'content' -> content_id); one row per
    source unit for either kind: unique (robot_run_id, unit_key) and
    unique (content_id, unit_key). A per-kind column keeps each source's own
    foreign key and uniqueness, which one nullable source column cannot.
episode_revisions
    + transition_topics (JSONB, default '[]') and transition_count (the
    projection of EpisodeManifest v3 ``transitions``).

Existing rows stay valid: every one is a recording unit, and an existing
revision has no transition. Columns are added with defaults, which rewrites
no row through UPDATE, so the immutability triggers are not involved. The
downgrade refuses while a content-sourced Episode exists.
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects.postgresql import JSONB

revision: str = "f2c6a9e4b8d1"
down_revision: str | None = "b6e2d8f4a1c7"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

_RECORDING_ONLY = "source_kind = 'recording'"
_EITHER_SOURCE = (
    "(source_kind = 'recording' AND robot_run_id IS NOT NULL "
    "AND content_id IS NULL) OR "
    "(source_kind = 'content' AND content_id IS NOT NULL "
    "AND robot_run_id IS NULL)"
)
_COUNTS = (
    "observation_count >= 0 AND state_count >= 0 AND action_count >= 0 "
    "AND event_count >= 0"
)


def upgrade() -> None:
    op.add_column(
        "episodes",
        sa.Column(
            "content_id",
            sa.String(64),
            sa.ForeignKey(
                "source_contents.content_id",
                name="fk_episodes_content_id",
                ondelete="RESTRICT",
            ),
            nullable=True,
        ),
    )
    op.alter_column("episodes", "robot_run_id", nullable=True)
    op.drop_constraint("ck_episodes_source_kind", "episodes", type_="check")
    op.create_check_constraint("ck_episodes_source_kind", "episodes", _EITHER_SOURCE)
    op.create_unique_constraint(
        "uq_episodes_content_source_unit", "episodes", ["content_id", "unit_key"]
    )

    op.add_column(
        "episode_revisions",
        sa.Column(
            "transition_topics",
            JSONB,
            nullable=False,
            server_default=sa.text("'[]'::jsonb"),
        ),
    )
    op.add_column(
        "episode_revisions",
        sa.Column("transition_count", sa.Integer(), nullable=False, server_default="0"),
    )
    # The registrar always writes the count; the default only fills the
    # existing rows.
    op.alter_column("episode_revisions", "transition_count", server_default=None)
    op.drop_constraint(
        "ck_episode_revisions_counts", "episode_revisions", type_="check"
    )
    op.create_check_constraint(
        "ck_episode_revisions_counts",
        "episode_revisions",
        f"{_COUNTS} AND transition_count >= 0",
    )


def downgrade() -> None:
    count = (
        op.get_bind()
        .execute(sa.text("SELECT count(*) FROM episodes WHERE content_id IS NOT NULL"))
        .scalar_one()
    )
    if count:
        raise RuntimeError(
            f"episodes holds {count} content-sourced unit(s), which the previous "
            "schema cannot represent; reset development state"
        )
    op.drop_constraint(
        "ck_episode_revisions_counts", "episode_revisions", type_="check"
    )
    op.create_check_constraint(
        "ck_episode_revisions_counts", "episode_revisions", _COUNTS
    )
    op.drop_column("episode_revisions", "transition_count")
    op.drop_column("episode_revisions", "transition_topics")

    op.drop_constraint("uq_episodes_content_source_unit", "episodes", type_="unique")
    op.drop_constraint("ck_episodes_source_kind", "episodes", type_="check")
    op.create_check_constraint("ck_episodes_source_kind", "episodes", _RECORDING_ONLY)
    op.alter_column("episodes", "robot_run_id", nullable=False)
    op.drop_column("episodes", "content_id")
