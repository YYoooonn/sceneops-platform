"""robot_runs.robot_id FK: ON DELETE CASCADE -> RESTRICT (ADR-007 §10.4)

Revision ID: 51b9e7204713
Revises: d820064cc715
Create Date: 2026-10-02

A RobotRunRecord is immutable recording provenance. With CASCADE, deleting a
Robot silently deleted its RobotRuns while their recording/manifest
ArtifactRecords survived (artifacts are RESTRICT-protected), leaving exactly
the "RobotRun ArtifactRecords without a RobotRunRecord" state that
REGISTER_ROBOT_RUN reports as inconsistent. RESTRICT makes the database
refuse to delete a Robot that has any RobotRun.

Only the foreign key's delete rule changes; no rows are touched, so the
upgrade is safe on a populated table.
"""

from __future__ import annotations

from collections.abc import Sequence

from alembic import op

revision: str = "51b9e7204713"
down_revision: str | None = "d820064cc715"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

_FK_NAME = "robot_runs_robot_id_fkey"


def _replace_robot_fk(ondelete: str) -> None:
    op.drop_constraint(_FK_NAME, "robot_runs", type_="foreignkey")
    op.create_foreign_key(
        _FK_NAME,
        "robot_runs",
        "robots",
        ["robot_id"],
        ["robot_id"],
        ondelete=ondelete,
    )


def upgrade() -> None:
    _replace_robot_fk("RESTRICT")


def downgrade() -> None:
    _replace_robot_fk("CASCADE")
