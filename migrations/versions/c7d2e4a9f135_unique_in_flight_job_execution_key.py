"""At most one in-flight Job per execution key

Revision ID: c7d2e4a9f135
Revises: b3e9d1f7a204
Create Date: 2026-10-07

Job creation deduplicates on ``execution_key`` by reading before it inserts, so
concurrent requests for the same Job each found nothing and each inserted one. A
partial unique index over the PENDING / QUEUED / RUNNING Jobs makes PostgreSQL
refuse the second in-flight Job of a key; ``PostgresJobRepository.create`` inserts
with ``ON CONFLICT DO NOTHING`` against it and the loser joins the winner. Finished
Jobs stay outside the index: forced reruns and replacements share their key.

The upgrade fails, naming the key, if a database already holds two in-flight Jobs
with one key; finish or fail all but one of them and upgrade again.
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "c7d2e4a9f135"
down_revision: str | None = "b3e9d1f7a204"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_index(
        "uq_jobs_execution_key_in_flight",
        "jobs",
        ["execution_key"],
        unique=True,
        postgresql_where=sa.text("status IN ('pending', 'queued', 'running')"),
    )


def downgrade() -> None:
    op.drop_index("uq_jobs_execution_key_in_flight", table_name="jobs")
