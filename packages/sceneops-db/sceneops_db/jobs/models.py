from __future__ import annotations

from datetime import datetime
from typing import Any

from sqlalchemy import DateTime, ForeignKey, Index, Integer, String, Text, text
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from sceneops_db.base import Base


class JobModel(Base):
    __tablename__ = "jobs"

    id: Mapped[str] = mapped_column(String(64), primary_key=True)
    type: Mapped[str] = mapped_column(String(64), nullable=False)
    status: Mapped[str] = mapped_column(String(32), nullable=False)

    dataset_id: Mapped[str] = mapped_column(String(128), nullable=False)
    dataset_version: Mapped[str] = mapped_column(String(128), nullable=False)

    pipeline_run_id: Mapped[str | None] = mapped_column(String(64), nullable=True)
    pipeline_step_run_id: Mapped[str | None] = mapped_column(String(64), nullable=True)
    pipeline_step_name: Mapped[str | None] = mapped_column(String(64), nullable=True)

    run_id: Mapped[str | None] = mapped_column(String(128), nullable=True)
    evaluation_id: Mapped[str | None] = mapped_column(String(128), nullable=True)

    params: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False, default=dict)
    result: Mapped[dict[str, Any] | None] = mapped_column(JSONB, nullable=True)
    error: Mapped[dict[str, Any] | None] = mapped_column(JSONB, nullable=True)

    retry_count: Mapped[int] = mapped_column(
        Integer,
        nullable=False,
        server_default="0",
    )
    max_retries: Mapped[int] = mapped_column(
        Integer,
        nullable=False,
        server_default="0",
    )

    worker_id: Mapped[str | None] = mapped_column(String(128), nullable=True)

    queued_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    locked_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    heartbeat_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)

    manifest: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False)

    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        server_default=text("now()"),
        nullable=False,
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        server_default=text("now()"),
        nullable=False,
    )

    started_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    finished_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)


class JobEventModel(Base):
    __tablename__ = "job_events"

    id: Mapped[str] = mapped_column(String(64), primary_key=True)

    job_id: Mapped[str] = mapped_column(
        String(64),
        ForeignKey("jobs.id", ondelete="CASCADE"),
        nullable=False,
    )

    event_type: Mapped[str] = mapped_column(String(64), nullable=False)
    level: Mapped[str] = mapped_column(String(16), nullable=False)
    message: Mapped[str | None] = mapped_column(Text, nullable=True)
    payload: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False, default=dict)

    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        server_default=text("now()"),
        nullable=False,
    )


Index("ix_jobs_type", JobModel.type)
Index("ix_jobs_status", JobModel.status)
Index("ix_jobs_created_at", JobModel.created_at)
Index("ix_jobs_dataset", JobModel.dataset_id, JobModel.dataset_version)
Index("ix_jobs_worker_id", JobModel.worker_id)
Index("ix_jobs_queued_at", JobModel.queued_at)

Index("ix_jobs_pipeline_run_id", JobModel.pipeline_run_id)
Index("ix_jobs_pipeline_step_run_id", JobModel.pipeline_step_run_id)
Index(
    "ix_jobs_pipeline_run_step",
    JobModel.pipeline_run_id,
    JobModel.pipeline_step_name,
)

Index("ix_job_events_job_id", JobEventModel.job_id)
Index("ix_job_events_created_at", JobEventModel.created_at)
Index("ix_job_events_job_id_created_at", JobEventModel.job_id, JobEventModel.created_at)
Index("ix_job_events_event_type", JobEventModel.event_type)
