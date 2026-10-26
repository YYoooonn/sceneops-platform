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
    UniqueConstraint,
    text,
)
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from sceneops_core.common.checksums import SHA256_CHECKSUM_PATTERN
from sceneops_db.base import Base


class EpisodeModel(Base):
    """The canonical Episode unit: its id and source binding (ADR-015 §3).
    Inserted once by the Episode registrar and never updated (a trigger
    rejects UPDATE); it carries no DatasetVersion, revision, status, task or
    outcome.

    The source is one column per source kind, exactly one of them set: a
    RobotRun (``robot_run_id``) or a SourceContent (``content_id``). Each has
    its own foreign key and its own unique source-unit key, which a single
    nullable source column could not express."""

    __tablename__ = "episodes"
    __table_args__ = (
        # One source unit has exactly one row (see SceneModel).
        UniqueConstraint("robot_run_id", "unit_key", name="uq_episodes_source_unit"),
        UniqueConstraint(
            "content_id", "unit_key", name="uq_episodes_content_source_unit"
        ),
        CheckConstraint(
            "(source_kind = 'recording' AND robot_run_id IS NOT NULL "
            "AND content_id IS NULL) OR "
            "(source_kind = 'content' AND content_id IS NOT NULL "
            "AND robot_run_id IS NULL)",
            name="ck_episodes_source_kind",
        ),
    )

    episode_id: Mapped[str] = mapped_column(String(128), primary_key=True)

    source_kind: Mapped[str] = mapped_column(String(32), nullable=False)
    robot_run_id: Mapped[str | None] = mapped_column(
        String(128),
        ForeignKey("robot_runs.run_id", ondelete="RESTRICT"),
        nullable=True,
    )
    content_id: Mapped[str | None] = mapped_column(
        String(64),
        ForeignKey("source_contents.content_id", ondelete="RESTRICT"),
        nullable=True,
    )
    unit_key: Mapped[str] = mapped_column(String(256), nullable=False)

    registered_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=text("now()")
    )


class EpisodeRevisionModel(Base):
    """One registered EpisodeManifest revision of an Episode unit, keyed
    ``(episode_id, manifest_checksum)``, projecting that manifest. Inserted
    once by the Episode registrar and never updated or repointed (a trigger
    rejects UPDATE); a unit has no current revision."""

    __tablename__ = "episode_revisions"
    __table_args__ = (
        # MS-7: one unit and one producer fingerprint, one manifest checksum.
        UniqueConstraint(
            "episode_id",
            "producer_fingerprint",
            name="uq_episode_revisions_fingerprint",
        ),
        UniqueConstraint(
            "manifest_artifact_id", name="uq_episode_revisions_manifest_artifact_id"
        ),
        # The full pin, the target of every reference to a revision.
        UniqueConstraint(
            "episode_id",
            "manifest_artifact_id",
            "manifest_checksum",
            name="uq_episode_revisions_pin",
        ),
        CheckConstraint(
            "window_end_timestamp_ns > window_start_timestamp_ns",
            name="ck_episode_revisions_segment_window",
        ),
        CheckConstraint(
            f"manifest_checksum ~ '{SHA256_CHECKSUM_PATTERN}' AND "
            f"producer_fingerprint ~ '{SHA256_CHECKSUM_PATTERN}'",
            name="ck_episode_revisions_checksums",
        ),
        CheckConstraint(
            "observation_count >= 0 AND state_count >= 0 AND action_count >= 0 "
            "AND event_count >= 0 AND transition_count >= 0",
            name="ck_episode_revisions_counts",
        ),
    )

    episode_id: Mapped[str] = mapped_column(
        String(128),
        ForeignKey("episodes.episode_id", ondelete="RESTRICT"),
        primary_key=True,
    )
    manifest_checksum: Mapped[str] = mapped_column(String(71), primary_key=True)
    manifest_artifact_id: Mapped[str] = mapped_column(
        String(128),
        ForeignKey("artifacts.artifact_id", ondelete="RESTRICT"),
        nullable=False,
    )
    producer_fingerprint: Mapped[str] = mapped_column(String(71), nullable=False)

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
    transition_topics: Mapped[list[str]] = mapped_column(
        JSONB, nullable=False, server_default=text("'[]'::jsonb")
    )
    observation_count: Mapped[int] = mapped_column(Integer, nullable=False)
    state_count: Mapped[int] = mapped_column(Integer, nullable=False)
    action_count: Mapped[int] = mapped_column(Integer, nullable=False)
    event_count: Mapped[int] = mapped_column(Integer, nullable=False)
    transition_count: Mapped[int] = mapped_column(Integer, nullable=False)

    registered_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=text("now()")
    )


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
        # A per-episode row pins a registered revision, by its full pin.
        ForeignKeyConstraint(
            ["episode_id", "manifest_artifact_id", "manifest_checksum"],
            [
                "episode_revisions.episode_id",
                "episode_revisions.manifest_artifact_id",
                "episode_revisions.manifest_checksum",
            ],
            ondelete="RESTRICT",
            name="fk_episode_run_records_revision",
        ),
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
    manifest_artifact_id: Mapped[str | None] = mapped_column(String(128), nullable=True)
    manifest_checksum: Mapped[str | None] = mapped_column(String(71), nullable=True)

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
Index("ix_episode_run_records_job_id", EpisodeRunRecordModel.job_id)
Index("ix_episode_run_records_pipeline_run_id", EpisodeRunRecordModel.pipeline_run_id)
