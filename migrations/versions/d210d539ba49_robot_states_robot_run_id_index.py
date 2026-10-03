"""robot_states.robot_run_id: add the index the model declares

Revision ID: d210d539ba49
Revises: 7a3d5e9c1b42
Create Date: 2026-10-03

``RobotStateModel.robot_run_id`` is declared with ``index=True``, but no
migration created ``ix_robot_states_robot_run_id``, so ``alembic check``
reported it as model/schema drift. Only the single-column index is added;
no column, constraint or row changes, so the upgrade is safe on a populated
table.
"""

from __future__ import annotations

from collections.abc import Sequence

from alembic import op

revision: str = "d210d539ba49"
down_revision: str | None = "7a3d5e9c1b42"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

_INDEX_NAME = "ix_robot_states_robot_run_id"


def upgrade() -> None:
    op.create_index(_INDEX_NAME, "robot_states", ["robot_run_id"])


def downgrade() -> None:
    op.drop_index(_INDEX_NAME, table_name="robot_states")
