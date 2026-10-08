"""Job enqueued_at and finish-time indexes for execution metrics

Revision ID: a9d3f5c1e7b2
Revises: e5b1c3a7d920
Create Date: 2026-10-08

``queued_at`` is the last dispatch of a Job: execution recovery moves it on every
resend, because it is the timestamp the resend threshold and the resend's
single-winner compare-and-set read. It therefore cannot say how long a Job has
been waiting. ``enqueued_at`` is when the Job entered QUEUED from another status
(a dispatch from PENDING, a retry from FAILED, lease recovery from RUNNING, the
orchestrator's insert); a redispatch or resend of a QUEUED Job keeps it. It is
written by the same UPDATE as the transition, so it costs no extra write.

Existing rows: ``enqueued_at`` takes ``queued_at``, the only record of their wait,
except PENDING Jobs, which have not been asked to run (job creation stamps
``queued_at`` anyway).

``ix_jobs_finished_at`` / ``ix_pipeline_runs_finished_at`` serve the window
metrics (Jobs and runs that finished in ``[start, end)``), which otherwise scan
the whole history. Setting ``finished_at`` happens in the terminal UPDATE, which
also changes the indexed ``status``, so the index adds no non-HOT update.
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "a9d3f5c1e7b2"
down_revision: str | None = "e5b1c3a7d920"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column(
        "jobs",
        sa.Column("enqueued_at", sa.DateTime(timezone=True), nullable=True),
    )
    op.execute(
        "UPDATE jobs SET enqueued_at = queued_at "
        "WHERE queued_at IS NOT NULL AND status <> 'pending'"
    )
    op.create_index(
        "ix_jobs_finished_at",
        "jobs",
        ["finished_at"],
        postgresql_where=sa.text("finished_at IS NOT NULL"),
    )
    op.create_index(
        "ix_pipeline_runs_finished_at",
        "pipeline_runs",
        ["finished_at"],
        postgresql_where=sa.text("finished_at IS NOT NULL"),
    )


def downgrade() -> None:
    op.drop_index("ix_pipeline_runs_finished_at", table_name="pipeline_runs")
    op.drop_index("ix_jobs_finished_at", table_name="jobs")
    op.drop_column("jobs", "enqueued_at")
