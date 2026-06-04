"""normalize legacy enum values

Revision ID: e9c5401c7bdb
Revises: 5aa23d896f36
Create Date: 2026-05-24 09:51:48.419862

"""
from __future__ import annotations

from collections.abc import Sequence

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = 'e9c5401c7bdb'
down_revision: str | None = '5aa23d896f36'
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    # jobs.type legacy values
    op.execute(
        """
        UPDATE jobs
        SET type = CASE type
            WHEN 'INGEST_NUSCENES' THEN 'ingest_dataset'
            WHEN 'ingest_nuscenes' THEN 'ingest_dataset'
            WHEN 'PREDICT_MOCK_DETECTION' THEN 'predict_detection'
            WHEN 'predict_mock_detection' THEN 'predict_detection'
            WHEN 'EVALUATE_DETECTION' THEN 'evaluate_detection'
            WHEN 'evaluate_detection' THEN 'evaluate_detection'
            ELSE type
        END
        """
    )

    # jobs.status legacy values
    op.execute(
        """
        UPDATE jobs
        SET status = lower(status)
        WHERE status IN ('PENDING', 'RUNNING', 'SUCCEEDED', 'FAILED', 'CANCELED')
        """
    )

    # jobs.manifest.type legacy values
    op.execute(
        """
        UPDATE jobs
        SET manifest = jsonb_set(
            manifest,
            '{type}',
            to_jsonb(
                CASE manifest->>'type'
                    WHEN 'INGEST_NUSCENES' THEN 'ingest_dataset'
                    WHEN 'ingest_nuscenes' THEN 'ingest_dataset'
                    WHEN 'PREDICT_MOCK_DETECTION' THEN 'predict_detection'
                    WHEN 'predict_mock_detection' THEN 'predict_detection'
                    WHEN 'EVALUATE_DETECTION' THEN 'evaluate_detection'
                    WHEN 'evaluate_detection' THEN 'evaluate_detection'
                    ELSE manifest->>'type'
                END
            ),
            true
        )
        WHERE manifest ? 'type'
        """
    )

    # jobs.manifest.status legacy values
    op.execute(
        """
        UPDATE jobs
        SET manifest = jsonb_set(
            manifest,
            '{status}',
            to_jsonb(lower(manifest->>'status')),
            true
        )
        WHERE manifest ? 'status'
          AND manifest->>'status' IN ('PENDING', 'RUNNING', 'SUCCEEDED', 'FAILED', 'CANCELED')
        """
    )

    # pipeline_step_runs.job_type legacy values
    op.execute(
        """
        UPDATE pipeline_step_runs
        SET job_type = CASE job_type
            WHEN 'INGEST_NUSCENES' THEN 'ingest_dataset'
            WHEN 'ingest_nuscenes' THEN 'ingest_dataset'
            WHEN 'PREDICT_MOCK_DETECTION' THEN 'predict_detection'
            WHEN 'predict_mock_detection' THEN 'predict_detection'
            WHEN 'EVALUATE_DETECTION' THEN 'evaluate_detection'
            WHEN 'evaluate_detection' THEN 'evaluate_detection'
            ELSE job_type
        END
        """
    )

    # pipeline_step_runs.status legacy values
    op.execute(
        """
        UPDATE pipeline_step_runs
        SET status = lower(status)
        WHERE status IN (
            'PENDING',
            'WAITING',
            'RUNNING',
            'SUCCEEDED',
            'FAILED',
            'SKIPPED',
            'CANCELED'
        )
        """
    )

    # pipeline_runs.type legacy values
    op.execute(
        """
        UPDATE pipeline_runs
        SET type = CASE type
            WHEN 'DETECTION_VALIDATION' THEN 'detection_validation'
            ELSE type
        END
        """
    )

    # pipeline_runs.status legacy values
    op.execute(
        """
        UPDATE pipeline_runs
        SET status = lower(status)
        WHERE status IN ('PENDING', 'RUNNING', 'SUCCEEDED', 'FAILED', 'CANCELED')
        """
    )


def downgrade() -> None:
    # 보통 legacy enum normalization은 downgrade를 완벽히 되돌릴 필요가 적음.
    # 그래도 형식상 reverse를 둔다면 아래 정도로 충분.
    op.execute(
        """
        UPDATE jobs
        SET type = CASE type
            WHEN 'ingest_dataset' THEN 'ingest_nuscenes'
            WHEN 'predict_detection' THEN 'predict_mock_detection'
            ELSE type
        END
        """
    )

    op.execute(
        """
        UPDATE jobs
        SET manifest = jsonb_set(
            manifest,
            '{type}',
            to_jsonb(
                CASE manifest->>'type'
                    WHEN 'ingest_dataset' THEN 'ingest_nuscenes'
                    WHEN 'predict_detection' THEN 'predict_mock_detection'
                    ELSE manifest->>'type'
                END
            ),
            true
        )
        WHERE manifest ? 'type'
        """
    )

    op.execute(
        """
        UPDATE pipeline_step_runs
        SET job_type = CASE job_type
            WHEN 'ingest_dataset' THEN 'ingest_nuscenes'
            WHEN 'predict_detection' THEN 'predict_mock_detection'
            ELSE job_type
        END
        """
    )
