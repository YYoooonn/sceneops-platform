from __future__ import annotations

from datetime import datetime
from typing import Any

from sqlalchemy import DateTime, Index, Integer, String, Text, text
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from sceneops_db.base import Base


class SceneModel(Base):
    __tablename__ = "scenes"

    scene_id: Mapped[str] = mapped_column(String(128), primary_key=True)

    dataset_id: Mapped[str | None] = mapped_column(String(128), nullable=True)
    dataset_version: Mapped[str | None] = mapped_column(String(128), nullable=True)

    raw_log_id: Mapped[str | None] = mapped_column(String(128), nullable=True)
    segment_id: Mapped[str | None] = mapped_column(String(128), nullable=True)

    status: Mapped[str] = mapped_column(String(32), nullable=False)
    origin_type: Mapped[str] = mapped_column(String(64), nullable=False)
    generation_method: Mapped[str] = mapped_column(String(64), nullable=False)
    parent_scene_id: Mapped[str | None] = mapped_column(
        String(128),
        nullable=True,
        index=True,
    )

    lineage: Mapped[dict[str, Any] | None] = mapped_column(JSONB, nullable=True)
    generation: Mapped[dict[str, Any] | None] = mapped_column(JSONB, nullable=True)
    scene_manifest_uri: Mapped[str | None] = mapped_column(Text, nullable=True)
    world_state_manifest_uri: Mapped[str | None] = mapped_column(Text, nullable=True)
    artifact_root_uri: Mapped[str | None] = mapped_column(Text, nullable=True)

    sample_count: Mapped[int] = mapped_column(
        Integer, nullable=False, server_default=text("0")
    )
    frame_count: Mapped[int] = mapped_column(
        Integer, nullable=False, server_default=text("0")
    )
    channels: Mapped[list[str]] = mapped_column(
        JSONB, nullable=False, server_default=text("'[]'::jsonb")
    )

    started_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    ended_at: Mapped[datetime | None] = mapped_column(
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


class SceneRunRecordModel(Base):
    """Unified run record for scene-scoped run types.

    Covers: scene_validation, scene_profile, scene_comparison,
    scene_reconstruction, scene_package_export.
    Use ``type`` to discriminate.
    """

    __tablename__ = "scene_run_records"

    run_id: Mapped[str] = mapped_column(String(128), primary_key=True)
    type: Mapped[str] = mapped_column(String(64), nullable=False)
    status: Mapped[str] = mapped_column(String(32), nullable=False)

    scene_id: Mapped[str | None] = mapped_column(String(128), nullable=True)
    scene_manifest_uri: Mapped[str | None] = mapped_column(Text, nullable=True)

    dataset_id: Mapped[str | None] = mapped_column(String(128), nullable=True)
    dataset_version: Mapped[str | None] = mapped_column(String(128), nullable=True)

    source_scene_id: Mapped[str | None] = mapped_column(String(128), nullable=True)
    target_scene_id: Mapped[str | None] = mapped_column(String(128), nullable=True)

    raw_log_id: Mapped[str | None] = mapped_column(String(128), nullable=True)
    raw_log_manifest_uri: Mapped[str | None] = mapped_column(Text, nullable=True)
    raw_log_frame_index_uri: Mapped[str | None] = mapped_column(Text, nullable=True)

    pipeline_run_id: Mapped[str | None] = mapped_column(String(128), nullable=True)
    pipeline_step_run_id: Mapped[str | None] = mapped_column(String(128), nullable=True)
    job_id: Mapped[str | None] = mapped_column(String(128), nullable=True)

    params: Mapped[dict[str, Any]] = mapped_column(
        JSONB, nullable=False, server_default=text("'{}'::jsonb")
    )
    result: Mapped[dict[str, Any] | None] = mapped_column(JSONB, nullable=True)
    error: Mapped[dict[str, Any] | None] = mapped_column(JSONB, nullable=True)

    artifact_root_uri: Mapped[str | None] = mapped_column(Text, nullable=True)
    manifest_uri: Mapped[str | None] = mapped_column(Text, nullable=True)

    report_uri: Mapped[str | None] = mapped_column(Text, nullable=True)
    package_uri: Mapped[str | None] = mapped_column(Text, nullable=True)
    world_state_manifest_uri: Mapped[str | None] = mapped_column(Text, nullable=True)

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


Index("ix_scenes_dataset", SceneModel.dataset_id, SceneModel.dataset_version)
Index("ix_scenes_raw_log_id", SceneModel.raw_log_id)
Index("ix_scenes_origin_type", SceneModel.origin_type)
Index("ix_scenes_generation_method", SceneModel.generation_method)
Index("ix_scenes_status", SceneModel.status)

Index(
    "ix_scene_run_records_type_status",
    SceneRunRecordModel.type,
    SceneRunRecordModel.status,
)
Index("ix_scene_run_records_scene_id", SceneRunRecordModel.scene_id)
Index(
    "ix_scene_run_records_dataset",
    SceneRunRecordModel.dataset_id,
    SceneRunRecordModel.dataset_version,
)
Index("ix_scene_run_records_source_scene_id", SceneRunRecordModel.source_scene_id)
Index("ix_scene_run_records_target_scene_id", SceneRunRecordModel.target_scene_id)
Index("ix_scene_run_records_raw_log_id", SceneRunRecordModel.raw_log_id)
Index("ix_scene_run_records_job_id", SceneRunRecordModel.job_id)
Index("ix_scene_run_records_pipeline_run_id", SceneRunRecordModel.pipeline_run_id)
Index("ix_scene_run_records_created_at", SceneRunRecordModel.created_at)
