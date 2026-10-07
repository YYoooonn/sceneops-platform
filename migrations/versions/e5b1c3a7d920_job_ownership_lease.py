"""Job ownership lease: claim generation and lease expiry

Revision ID: e5b1c3a7d920
Revises: c7d2e4a9f135
Create Date: 2026-10-07

A RUNNING Job is owned by one claim. ``lease_generation`` counts the claims of a
Job and identifies the current one: every claim increments it and every write of
the claiming worker is conditional on it, so a worker whose claim was taken over
writes nothing, even when a redelivered message gives the new owner the same
``worker_id``. ``lease_expires_at`` is the PostgreSQL time until which the claim is
held; the worker renews it while it runs and job lease recovery reclaims a RUNNING
Job whose lease has passed.

Existing rows: a Job that was ever claimed (``worker_id`` set) starts at generation
1. A RUNNING Job gets the lease its last recorded activity implies, which has long
passed, so the first recovery pass reclaims the Jobs that workers abandoned before
leases existed.
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "e5b1c3a7d920"
down_revision: str | None = "c7d2e4a9f135"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column(
        "jobs",
        sa.Column(
            "lease_generation",
            sa.Integer(),
            nullable=False,
            server_default=sa.text("0"),
        ),
    )
    op.add_column(
        "jobs",
        sa.Column("lease_expires_at", sa.DateTime(timezone=True), nullable=True),
    )
    op.execute("UPDATE jobs SET lease_generation = 1 WHERE worker_id IS NOT NULL")
    op.execute(
        "UPDATE jobs SET lease_expires_at = "
        "COALESCE(heartbeat_at, locked_at, started_at, updated_at) "
        "WHERE status = 'running'"
    )
    op.create_index(
        "ix_jobs_running_lease_expires_at",
        "jobs",
        ["lease_expires_at"],
        postgresql_where=sa.text("status = 'running'"),
    )


def downgrade() -> None:
    op.drop_index("ix_jobs_running_lease_expires_at", table_name="jobs")
    op.drop_column("jobs", "lease_expires_at")
    op.drop_column("jobs", "lease_generation")
