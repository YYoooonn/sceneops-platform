"""split step id from pipeline and jobs

Revision ID: b1572d66b02a
Revises: 9962d62d9c25
Create Date: 2026-06-05 14:44:39.087247

"""
from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision: str = 'b1572d66b02a'
down_revision: str | None = '9962d62d9c25'
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    # ── pipeline_step_runs: step_id → pipeline_step_id, step_name → pipeline_step_name ──

    # 1. Add new columns as nullable (existing rows have no value yet)
    op.add_column('pipeline_step_runs', sa.Column('pipeline_step_id', sa.String(length=128), nullable=True))
    op.add_column('pipeline_step_runs', sa.Column('pipeline_step_name', sa.String(length=128), nullable=True))

    # 2. Copy existing data from old columns to new columns
    op.execute("UPDATE pipeline_step_runs SET pipeline_step_id = step_id, pipeline_step_name = step_name")

    # 3. Set NOT NULL now that all rows have values
    op.alter_column('pipeline_step_runs', 'pipeline_step_id', nullable=False)
    op.alter_column('pipeline_step_runs', 'pipeline_step_name', nullable=False)

    # 4. Replace index
    op.drop_index('ix_pipeline_step_runs_pipeline_run_step_id', table_name='pipeline_step_runs')
    op.create_index('ix_pipeline_step_runs_pipeline_run_step_id', 'pipeline_step_runs', ['pipeline_run_id', 'pipeline_step_id'], unique=False)

    # 5. Drop old columns
    op.drop_column('pipeline_step_runs', 'step_id')
    op.drop_column('pipeline_step_runs', 'step_name')

    # ── job_events: step_id/step_name/step_status → job_step_id/job_step_name/job_step_status ──

    # 1. Add new nullable columns
    op.add_column('job_events', sa.Column('job_step_id', sa.String(length=128), nullable=True))
    op.add_column('job_events', sa.Column('job_step_name', sa.String(length=128), nullable=True))
    op.add_column('job_events', sa.Column('job_step_status', sa.String(length=32), nullable=True))

    # 2. Copy existing data (best-effort; old columns may be empty due to prior converter bug)
    op.execute("UPDATE job_events SET job_step_id = step_id, job_step_name = step_name, job_step_status = step_status")

    # 3. Drop old columns
    op.drop_column('job_events', 'step_id')
    op.drop_column('job_events', 'step_name')
    op.drop_column('job_events', 'step_status')


def downgrade() -> None:
    # ── job_events ────────────────────────────────────────────────────────────
    op.add_column('job_events', sa.Column('step_id', sa.VARCHAR(length=128), nullable=True))
    op.add_column('job_events', sa.Column('step_name', sa.VARCHAR(length=128), nullable=True))
    op.add_column('job_events', sa.Column('step_status', sa.VARCHAR(length=32), nullable=True))
    op.execute("UPDATE job_events SET step_id = job_step_id, step_name = job_step_name, step_status = job_step_status")
    op.drop_column('job_events', 'job_step_id')
    op.drop_column('job_events', 'job_step_name')
    op.drop_column('job_events', 'job_step_status')

    # ── pipeline_step_runs ────────────────────────────────────────────────────
    op.add_column('pipeline_step_runs', sa.Column('step_id', sa.VARCHAR(length=128), nullable=True))
    op.add_column('pipeline_step_runs', sa.Column('step_name', sa.VARCHAR(length=128), nullable=True))
    op.execute("UPDATE pipeline_step_runs SET step_id = pipeline_step_id, step_name = pipeline_step_name")
    op.alter_column('pipeline_step_runs', 'step_id', nullable=False)
    op.alter_column('pipeline_step_runs', 'step_name', nullable=False)
    op.drop_index('ix_pipeline_step_runs_pipeline_run_step_id', table_name='pipeline_step_runs')
    op.create_index('ix_pipeline_step_runs_pipeline_run_step_id', 'pipeline_step_runs', ['pipeline_run_id', 'step_id'], unique=False)
    op.drop_column('pipeline_step_runs', 'pipeline_step_id')
    op.drop_column('pipeline_step_runs', 'pipeline_step_name')
