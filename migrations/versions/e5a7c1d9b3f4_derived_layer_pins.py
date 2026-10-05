"""Revision-pinned derived inputs; drop the derived dataset-index pointers

Revision ID: e5a7c1d9b3f4
Revises: b8e4d2a6c917
Create Date: 2026-10-05

ADR-007 step 10 (amendment A8): derived L3 records pin the exact revision of
the immutable manifest they project, and nothing points at the removed
derived dataset index any more.

scenario_sets
    + manifest_artifact_id, manifest_checksum: the one immutable ScenarioSet
      manifest revision the record projects.
inference_runs
    + prediction_manifest_checksum: pins the published prediction manifest
      revision an evaluation consumes.
    - dataset_manifest_uri
scenario_run_records
    - dataset_manifest_uri
dataset_versions
    - manifest_uri: the derived dataset index it pointed at has no producer
      or consumer any more (Scene membership is the SceneRecord table).

Rows written before this revision keep NULL pins. A legacy ScenarioSet or
inference run has no typed, checksum-pinned manifest, so the derived
workflows refuse it loudly instead of guessing; rebuild it instead.
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "e5a7c1d9b3f4"
down_revision: str | None = "b8e4d2a6c917"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column(
        "scenario_sets", sa.Column("manifest_artifact_id", sa.String(128), nullable=True)
    )
    op.add_column(
        "scenario_sets", sa.Column("manifest_checksum", sa.String(80), nullable=True)
    )
    op.add_column(
        "inference_runs",
        sa.Column("prediction_manifest_checksum", sa.String(80), nullable=True),
    )
    op.drop_column("inference_runs", "dataset_manifest_uri")
    op.drop_column("scenario_run_records", "dataset_manifest_uri")
    op.drop_column("dataset_versions", "manifest_uri")


def downgrade() -> None:
    op.add_column("dataset_versions", sa.Column("manifest_uri", sa.Text, nullable=True))
    op.add_column(
        "scenario_run_records", sa.Column("dataset_manifest_uri", sa.Text, nullable=True)
    )
    op.add_column(
        "inference_runs", sa.Column("dataset_manifest_uri", sa.Text, nullable=True)
    )
    op.drop_column("inference_runs", "prediction_manifest_checksum")
    op.drop_column("scenario_sets", "manifest_checksum")
    op.drop_column("scenario_sets", "manifest_artifact_id")
