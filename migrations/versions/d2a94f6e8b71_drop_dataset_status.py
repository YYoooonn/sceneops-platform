"""drop dead datasets.status column

Revision ID: d2a94f6e8b71
Revises: ae982d27ca4b
Create Date: 2026-09-14

DatasetStatus (CREATED/REGISTERED/INGESTING/INGESTED/VALIDATING/PROFILING/
READY/FAILED/DEPRECATED) never had a real writer beyond the Pydantic-level
default on create, and no code ever read the persisted value except an
unused `GET /datasets?status=` filter param (see SceneOps V2 Request 06
audit). Unlike DatasetVersionStatus (Request 05), this column has zero
production behavior riding on it, so it is dropped entirely rather than
shrunk. No data migration is needed: the column has no CHECK constraint and
nothing downstream deserializes it once the Python field is gone.
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "d2a94f6e8b71"
down_revision: str | None = "ae982d27ca4b"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.drop_column("datasets", "status")


def downgrade() -> None:
    op.add_column(
        "datasets",
        sa.Column(
            "status",
            sa.String(32),
            nullable=False,
            server_default="created",
        ),
    )
