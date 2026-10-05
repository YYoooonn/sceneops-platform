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


class EpisodeModel(Base):
    """Canonical Episode membership: one row per registered Episode,
    projecting the manifest revision named by ``manifest_artifact_id``.
    Written only by the Episode registrar; no status, task or outcome
    column (ADR-007 §13.3-§13.4, §31)."""

    __tablename__ = "episodes"
    __table_args__ = (
        ForeignKeyConstraint(
            ["dataset_id", "dataset_version"],
            ["dataset_versions.dataset_id", "dataset_versions.version"],
            ondelete="RESTRICT",
            name="fk_episodes_dataset_version",
        ),
        CheckConstraint(
            "window_end_timestamp_ns > window_start_timestamp_ns",
            name="ck_episodes_segment_window",
        ),
    )

    episode_id: Mapped[str] = mapped_column(String(128), primary_key=True)

    dataset_id: Mapped[str] = mapped_column(String(128), nullable=False)
    dataset_version: Mapped[str] = mapped_column(String(128), nullable=False)

    # The source projection: the RobotRun the Episode was built from and the
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

    # The episode window [start, end) in window_clock, the producer's
    # declared segmentation clock.
    window_clock: Mapped[str] = mapped_column(String(64), nullable=False)
    window_start_timestamp_ns: Mapped[int] = mapped_column(BigInteger, nullable=False)
    window_end_timestamp_ns: Mapped[int] = mapped_column(BigInteger, nullable=False)

    observation_topics: Mapped[list[str]] = mapped_column(
        JSONB, nullable=False, server_default=text("'[]'::jsonb")
    )
    state_topics: Mapped[list[str]] = mapped_column(
        JSONB, nullable=False, server_default=text("'[]'::jsonb")
    )
    action_topics: Mapped[list[str]] = mapped_column(
        JSONB, nullable=False, server_default=text("'[]'::jsonb")
    )
    event_topics: Mapped[list[str]] = mapped_column(
        JSONB, nullable=False, server_default=text("'[]'::jsonb")
    )
    observation_count: Mapped[int] = mapped_column(Integer, nullable=False)
    state_count: Mapped[int] = mapped_column(Integer, nullable=False)
    action_count: Mapped[int] = mapped_column(Integer, nullable=False)
    event_count: Mapped[int] = mapped_column(Integer, nullable=False)

    registered_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=text("now()")
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        server_default=text("now()"),
        onupdate=text("now()"),
    )


Index("ix_episodes_dataset", EpisodeModel.dataset_id, EpisodeModel.dataset_version)
Index(
    "ix_episodes_recording_scope",
    EpisodeModel.dataset_id,
    EpisodeModel.dataset_version,
    EpisodeModel.robot_run_id,
)
Index("ix_episodes_manifest_artifact_id", EpisodeModel.manifest_artifact_id)


class EpisodeRunRecordModel(Base):
    """Unified run record for episode-scoped run types (SceneOps V2 Request 17).

    Covers: episode_validation, episode_profile. Use ``type`` to discriminate,
    same shape as ``SceneRunRecordModel`` — only a handful of typed columns,
    everything type-specific lives in ``summary`` (JSONB), reconstructed by
    the converter. Append-only: rows are never deleted, only inserted. A
    per-episode row pins the manifest revision it assessed; a job-level
    aggregate row has neither an episode nor a pin.
    """

    __tablename__ = "episode_run_records"
    __table_args__ = (
        CheckConstraint(
            "(episode_id IS NULL AND manifest_artifact_id IS NULL "
            "AND manifest_checksum IS NULL) OR "
            "(episode_id IS NOT NULL AND manifest_artifact_id IS NOT NULL "
            "AND manifest_checksum IS NOT NULL)",
            name="ck_episode_run_records_revision_pin",
        ),
    )

    run_id: Mapped[str] = mapped_column(String(128), primary_key=True)
    type: Mapped[str] = mapped_column(String(64), nullable=False)
    status: Mapped[str] = mapped_column(String(32), nullable=False)

    episode_id: Mapped[str | None] = mapped_column(String(128), nullable=True)
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
    "ix_episode_run_records_type_status",
    EpisodeRunRecordModel.type,
    EpisodeRunRecordModel.status,
)
Index("ix_episode_run_records_episode_id", EpisodeRunRecordModel.episode_id)
Index(
    "ix_episode_run_records_revision",
    EpisodeRunRecordModel.episode_id,
    EpisodeRunRecordModel.manifest_artifact_id,
    EpisodeRunRecordModel.created_at,
)
Index(
    "ix_episode_run_records_dataset",
    EpisodeRunRecordModel.dataset_id,
    EpisodeRunRecordModel.dataset_version,
)
Index("ix_episode_run_records_job_id", EpisodeRunRecordModel.job_id)
Index("ix_episode_run_records_pipeline_run_id", EpisodeRunRecordModel.pipeline_run_id)
