from __future__ import annotations

from datetime import datetime
from typing import Any

from sqlalchemy import DateTime, Index, Integer, String, Text, text
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from sceneops_db.base import Base


class LabelRunModel(Base):
    """Unified run record for auto-labeling run types.

    Covers: scene_auto_label, dataset_auto_label.
    Use ``type`` to discriminate.
    """

    __tablename__ = "label_runs"

    run_id: Mapped[str] = mapped_column(String(128), primary_key=True)
    type: Mapped[str] = mapped_column(String(64), nullable=False)
    status: Mapped[str] = mapped_column(String(32), nullable=False)

    # Scene-level fields (scene_auto_label)
    scene_id: Mapped[str | None] = mapped_column(String(128), nullable=True)
    scene_manifest_uri: Mapped[str | None] = mapped_column(Text, nullable=True)
    output_scene_manifest_uri: Mapped[str | None] = mapped_column(Text, nullable=True)
    output_label_uri: Mapped[str | None] = mapped_column(Text, nullable=True)

    # Dataset-level fields (dataset_auto_label)
    dataset_id: Mapped[str | None] = mapped_column(String(128), nullable=True)
    dataset_version: Mapped[str | None] = mapped_column(String(128), nullable=True)
    dataset_manifest_uri: Mapped[str | None] = mapped_column(Text, nullable=True)
    output_dataset_id: Mapped[str | None] = mapped_column(String(128), nullable=True)
    output_dataset_version: Mapped[str | None] = mapped_column(
        String(128), nullable=True
    )
    output_dataset_manifest_uri: Mapped[str | None] = mapped_column(Text, nullable=True)

    labeler_id: Mapped[str | None] = mapped_column(String(128), nullable=True)
    labeler_backend: Mapped[str] = mapped_column(
        String(64), nullable=False, server_default="vlm"
    )

    sample_count: Mapped[int | None] = mapped_column(Integer, nullable=True)
    labeled_sample_count: Mapped[int | None] = mapped_column(Integer, nullable=True)
    labeled_scene_count: Mapped[int] = mapped_column(
        Integer, nullable=False, server_default=text("0")
    )
    annotation_count: Mapped[int] = mapped_column(
        Integer, nullable=False, server_default=text("0")
    )

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


Index("ix_label_runs_type_status", LabelRunModel.type, LabelRunModel.status)
Index("ix_label_runs_scene_id", LabelRunModel.scene_id)
Index("ix_label_runs_dataset", LabelRunModel.dataset_id, LabelRunModel.dataset_version)
Index("ix_label_runs_job_id", LabelRunModel.job_id)
Index("ix_label_runs_pipeline_run_id", LabelRunModel.pipeline_run_id)
Index("ix_label_runs_created_at", LabelRunModel.created_at)
