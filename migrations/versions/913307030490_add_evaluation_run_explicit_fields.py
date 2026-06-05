"""add explicit fields to evaluation_runs

Revision ID: 913307030490
Revises: b1572d66b02a
Create Date: 2026-06-06 00:00:00.000000

"""
from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision: str = '913307030490'
down_revision: str | None = 'b1572d66b02a'
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column('evaluation_runs', sa.Column('prediction_count', sa.Integer(), nullable=True))
    op.add_column('evaluation_runs', sa.Column('ground_truth_count', sa.Integer(), nullable=True))
    op.add_column('evaluation_runs', sa.Column('evaluation_unit', sa.String(length=64), nullable=True))
    op.add_column('evaluation_runs', sa.Column('primary_metric_name', sa.String(length=128), nullable=True))
    op.add_column('evaluation_runs', sa.Column('primary_metric_value', sa.Float(), nullable=True))


def downgrade() -> None:
    op.drop_column('evaluation_runs', 'primary_metric_value')
    op.drop_column('evaluation_runs', 'primary_metric_name')
    op.drop_column('evaluation_runs', 'evaluation_unit')
    op.drop_column('evaluation_runs', 'ground_truth_count')
    op.drop_column('evaluation_runs', 'prediction_count')
