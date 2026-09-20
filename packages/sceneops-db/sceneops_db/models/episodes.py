from __future__ import annotations

from datetime import datetime
from typing import Any

from sqlalchemy import DateTime, Float, Index, Integer, String, Text, text
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from sceneops_db.base import Base


class EpisodeModel(Base):
    __tablename__ = "episodes"

    episode_id: Mapped[str] = mapped_column(String(128), primary_key=True)

    dataset_id: Mapped[str | None] = mapped_column(String(128), nullable=True)
    dataset_version: Mapped[str | None] = mapped_column(String(128), nullable=True)

    # Lineage into the raw-log / robot domains. Plain indexed columns, not
    # foreign keys — same cross-domain-reference convention as
    # RobotStateModel.scene_id (the referenced row may live in a table this
    # domain doesn't own, or may not exist yet at write time).
    raw_log_id: Mapped[str | None] = mapped_column(String(128), nullable=True)
    robot_id: Mapped[str | None] = mapped_column(String(128), nullable=True)
    robot_run_id: Mapped[str | None] = mapped_column(String(128), nullable=True)
    mission_id: Mapped[str | None] = mapped_column(String(128), nullable=True)

    status: Mapped[str] = mapped_column(String(32), nullable=False)

    task: Mapped[str | None] = mapped_column(String(255), nullable=True)
    outcome: Mapped[str] = mapped_column(
        String(32), nullable=False, server_default=text("'unknown'")
    )

    episode_manifest_uri: Mapped[str | None] = mapped_column(Text, nullable=True)

    observation_channels: Mapped[list[str]] = mapped_column(
        JSONB, nullable=False, server_default=text("'[]'::jsonb")
    )
    action_channels: Mapped[list[str]] = mapped_column(
        JSONB, nullable=False, server_default=text("'[]'::jsonb")
    )
    control_frequency_hz: Mapped[float | None] = mapped_column(Float, nullable=True)

    frame_count: Mapped[int] = mapped_column(
        Integer, nullable=False, server_default=text("0")
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


Index("ix_episodes_dataset", EpisodeModel.dataset_id, EpisodeModel.dataset_version)
Index("ix_episodes_raw_log_id", EpisodeModel.raw_log_id)
Index("ix_episodes_robot_id", EpisodeModel.robot_id)
Index("ix_episodes_robot_run_id", EpisodeModel.robot_run_id)
Index("ix_episodes_mission_id", EpisodeModel.mission_id)
Index("ix_episodes_status", EpisodeModel.status)


class EpisodeRunRecordModel(Base):
    """Unified run record for episode-scoped run types (SceneOps V2 Request 17).

    Covers: episode_validation, episode_profile. Use ``type`` to discriminate,
    same shape as ``SceneRunRecordModel`` — only a handful of typed columns,
    everything type-specific lives in ``summary`` (JSONB), reconstructed by
    the converter. Append-only: rows are never deleted, only inserted.
    """

    __tablename__ = "episode_run_records"

    run_id: Mapped[str] = mapped_column(String(128), primary_key=True)
    type: Mapped[str] = mapped_column(String(64), nullable=False)
    status: Mapped[str] = mapped_column(String(32), nullable=False)

    episode_id: Mapped[str | None] = mapped_column(String(128), nullable=True)
    episode_manifest_uri: Mapped[str | None] = mapped_column(Text, nullable=True)

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
    "ix_episode_run_records_dataset",
    EpisodeRunRecordModel.dataset_id,
    EpisodeRunRecordModel.dataset_version,
)
Index("ix_episode_run_records_job_id", EpisodeRunRecordModel.job_id)
Index("ix_episode_run_records_pipeline_run_id", EpisodeRunRecordModel.pipeline_run_id)
