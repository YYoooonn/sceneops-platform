from __future__ import annotations

from datetime import datetime
from typing import Any

from sqlalchemy import Boolean, DateTime, Float, Integer, String, Text, text
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from sceneops_db.base import Base


class InferenceRunModel(Base):
    __tablename__ = "inference_runs"

    id: Mapped[str] = mapped_column(String(128), primary_key=True)

    dataset_id: Mapped[str] = mapped_column(String(128), index=True, nullable=False)
    dataset_version: Mapped[str] = mapped_column(
        String(128), index=True, nullable=False
    )

    model_id: Mapped[str] = mapped_column(String(128), index=True, nullable=False)
    model_version: Mapped[str] = mapped_column(String(128), index=True, nullable=False)

    status: Mapped[str] = mapped_column(String(32), index=True, nullable=False)

    sample_count: Mapped[int | None] = mapped_column(Integer, nullable=True)
    prediction_count: Mapped[int | None] = mapped_column(Integer, nullable=True)

    run_manifest_uri: Mapped[str | None] = mapped_column(Text, nullable=True)
    predictions_root_uri: Mapped[str | None] = mapped_column(Text, nullable=True)

    pipeline_run_id: Mapped[str | None] = mapped_column(String(128), index=True)
    pipeline_step_run_id: Mapped[str | None] = mapped_column(String(128), index=True)
    job_id: Mapped[str | None] = mapped_column(String(128), index=True)

    metrics: Mapped[dict[str, Any]] = mapped_column(
        JSONB,
        nullable=False,
        default=dict,
    )
    metadata_: Mapped[dict[str, Any]] = mapped_column(
        "metadata",
        JSONB,
        nullable=False,
        default=dict,
    )
    error: Mapped[dict[str, Any] | None] = mapped_column(JSONB, nullable=True)

    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        server_default=text("now()"),
        nullable=False,
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        server_default=text("now()"),
        onupdate=text("now()"),
        nullable=False,
    )
    started_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    finished_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class EvaluationRunModel(Base):
    __tablename__ = "evaluation_runs"

    id: Mapped[str] = mapped_column(String(128), primary_key=True)

    inference_run_id: Mapped[str] = mapped_column(
        String(128),
        index=True,
        nullable=False,
    )

    dataset_id: Mapped[str] = mapped_column(String(128), index=True, nullable=False)
    dataset_version: Mapped[str] = mapped_column(
        String(128), index=True, nullable=False
    )

    model_id: Mapped[str] = mapped_column(String(128), index=True, nullable=False)
    model_version: Mapped[str] = mapped_column(String(128), index=True, nullable=False)

    evaluator_id: Mapped[str] = mapped_column(String(128), nullable=False)
    status: Mapped[str] = mapped_column(String(32), index=True, nullable=False)

    sample_count: Mapped[int | None] = mapped_column(Integer, nullable=True)

    evaluation_manifest_uri: Mapped[str | None] = mapped_column(Text, nullable=True)
    samples_root_uri: Mapped[str | None] = mapped_column(Text, nullable=True)

    metrics: Mapped[dict[str, Any]] = mapped_column(
        JSONB,
        nullable=False,
        default=dict,
    )
    class_metrics: Mapped[dict[str, Any]] = mapped_column(
        JSONB,
        nullable=False,
        default=dict,
    )

    pipeline_run_id: Mapped[str | None] = mapped_column(String(128), index=True)
    pipeline_step_run_id: Mapped[str | None] = mapped_column(String(128), index=True)
    job_id: Mapped[str | None] = mapped_column(String(128), index=True)

    metadata_: Mapped[dict[str, Any]] = mapped_column(
        "metadata",
        JSONB,
        nullable=False,
        default=dict,
    )
    error: Mapped[dict[str, Any] | None] = mapped_column(JSONB, nullable=True)

    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        server_default=text("now()"),
        nullable=False,
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        server_default=text("now()"),
        onupdate=text("now()"),
        nullable=False,
    )
    started_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    finished_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class DatasetValidationRunModel(Base):
    __tablename__ = "dataset_validation_runs"

    id: Mapped[str] = mapped_column(String(128), primary_key=True)

    dataset_id: Mapped[str] = mapped_column(String(128), index=True, nullable=False)
    dataset_version: Mapped[str] = mapped_column(
        String(128), index=True, nullable=False
    )

    # Run lifecycle status: pending/running/succeeded/failed...
    status: Mapped[str] = mapped_column(String(32), index=True, nullable=False)

    # Dataset quality gate status: ready/warning/failed/error
    validation_status: Mapped[str | None] = mapped_column(String(32), index=True)

    should_block_pipeline: Mapped[bool] = mapped_column(
        Boolean,
        nullable=False,
        server_default=text("false"),
    )
    decision_reason: Mapped[str | None] = mapped_column(Text, nullable=True)

    dataset_manifest_uri: Mapped[str | None] = mapped_column(Text)
    validation_report_uri: Mapped[str | None] = mapped_column(Text)

    scope: Mapped[str | None] = mapped_column(String(32))
    max_samples: Mapped[int | None] = mapped_column(Integer)

    scene_count: Mapped[int | None] = mapped_column(Integer)
    sample_count: Mapped[int | None] = mapped_column(Integer)
    annotation_count: Mapped[int | None] = mapped_column(Integer)

    validated_scene_count: Mapped[int | None] = mapped_column(Integer)
    validated_sample_count: Mapped[int | None] = mapped_column(Integer)

    issue_count: Mapped[int | None] = mapped_column(Integer)
    error_count: Mapped[int | None] = mapped_column(Integer)
    warning_count: Mapped[int | None] = mapped_column(Integer)

    missing_scene_count: Mapped[int | None] = mapped_column(Integer)
    missing_sample_count: Mapped[int | None] = mapped_column(Integer)
    missing_channel_count: Mapped[int | None] = mapped_column(Integer)
    missing_artifact_count: Mapped[int | None] = mapped_column(Integer)

    pipeline_run_id: Mapped[str | None] = mapped_column(String(128), index=True)
    pipeline_step_run_id: Mapped[str | None] = mapped_column(String(128), index=True)
    job_id: Mapped[str | None] = mapped_column(String(128), index=True)

    metadata_: Mapped[dict[str, Any]] = mapped_column(
        "metadata",
        JSONB,
        nullable=False,
        default=dict,
    )
    error: Mapped[dict[str, Any] | None] = mapped_column(JSONB, nullable=True)

    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        server_default=text("now()"),
        nullable=False,
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        server_default=text("now()"),
        onupdate=text("now()"),
        nullable=False,
    )
    started_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    finished_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class DatasetProfileRunModel(Base):
    __tablename__ = "dataset_profile_runs"

    id: Mapped[str] = mapped_column(String(128), primary_key=True)

    dataset_id: Mapped[str] = mapped_column(String(128), index=True, nullable=False)
    dataset_version: Mapped[str] = mapped_column(
        String(128), index=True, nullable=False
    )

    # Run lifecycle status: pending/running/succeeded/failed...
    status: Mapped[str] = mapped_column(String(32), index=True, nullable=False)

    dataset_manifest_uri: Mapped[str | None] = mapped_column(Text)
    profile_report_uri: Mapped[str | None] = mapped_column(Text)

    scope: Mapped[str | None] = mapped_column(String(32))
    max_samples: Mapped[int | None] = mapped_column(Integer)

    scene_count: Mapped[int | None] = mapped_column(Integer)
    sample_count: Mapped[int | None] = mapped_column(Integer)
    annotation_count: Mapped[int | None] = mapped_column(Integer)

    profiled_scene_count: Mapped[int | None] = mapped_column(Integer)
    profiled_sample_count: Mapped[int | None] = mapped_column(Integer)

    observed_channel_count: Mapped[int | None] = mapped_column(Integer)
    observed_channels: Mapped[list[str] | None] = mapped_column(
        JSONB,
        nullable=True,
    )

    missing_required_channel_count: Mapped[int | None] = mapped_column(Integer)
    sensor_coverage_ratio: Mapped[float | None] = mapped_column(Float)

    empty_annotation_sample_count: Mapped[int | None] = mapped_column(Integer)
    empty_annotation_sample_ratio: Mapped[float | None] = mapped_column(Float)

    pipeline_run_id: Mapped[str | None] = mapped_column(String(128), index=True)
    pipeline_step_run_id: Mapped[str | None] = mapped_column(String(128), index=True)
    job_id: Mapped[str | None] = mapped_column(String(128), index=True)

    metadata_: Mapped[dict[str, Any]] = mapped_column(
        "metadata",
        JSONB,
        nullable=False,
        default=dict,
    )
    error: Mapped[dict[str, Any] | None] = mapped_column(JSONB, nullable=True)

    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        server_default=text("now()"),
        nullable=False,
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        server_default=text("now()"),
        onupdate=text("now()"),
        nullable=False,
    )
    started_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    finished_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
