from __future__ import annotations

from datetime import datetime
from typing import Any

from sqlalchemy import (
    BigInteger,
    CheckConstraint,
    DateTime,
    ForeignKey,
    ForeignKeyConstraint,
    Index,
    Integer,
    String,
    Text,
    text,
)
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from sceneops_db.base import Base


class SceneModel(Base):
    """Canonical Scene membership: one row per registered Scene, projecting
    the manifest revision named by ``manifest_artifact_id``. Written only by
    the Scene registrar; there is no status column (ADR-007 §13.3-§13.4)."""

    __tablename__ = "scenes"
    __table_args__ = (
        # A Scene exists only as a member of an existing DatasetVersion, and
        # membership is never removed implicitly by deleting the version.
        ForeignKeyConstraint(
            ["dataset_id", "dataset_version"],
            ["dataset_versions.dataset_id", "dataset_versions.version"],
            ondelete="RESTRICT",
            name="fk_scenes_dataset_version",
        ),
        CheckConstraint(
            "window_end_timestamp_ns > window_start_timestamp_ns",
            name="ck_scenes_segment_window",
        ),
    )

    scene_id: Mapped[str] = mapped_column(String(128), primary_key=True)

    dataset_id: Mapped[str] = mapped_column(String(128), nullable=False)
    dataset_version: Mapped[str] = mapped_column(String(128), nullable=False)

    # The source projection: the RobotRun the Scene was built from and the
    # producer's unit key within its recording.
    robot_run_id: Mapped[str] = mapped_column(
        String(128),
        ForeignKey("robot_runs.run_id", ondelete="RESTRICT"),
        nullable=False,
    )
    unit_key: Mapped[str] = mapped_column(String(256), nullable=False)

    producer_fingerprint: Mapped[str] = mapped_column(String(71), nullable=False)

    manifest_artifact_id: Mapped[str] = mapped_column(
        String(128),
        ForeignKey("artifacts.artifact_id", ondelete="RESTRICT"),
        nullable=False,
    )
    manifest_checksum: Mapped[str] = mapped_column(String(71), nullable=False)

    # The segment window [start, end) in window_clock, the producer's
    # declared segmentation clock.
    window_clock: Mapped[str] = mapped_column(String(64), nullable=False)
    window_start_timestamp_ns: Mapped[int] = mapped_column(BigInteger, nullable=False)
    window_end_timestamp_ns: Mapped[int] = mapped_column(BigInteger, nullable=False)

    observed_channels: Mapped[list[str]] = mapped_column(
        JSONB, nullable=False, server_default=text("'[]'::jsonb")
    )
    observation_count: Mapped[int] = mapped_column(Integer, nullable=False)
    keyframe_count: Mapped[int] = mapped_column(Integer, nullable=False)
    annotation_count: Mapped[int] = mapped_column(Integer, nullable=False)

    registered_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=text("now()")
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        server_default=text("now()"),
        onupdate=text("now()"),
    )


class SceneRunRecordModel(Base):
    """Unified run record for scene-scoped run types.

    Covers: scene_validation, scene_profile. Use ``type`` to discriminate. A
    per-scene row pins the manifest revision it assessed; a job-level
    aggregate row has neither a scene nor a pin.
    """

    __tablename__ = "scene_run_records"
    __table_args__ = (
        CheckConstraint(
            "(scene_id IS NULL AND manifest_artifact_id IS NULL "
            "AND manifest_checksum IS NULL) OR "
            "(scene_id IS NOT NULL AND manifest_artifact_id IS NOT NULL "
            "AND manifest_checksum IS NOT NULL)",
            name="ck_scene_run_records_revision_pin",
        ),
    )

    run_id: Mapped[str] = mapped_column(String(128), primary_key=True)
    type: Mapped[str] = mapped_column(String(64), nullable=False)
    status: Mapped[str] = mapped_column(String(32), nullable=False)

    scene_id: Mapped[str | None] = mapped_column(String(128), nullable=True)
    manifest_artifact_id: Mapped[str | None] = mapped_column(
        String(128),
        ForeignKey("artifacts.artifact_id", ondelete="RESTRICT"),
        nullable=True,
    )
    manifest_checksum: Mapped[str | None] = mapped_column(String(71), nullable=True)

    dataset_id: Mapped[str | None] = mapped_column(String(128), nullable=True)
    dataset_version: Mapped[str | None] = mapped_column(String(128), nullable=True)

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
Index(
    "ix_scenes_recording_scope",
    SceneModel.dataset_id,
    SceneModel.dataset_version,
    SceneModel.robot_run_id,
)
Index("ix_scenes_manifest_artifact_id", SceneModel.manifest_artifact_id)

Index(
    "ix_scene_run_records_type_status",
    SceneRunRecordModel.type,
    SceneRunRecordModel.status,
)
Index("ix_scene_run_records_scene_id", SceneRunRecordModel.scene_id)
Index(
    "ix_scene_run_records_revision",
    SceneRunRecordModel.scene_id,
    SceneRunRecordModel.manifest_artifact_id,
    SceneRunRecordModel.created_at,
)
Index(
    "ix_scene_run_records_dataset",
    SceneRunRecordModel.dataset_id,
    SceneRunRecordModel.dataset_version,
)
Index("ix_scene_run_records_job_id", SceneRunRecordModel.job_id)
Index("ix_scene_run_records_pipeline_run_id", SceneRunRecordModel.pipeline_run_id)
Index("ix_scene_run_records_created_at", SceneRunRecordModel.created_at)
