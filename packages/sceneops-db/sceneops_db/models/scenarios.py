from __future__ import annotations

from datetime import datetime
from typing import Any

from sqlalchemy import DateTime, Float, Index, Integer, String, Text, text
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from sceneops_db.base import Base


class ScenarioSetModel(Base):
    __tablename__ = "scenario_sets"

    scenario_set_id: Mapped[str] = mapped_column(String(128), primary_key=True)

    dataset_id: Mapped[str | None] = mapped_column(String(128), nullable=True)
    dataset_version: Mapped[str | None] = mapped_column(String(128), nullable=True)

    name: Mapped[str | None] = mapped_column(String(255), nullable=True)
    description: Mapped[str | None] = mapped_column(Text, nullable=True)

    scenario_set_uri: Mapped[str | None] = mapped_column(Text, nullable=True)

    scenario_count: Mapped[int] = mapped_column(
        Integer, nullable=False, server_default=text("0")
    )
    tags: Mapped[list[str]] = mapped_column(
        JSONB, nullable=False, server_default=text("'[]'::jsonb")
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


class ScenarioRunRecordModel(Base):
    """Unified run record for scenario-scoped run types.

    Covers: scenario_mining, scenario_readiness.
    Use ``type`` to discriminate.
    """

    __tablename__ = "scenario_run_records"

    run_id: Mapped[str] = mapped_column(String(128), primary_key=True)
    type: Mapped[str] = mapped_column(String(64), nullable=False)
    status: Mapped[str] = mapped_column(String(32), nullable=False)

    scenario_set_id: Mapped[str | None] = mapped_column(String(128), nullable=True)
    scenario_set_uri: Mapped[str | None] = mapped_column(Text, nullable=True)

    dataset_id: Mapped[str | None] = mapped_column(String(128), nullable=True)
    dataset_version: Mapped[str | None] = mapped_column(String(128), nullable=True)
    dataset_manifest_uri: Mapped[str | None] = mapped_column(Text, nullable=True)

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
    report_uri: Mapped[str | None] = mapped_column(Text, nullable=True)

    candidate_count: Mapped[int] = mapped_column(
        Integer, nullable=False, server_default=text("0")
    )
    selected_count: Mapped[int] = mapped_column(
        Integer, nullable=False, server_default=text("0")
    )
    rejected_count: Mapped[int] = mapped_column(
        Integer, nullable=False, server_default=text("0")
    )

    ready_count: Mapped[int] = mapped_column(
        Integer, nullable=False, server_default=text("0")
    )
    blocked_count: Mapped[int] = mapped_column(
        Integer, nullable=False, server_default=text("0")
    )
    warning_count: Mapped[int] = mapped_column(
        Integer, nullable=False, server_default=text("0")
    )
    average_score: Mapped[float | None] = mapped_column(Float, nullable=True)

    summary: Mapped[dict[str, Any]] = mapped_column(
        JSONB, nullable=False, server_default=text("'{}'::jsonb")
    )
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


Index(
    "ix_scenario_sets_dataset",
    ScenarioSetModel.dataset_id,
    ScenarioSetModel.dataset_version,
)

Index(
    "ix_scenario_run_records_type_status",
    ScenarioRunRecordModel.type,
    ScenarioRunRecordModel.status,
)
Index("ix_scenario_run_records_scenario_set_id", ScenarioRunRecordModel.scenario_set_id)
Index(
    "ix_scenario_run_records_dataset",
    ScenarioRunRecordModel.dataset_id,
    ScenarioRunRecordModel.dataset_version,
)
Index("ix_scenario_run_records_job_id", ScenarioRunRecordModel.job_id)
Index("ix_scenario_run_records_pipeline_run_id", ScenarioRunRecordModel.pipeline_run_id)
Index("ix_scenario_run_records_created_at", ScenarioRunRecordModel.created_at)
