"""Drop the annotation count and the DatasetVersion workflow configuration

Revision ID: f6c2a8d4e710
Revises: e5a7c1d9b3f4
Create Date: 2026-10-05

ADR-007 step 11 (amendment A9): the canonical schema keeps nothing that only
earlier steps deferred removing.

scenes
    - annotation_count: SceneManifest v2 embeds no annotations (labels are
      independent derived label sets, I-50), so a SceneRecord has nothing to
      count.
dataset_versions
    - required_channels: channel requirements are pipeline / job parameters,
      never DatasetVersion state (ADR-007 §16).
    - metadata: free-form version state is not part of the DatasetVersion
      contract.
datasets
    - metadata: same.

Scenes registered before this revision pin SceneManifest v1 bytes. They are
not rewritten or fabricated into v2: reading one fails loudly
(UnsupportedSceneManifestVersionError) and the fix is rebuilding the scope
with ``replace`` (the Scene builder's semantics version changed with the
schema, so a rebuild is a distinct producer).
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "f6c2a8d4e710"
down_revision: str | None = "e5a7c1d9b3f4"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.drop_column("scenes", "annotation_count")
    op.drop_column("dataset_versions", "required_channels")
    op.drop_column("dataset_versions", "metadata")
    op.drop_column("datasets", "metadata")


def downgrade() -> None:
    op.add_column(
        "datasets",
        sa.Column(
            "metadata",
            postgresql.JSONB,
            nullable=False,
            server_default=sa.text("'{}'::jsonb"),
        ),
    )
    op.add_column(
        "dataset_versions",
        sa.Column(
            "metadata",
            postgresql.JSONB,
            nullable=False,
            server_default=sa.text("'{}'::jsonb"),
        ),
    )
    op.add_column(
        "dataset_versions",
        sa.Column(
            "required_channels",
            postgresql.JSONB,
            nullable=False,
            server_default=sa.text("'[]'::jsonb"),
        ),
    )
    op.add_column(
        "scenes",
        sa.Column(
            "annotation_count",
            sa.Integer,
            nullable=False,
            server_default=sa.text("0"),
        ),
    )
