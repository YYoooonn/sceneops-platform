from __future__ import annotations

from datetime import datetime
from typing import Any

from sqlalchemy import DateTime, Float, Index, Integer, String, Text, text
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from sceneops_db.base import Base


class EvaluationRunModel(Base):
    __tablename__ = "evaluation_runs"

    run_id: Mapped[str] = mapped_column(String(128), primary_key=True)
    type: Mapped[str] = mapped_column(
        String(64), nullable=False, server_default="evaluation"
    )
    status: Mapped[str] = mapped_column(String(32), nullable=False)

    dataset_id: Mapped[str] = mapped_column(String(128), nullable=False)
    dataset_version: Mapped[str] = mapped_column(String(128), nullable=False)

    model_id: Mapped[str | None] = mapped_column(String(128), nullable=True)
    model_version: Mapped[str | None] = mapped_column(String(128), nullable=True)

    inference_run_id: Mapped[str | None] = mapped_column(String(128), nullable=True)
    predictions_root_uri: Mapped[str | None] = mapped_column(Text, nullable=True)

    evaluator_id: Mapped[str] = mapped_column(
        String(128), nullable=False, server_default="center-distance"
    )
    task_type: Mapped[str] = mapped_column(
        String(64), nullable=False, server_default="detection"
    )

    sample_count: Mapped[int | None] = mapped_column(Integer, nullable=True)
    prediction_count: Mapped[int | None] = mapped_column(Integer, nullable=True)
    ground_truth_count: Mapped[int | None] = mapped_column(Integer, nullable=True)
    evaluation_unit: Mapped[str | None] = mapped_column(String(64), nullable=True)

    primary_metric_name: Mapped[str | None] = mapped_column(String(128), nullable=True)
    primary_metric_value: Mapped[float | None] = mapped_column(Float, nullable=True)

    evaluation_manifest_uri: Mapped[str | None] = mapped_column(Text, nullable=True)
    metrics_uri: Mapped[str | None] = mapped_column(Text, nullable=True)

    pipeline_run_id: Mapped[str | None] = mapped_column(String(128), nullable=True)
    pipeline_task_run_id: Mapped[str | None] = mapped_column(String(128), nullable=True)
    job_id: Mapped[str | None] = mapped_column(String(128), nullable=True)

    params: Mapped[dict[str, Any]] = mapped_column(
        JSONB, nullable=False, server_default=text("'{}'::jsonb")
    )
    result: Mapped[dict[str, Any] | None] = mapped_column(JSONB, nullable=True)
    error: Mapped[dict[str, Any] | None] = mapped_column(JSONB, nullable=True)

    artifact_root_uri: Mapped[str | None] = mapped_column(Text, nullable=True)
    manifest_uri: Mapped[str | None] = mapped_column(Text, nullable=True)

    summary: Mapped[dict[str, Any]] = mapped_column(
        JSONB, nullable=False, server_default=text("'{}'::jsonb")
    )
    metrics: Mapped[dict[str, Any]] = mapped_column(
        JSONB, nullable=False, server_default=text("'{}'::jsonb")
    )
    class_metrics: Mapped[dict[str, Any]] = mapped_column(
        JSONB, nullable=False, server_default=text("'{}'::jsonb")
    )

    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=text("now()")
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        server_default=text("now()"),
        onupdate=text("now()"),
    )
    started_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    finished_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )

    metadata_: Mapped[dict[str, Any]] = mapped_column(
        "metadata", JSONB, nullable=False, server_default=text("'{}'::jsonb")
    )


Index("ix_evaluation_runs_status", EvaluationRunModel.status)
Index("ix_evaluation_runs_task_type", EvaluationRunModel.task_type)
Index(
    "ix_evaluation_runs_dataset",
    EvaluationRunModel.dataset_id,
    EvaluationRunModel.dataset_version,
)
Index(
    "ix_evaluation_runs_model",
    EvaluationRunModel.model_id,
    EvaluationRunModel.model_version,
)
Index("ix_evaluation_runs_inference_run_id", EvaluationRunModel.inference_run_id)
Index("ix_evaluation_runs_job_id", EvaluationRunModel.job_id)
Index("ix_evaluation_runs_pipeline_run_id", EvaluationRunModel.pipeline_run_id)
Index("ix_evaluation_runs_created_at", EvaluationRunModel.created_at)
