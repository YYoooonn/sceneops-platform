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


class SceneModel(Base):
    """The canonical Scene unit: its id and source binding (ADR-015 §3).
    Inserted once by the Scene registrar and never updated (a trigger
    rejects UPDATE); it carries no DatasetVersion, revision or status."""

    __tablename__ = "scenes"
    __table_args__ = (
        # A unit id is a function of (domain, source_kind, source_id,
        # unit_key), so one source unit has exactly one row; a writer that
        # derived another id for it fails here instead of forking identity.
        UniqueConstraint("robot_run_id", "unit_key", name="uq_scenes_source_unit"),
        CheckConstraint("source_kind = 'recording'", name="ck_scenes_source_kind"),
    )

    scene_id: Mapped[str] = mapped_column(String(128), primary_key=True)

    source_kind: Mapped[str] = mapped_column(String(32), nullable=False)
    robot_run_id: Mapped[str] = mapped_column(
        String(128),
        ForeignKey("robot_runs.run_id", ondelete="RESTRICT"),
        nullable=False,
    )
    unit_key: Mapped[str] = mapped_column(String(256), nullable=False)

    registered_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=text("now()")
    )


class SceneRevisionModel(Base):
    """One registered SceneManifest revision of a Scene unit, keyed
    ``(scene_id, manifest_checksum)``, projecting that manifest. Inserted
    once by the Scene registrar and never updated or repointed (a trigger
    rejects UPDATE); a unit has no current revision."""

    __tablename__ = "scene_revisions"
    __table_args__ = (
        # One unit and one producer fingerprint have at most one manifest
        # checksum (MS-7): a second checksum is a non-deterministic producer.
        UniqueConstraint(
            "scene_id", "producer_fingerprint", name="uq_scene_revisions_fingerprint"
        ),
        # The manifest ArtifactRecord id is a function of the revision, so
        # one artifact belongs to exactly one revision.
        UniqueConstraint(
            "manifest_artifact_id", name="uq_scene_revisions_manifest_artifact_id"
        ),
        # The full pin, the target of every reference to a revision: a pin
        # whose artifact id and checksum disagree names no revision.
        UniqueConstraint(
            "scene_id",
            "manifest_artifact_id",
            "manifest_checksum",
            name="uq_scene_revisions_pin",
        ),
        CheckConstraint(
            "window_end_timestamp_ns > window_start_timestamp_ns",
            name="ck_scene_revisions_segment_window",
        ),
        CheckConstraint(
            f"manifest_checksum ~ '{SHA256_CHECKSUM_PATTERN}' AND "
            f"producer_fingerprint ~ '{SHA256_CHECKSUM_PATTERN}'",
            name="ck_scene_revisions_checksums",
        ),
        CheckConstraint(
            "observation_count >= 0 AND keyframe_count >= 0",
            name="ck_scene_revisions_counts",
        ),
    )

    scene_id: Mapped[str] = mapped_column(
        String(128),
        ForeignKey("scenes.scene_id", ondelete="RESTRICT"),
        primary_key=True,
    )
    manifest_checksum: Mapped[str] = mapped_column(String(71), primary_key=True)
    manifest_artifact_id: Mapped[str] = mapped_column(
        String(128),
        ForeignKey("artifacts.artifact_id", ondelete="RESTRICT"),
        nullable=False,
    )
    producer_fingerprint: Mapped[str] = mapped_column(String(71), nullable=False)

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

    registered_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=text("now()")
    )


class SceneRunRecordModel(Base):
    """Unified run record for scene-scoped run types.

    Covers: scene_validation, scene_profile. Use ``type`` to discriminate. A
    per-scene row pins the manifest revision it assessed; a job-level
    aggregate row has neither a scene nor a pin.
    """

    __tablename__ = "scene_run_records"
    __table_args__ = (
        # A per-scene row pins a registered revision, by its full pin.
        ForeignKeyConstraint(
            ["scene_id", "manifest_artifact_id", "manifest_checksum"],
            [
                "scene_revisions.scene_id",
                "scene_revisions.manifest_artifact_id",
                "scene_revisions.manifest_checksum",
            ],
            ondelete="RESTRICT",
            name="fk_scene_run_records_revision",
        ),
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
Index("ix_scene_run_records_job_id", SceneRunRecordModel.job_id)
Index("ix_scene_run_records_pipeline_run_id", SceneRunRecordModel.pipeline_run_id)
Index("ix_scene_run_records_created_at", SceneRunRecordModel.created_at)
