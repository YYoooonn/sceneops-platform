"""Scene / Episode run records name no DatasetVersion

Revision ID: a3f7c1e9d5b8
Revises: e9b5d3a7c2f4
Create Date: 2026-10-18

ADR-015 §6.4: validation and profiling assess one pinned revision, and a
revision has no DatasetVersion: the same revision is assessed once however
many versions pin it, and readiness is derived per revision. The
dataset_id / dataset_version columns of scene_run_records and
episode_run_records, and their indexes, are dropped. A version's quality
joins its members to the run records by revision pin.

The downgrade restores the columns empty.
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "a3f7c1e9d5b8"
down_revision: str | None = "e9b5d3a7c2f4"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

_TABLES = ("scene_run_records", "episode_run_records")


def upgrade() -> None:
    for table in _TABLES:
        op.drop_index(f"ix_{table}_dataset", table_name=table)
        op.drop_column(table, "dataset_version")
        op.drop_column(table, "dataset_id")


def downgrade() -> None:
    for table in _TABLES:
        op.add_column(table, sa.Column("dataset_id", sa.String(128), nullable=True))
        op.add_column(
            table, sa.Column("dataset_version", sa.String(128), nullable=True)
        )
        op.create_index(f"ix_{table}_dataset", table, ["dataset_id", "dataset_version"])
