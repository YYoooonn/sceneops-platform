from __future__ import annotations

from datetime import datetime
from typing import Any

from sqlalchemy import DateTime, ForeignKey, Index, Integer, String, text
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from sceneops_db.base import Base


class PipelineRunModel(Base):
    __tablename__ = "pipeline_runs"

    id: Mapped[str] = mapped_column(String(64), primary_key=True)

    type: Mapped[str] = mapped_column(String(64), nullable=False)
    status: Mapped[str] = mapped_column(String(32), nullable=False)

    dataset_id: Mapped[str] = mapped_column(String(128), nullable=False)
    dataset_version: Mapped[str] = mapped_column(String(128), nullable=False)

    model_id: Mapped[str | None] = mapped_column(String(128), nullable=True)
    model_version: Mapped[str | None] = mapped_column(String(128), nullable=True)

    params: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False, default=dict)
    result: Mapped[dict[str, Any] | None] = mapped_column(JSONB, nullable=True)
    error: Mapped[dict[str, Any] | None] = mapped_column(JSONB, nullable=True)

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

    started_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True),
        nullable=True,
    )
    finished_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True),
        nullable=True,
    )


class PipelineStepRunModel(Base):
    __tablename__ = "pipeline_step_runs"

    id: Mapped[str] = mapped_column(String(64), primary_key=True)

    pipeline_run_id: Mapped[str] = mapped_column(
        String(64),
        ForeignKey("pipeline_runs.id", ondelete="CASCADE"),
        nullable=False,
    )

    step_name: Mapped[str] = mapped_column(String(64), nullable=False)
    step_order: Mapped[int] = mapped_column(Integer, nullable=False)
    status: Mapped[str] = mapped_column(String(32), nullable=False)

    job_type: Mapped[str] = mapped_column(String(64), nullable=False)
    job_id: Mapped[str | None] = mapped_column(String(64), nullable=True)

    depends_on_step_names: Mapped[list[str]] = mapped_column(
        JSONB,
        nullable=False,
        default=list,
    )

    params: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False, default=dict)
    result: Mapped[dict[str, Any] | None] = mapped_column(JSONB, nullable=True)
    error: Mapped[dict[str, Any] | None] = mapped_column(JSONB, nullable=True)

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

    started_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True),
        nullable=True,
    )
    finished_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True),
        nullable=True,
    )


Index("ix_pipeline_runs_type", PipelineRunModel.type)
Index("ix_pipeline_runs_status", PipelineRunModel.status)
Index("ix_pipeline_runs_created_at", PipelineRunModel.created_at)
Index(
    "ix_pipeline_runs_dataset",
    PipelineRunModel.dataset_id,
    PipelineRunModel.dataset_version,
)

Index("ix_pipeline_step_runs_pipeline_run_id", PipelineStepRunModel.pipeline_run_id)
Index(
    "ix_pipeline_step_runs_pipeline_run_order",
    PipelineStepRunModel.pipeline_run_id,
    PipelineStepRunModel.step_order,
)
Index("ix_pipeline_step_runs_status", PipelineStepRunModel.status)
Index("ix_pipeline_step_runs_job_id", PipelineStepRunModel.job_id)
