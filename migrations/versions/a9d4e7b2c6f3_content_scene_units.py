"""Content-sourced Scene units

Revision ID: a9d4e7b2c6f3
Revises: f2c6a9e4b8d1
Create Date: 2026-10-26

ADR-015 §3, §6.4: a Scene unit is sourced from a registered RobotRun
recording or from a registered SourceContent.

scenes
    + content_id (FK source_contents, RESTRICT); robot_run_id becomes
    nullable; exactly one of them is set, matching source_kind
    ('recording' -> robot_run_id, 'content' -> content_id); one row per
    source unit for either kind: unique (robot_run_id, unit_key) and
    unique (content_id, unit_key). A per-kind column keeps each source's own
    foreign key and uniqueness, which one nullable source column cannot.

Existing rows stay valid: every one is a recording unit. The column is added
nullable, which rewrites no row through UPDATE, so the immutability trigger is
not involved. The downgrade refuses while a content-sourced Scene exists.
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "a9d4e7b2c6f3"
down_revision: str | None = "f2c6a9e4b8d1"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

_RECORDING_ONLY = "source_kind = 'recording'"
_EITHER_SOURCE = (
    "(source_kind = 'recording' AND robot_run_id IS NOT NULL "
    "AND content_id IS NULL) OR "
    "(source_kind = 'content' AND content_id IS NOT NULL "
    "AND robot_run_id IS NULL)"
)


def upgrade() -> None:
    op.add_column(
        "scenes",
        sa.Column(
            "content_id",
            sa.String(64),
            sa.ForeignKey(
                "source_contents.content_id",
                name="fk_scenes_content_id",
                ondelete="RESTRICT",
            ),
            nullable=True,
        ),
    )
    op.alter_column("scenes", "robot_run_id", nullable=True)
    op.drop_constraint("ck_scenes_source_kind", "scenes", type_="check")
    op.create_check_constraint("ck_scenes_source_kind", "scenes", _EITHER_SOURCE)
    op.create_unique_constraint(
        "uq_scenes_content_source_unit", "scenes", ["content_id", "unit_key"]
    )


def downgrade() -> None:
    count = (
        op.get_bind()
        .execute(sa.text("SELECT count(*) FROM scenes WHERE content_id IS NOT NULL"))
        .scalar_one()
    )
    if count:
        raise RuntimeError(
            f"scenes holds {count} content-sourced unit(s), which the previous "
            "schema cannot represent; reset development state"
        )
    op.drop_constraint("uq_scenes_content_source_unit", "scenes", type_="unique")
    op.drop_constraint("ck_scenes_source_kind", "scenes", type_="check")
    op.create_check_constraint("ck_scenes_source_kind", "scenes", _RECORDING_ONLY)
    op.alter_column("scenes", "robot_run_id", nullable=False)
    op.drop_column("scenes", "content_id")
