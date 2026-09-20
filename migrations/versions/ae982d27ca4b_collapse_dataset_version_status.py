"""collapse dataset_versions.status to the generic lifecycle

Revision ID: ae982d27ca4b
Revises: b5f4b88342e2
Create Date: 2026-09-13

SceneOps V2 Request 05: DatasetVersionStatus no longer carries Scene workflow
states (ingesting/ingested/validating/profiling/ready) or the never-written
failed/deprecated values — see the Request 05 audit. `status` is a plain
String(32) column (no native Postgres enum type, no CHECK constraint), so no
schema change is required. This is a data-only migration: any existing row
whose status is one of the removed values is rewritten to 'registered' so
`DatasetVersionRecord` (whose Pydantic enum now only has REGISTERED) can
still deserialize every row.

This is a lossy, one-directional remap — the original per-row distinction
between e.g. 'ingested' and 'ready' is not recoverable, so downgrade() is a
no-op rather than a fabricated reverse mapping.
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "ae982d27ca4b"
down_revision: str | None = "b5f4b88342e2"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

_REMOVED_STATUS_VALUES = (
    "ingesting",
    "ingested",
    "validating",
    "profiling",
    "ready",
    "failed",
    "deprecated",
)


def upgrade() -> None:
    dataset_versions = sa.table("dataset_versions", sa.column("status", sa.String))
    op.execute(
        dataset_versions.update()
        .where(dataset_versions.c.status.in_(_REMOVED_STATUS_VALUES))
        .values(status="registered")
    )


def downgrade() -> None:
    # Lossy remap — see module docstring. Nothing to restore.
    pass
