"""Drop execution_records.status

Revision ID: b3e9d1f7a204
Revises: f6c2a8d4e710
Create Date: 2026-10-07

An execution record is the audit record of one message sent to an execution
backend. Nothing ever updated its status after the send, so every row said
``queued`` whatever happened to the work; the state of the work is the Job's and
the PipelineRun's own. The column and its (status, created_at) index go, and
listing by recency gets a plain created_at index.
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "b3e9d1f7a204"
down_revision: str | None = "f6c2a8d4e710"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.drop_index(
        "ix_execution_records_status_created_at", table_name="execution_records"
    )
    op.drop_column("execution_records", "status")
    op.create_index(
        "ix_execution_records_created_at", "execution_records", ["created_at"]
    )


def downgrade() -> None:
    op.drop_index("ix_execution_records_created_at", table_name="execution_records")
    op.add_column(
        "execution_records",
        sa.Column("status", sa.String(32), nullable=False, server_default="queued"),
    )
    op.alter_column("execution_records", "status", server_default=None)
    op.create_index(
        "ix_execution_records_status_created_at",
        "execution_records",
        ["status", "created_at"],
    )
