from __future__ import annotations

from datetime import datetime
from typing import Any

from sqlalchemy import DateTime, ForeignKey, Index, Integer, String, Text, text
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column, relationship

from sceneops_db.base import Base


class JobModel(Base):
    __tablename__ = "jobs"

    job_id: Mapped[str] = mapped_column(String(128), primary_key=True)
    type: Mapped[str] = mapped_column(String(64), nullable=False)
    status: Mapped[str] = mapped_column(String(32), nullable=False)

    dataset_id: Mapped[str | None] = mapped_column(String(128), nullable=True)
    dataset_version: Mapped[str | None] = mapped_column(String(128), nullable=True)

    params: Mapped[dict[str, Any]] = mapped_column(
        JSONB, nullable=False, server_default=text("'{}'::jsonb")
    )
    steps: Mapped[list[Any]] = mapped_column(
        JSONB, nullable=False, server_default=text("'[]'::jsonb")
    )
    result: Mapped[dict[str, Any] | None] = mapped_column(JSONB, nullable=True)
    error: Mapped[dict[str, Any] | None] = mapped_column(JSONB, nullable=True)

    pipeline_run_id: Mapped[str | None] = mapped_column(String(128), nullable=True)
    pipeline_task_run_id: Mapped[str | None] = mapped_column(String(128), nullable=True)
    pipeline_task_id: Mapped[str | None] = mapped_column(String(128), nullable=True)

    retry_count: Mapped[int] = mapped_column(
        Integer, nullable=False, server_default=text("0")
    )
    max_retries: Mapped[int] = mapped_column(
        Integer, nullable=False, server_default=text("0")
    )
    execution_key: Mapped[str | None] = mapped_column(String(128), nullable=True)

    worker_id: Mapped[str | None] = mapped_column(String(128), nullable=True)

    queued_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    locked_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    heartbeat_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    started_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    finished_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
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

    metadata_: Mapped[dict[str, Any]] = mapped_column(
        "metadata", JSONB, nullable=False, server_default=text("'{}'::jsonb")
    )

    events: Mapped[list["JobEventModel"]] = relationship(
        back_populates="job",
        cascade="all, delete-orphan",
        order_by="JobEventModel.created_at",
    )


class JobEventModel(Base):
    __tablename__ = "job_events"

    event_id: Mapped[str] = mapped_column(String(128), primary_key=True)

    job_id: Mapped[str] = mapped_column(
        String(128),
        ForeignKey("jobs.job_id", ondelete="CASCADE"),
        nullable=False,
    )

    type: Mapped[str] = mapped_column(String(64), nullable=False)
    level: Mapped[str] = mapped_column(String(32), nullable=False)

    job_type: Mapped[str | None] = mapped_column(String(64), nullable=True)
    status: Mapped[str | None] = mapped_column(String(32), nullable=True)

    job_step_id: Mapped[str | None] = mapped_column(String(128), nullable=True)
    job_step_name: Mapped[str | None] = mapped_column(String(128), nullable=True)
    job_step_status: Mapped[str | None] = mapped_column(String(32), nullable=True)

    pipeline_run_id: Mapped[str | None] = mapped_column(String(128), nullable=True)
    pipeline_task_run_id: Mapped[str | None] = mapped_column(String(128), nullable=True)
    pipeline_task_id: Mapped[str | None] = mapped_column(String(128), nullable=True)

    worker_id: Mapped[str | None] = mapped_column(String(128), nullable=True)
    attempt: Mapped[int | None] = mapped_column(Integer, nullable=True)

    message: Mapped[str | None] = mapped_column(Text, nullable=True)
    error: Mapped[dict[str, Any] | None] = mapped_column(JSONB, nullable=True)
    data: Mapped[dict[str, Any]] = mapped_column(
        JSONB, nullable=False, server_default=text("'{}'::jsonb")
    )

    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=text("now()")
    )

    job: Mapped[JobModel] = relationship(back_populates="events")


Index("ix_jobs_type_status", JobModel.type, JobModel.status)
Index("ix_jobs_dataset", JobModel.dataset_id, JobModel.dataset_version)
Index("ix_jobs_pipeline_run_id", JobModel.pipeline_run_id)
Index("ix_jobs_pipeline_task_run_id", JobModel.pipeline_task_run_id)
Index("ix_jobs_status_queued_at", JobModel.status, JobModel.queued_at)
Index("ix_jobs_status_locked_at", JobModel.status, JobModel.locked_at)
Index("ix_jobs_execution_key", JobModel.execution_key)

Index("ix_job_events_job_id_created_at", JobEventModel.job_id, JobEventModel.created_at)
Index("ix_job_events_level_created_at", JobEventModel.level, JobEventModel.created_at)
Index("ix_job_events_type_created_at", JobEventModel.type, JobEventModel.created_at)
Index(
    "ix_job_events_pipeline_run_id_created_at",
    JobEventModel.pipeline_run_id,
    JobEventModel.created_at,
)
