from __future__ import annotations

from datetime import datetime
from typing import Any

from sqlalchemy import DateTime, Index, Integer, String, Text, text
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from sceneops_db.base import Base


class InferenceRunModel(Base):
    __tablename__ = "inference_runs"

    run_id: Mapped[str] = mapped_column(String(128), primary_key=True)
    type: Mapped[str] = mapped_column(
        String(64), nullable=False, server_default="inference"
    )
    status: Mapped[str] = mapped_column(String(32), nullable=False)

    dataset_id: Mapped[str] = mapped_column(String(128), nullable=False)
    dataset_version: Mapped[str] = mapped_column(String(128), nullable=False)

    model_id: Mapped[str] = mapped_column(String(128), nullable=False)
    model_version: Mapped[str] = mapped_column(String(128), nullable=False)

    dataset_manifest_uri: Mapped[str | None] = mapped_column(Text, nullable=True)
    inference_backend: Mapped[str] = mapped_column(
        String(64), nullable=False, server_default="mock"
    )

    predictions_root_uri: Mapped[str | None] = mapped_column(Text, nullable=True)
    prediction_manifest_uri: Mapped[str | None] = mapped_column(Text, nullable=True)

    sample_count: Mapped[int | None] = mapped_column(Integer, nullable=True)
    prediction_count: Mapped[int | None] = mapped_column(Integer, nullable=True)

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

    metrics: Mapped[dict[str, Any]] = mapped_column(
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


Index("ix_inference_runs_status", InferenceRunModel.status)
Index(
    "ix_inference_runs_dataset",
    InferenceRunModel.dataset_id,
    InferenceRunModel.dataset_version,
)
Index(
    "ix_inference_runs_model",
    InferenceRunModel.model_id,
    InferenceRunModel.model_version,
)
Index("ix_inference_runs_job_id", InferenceRunModel.job_id)
Index("ix_inference_runs_pipeline_run_id", InferenceRunModel.pipeline_run_id)
Index("ix_inference_runs_created_at", InferenceRunModel.created_at)
