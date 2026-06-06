"""rename pipeline step to task

Revision ID: fa61176f354f
Revises: b1572d66b02a
Create Date: 2026-06-06

"""
from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = 'fa61176f354f'
down_revision: str | None = 'b1572d66b02a'
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    # ── pipeline_step_runs → pipeline_task_runs ────────────────────────────────

    # 1. Drop old indexes before renaming table/columns
    op.drop_index('ix_pipeline_step_runs_pipeline_run_order', table_name='pipeline_step_runs')
    op.drop_index('ix_pipeline_step_runs_pipeline_run_step_id', table_name='pipeline_step_runs')
    op.drop_index('ix_pipeline_step_runs_job_id', table_name='pipeline_step_runs')
    op.drop_index('ix_pipeline_step_runs_status', table_name='pipeline_step_runs')

    # 2. Rename columns inside the table first (before renaming the table)
    op.alter_column('pipeline_step_runs', 'pipeline_step_run_id', new_column_name='pipeline_task_run_id')
    op.alter_column('pipeline_step_runs', 'pipeline_step_id', new_column_name='pipeline_task_id')
    op.alter_column('pipeline_step_runs', 'pipeline_step_name', new_column_name='pipeline_task_name')
    op.alter_column('pipeline_step_runs', 'step_order', new_column_name='task_order')
    op.alter_column('pipeline_step_runs', 'depends_on_step_ids', new_column_name='depends_on_task_ids')

    # 3. Rename the table
    op.rename_table('pipeline_step_runs', 'pipeline_task_runs')

    # 4. Recreate indexes with new names
    op.create_index('ix_pipeline_task_runs_pipeline_run_order', 'pipeline_task_runs', ['pipeline_run_id', 'task_order'], unique=False)
    op.create_index('ix_pipeline_task_runs_pipeline_run_task_id', 'pipeline_task_runs', ['pipeline_run_id', 'pipeline_task_id'], unique=False)
    op.create_index('ix_pipeline_task_runs_job_id', 'pipeline_task_runs', ['job_id'], unique=False)
    op.create_index('ix_pipeline_task_runs_status', 'pipeline_task_runs', ['status'], unique=False)

    # ── jobs: pipeline_step_run_id → pipeline_task_run_id ─────────────────────

    op.alter_column('jobs', 'pipeline_step_run_id', new_column_name='pipeline_task_run_id')
    op.alter_column('jobs', 'pipeline_step_id', new_column_name='pipeline_task_id')

    op.drop_index('ix_jobs_pipeline_step_run_id', table_name='jobs')
    op.create_index('ix_jobs_pipeline_task_run_id', 'jobs', ['pipeline_task_run_id'], unique=False)

    # ── job_events: pipeline_step_run_id → pipeline_task_run_id ───────────────

    op.alter_column('job_events', 'pipeline_step_run_id', new_column_name='pipeline_task_run_id')
    op.alter_column('job_events', 'pipeline_step_id', new_column_name='pipeline_task_id')

    # ── domain run records: pipeline_step_run_id → pipeline_task_run_id ───────

    for table in (
        'dataset_run_records',
        'scene_run_records',
        'scenario_run_records',
        'inference_runs',
        'evaluation_runs',
        'label_runs',
    ):
        op.alter_column(table, 'pipeline_step_run_id', new_column_name='pipeline_task_run_id')


def downgrade() -> None:
    # ── domain run records ────────────────────────────────────────────────────

    for table in (
        'dataset_run_records',
        'scene_run_records',
        'scenario_run_records',
        'inference_runs',
        'evaluation_runs',
        'label_runs',
    ):
        op.alter_column(table, 'pipeline_task_run_id', new_column_name='pipeline_step_run_id')

    # ── job_events ────────────────────────────────────────────────────────────

    op.alter_column('job_events', 'pipeline_task_run_id', new_column_name='pipeline_step_run_id')
    op.alter_column('job_events', 'pipeline_task_id', new_column_name='pipeline_step_id')

    # ── jobs ──────────────────────────────────────────────────────────────────

    op.drop_index('ix_jobs_pipeline_task_run_id', table_name='jobs')
    op.create_index('ix_jobs_pipeline_step_run_id', 'jobs', ['pipeline_step_run_id'], unique=False)

    op.alter_column('jobs', 'pipeline_task_run_id', new_column_name='pipeline_step_run_id')
    op.alter_column('jobs', 'pipeline_task_id', new_column_name='pipeline_step_id')

    # ── pipeline_task_runs → pipeline_step_runs ────────────────────────────────

    op.drop_index('ix_pipeline_task_runs_pipeline_run_order', table_name='pipeline_task_runs')
    op.drop_index('ix_pipeline_task_runs_pipeline_run_task_id', table_name='pipeline_task_runs')
    op.drop_index('ix_pipeline_task_runs_job_id', table_name='pipeline_task_runs')
    op.drop_index('ix_pipeline_task_runs_status', table_name='pipeline_task_runs')

    op.rename_table('pipeline_task_runs', 'pipeline_step_runs')

    op.alter_column('pipeline_step_runs', 'pipeline_task_run_id', new_column_name='pipeline_step_run_id')
    op.alter_column('pipeline_step_runs', 'pipeline_task_id', new_column_name='pipeline_step_id')
    op.alter_column('pipeline_step_runs', 'pipeline_task_name', new_column_name='pipeline_step_name')
    op.alter_column('pipeline_step_runs', 'task_order', new_column_name='step_order')
    op.alter_column('pipeline_step_runs', 'depends_on_task_ids', new_column_name='depends_on_step_ids')

    op.create_index('ix_pipeline_step_runs_pipeline_run_order', 'pipeline_step_runs', ['pipeline_run_id', 'step_order'], unique=False)
    op.create_index('ix_pipeline_step_runs_pipeline_run_step_id', 'pipeline_step_runs', ['pipeline_run_id', 'pipeline_step_id'], unique=False)
    op.create_index('ix_pipeline_step_runs_job_id', 'pipeline_step_runs', ['job_id'], unique=False)
    op.create_index('ix_pipeline_step_runs_status', 'pipeline_step_runs', ['status'], unique=False)
