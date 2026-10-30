from __future__ import annotations

from datetime import datetime
from sqlalchemy import (
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

from sceneops_db.base import Base


class DatasetModel(Base):
    __tablename__ = "datasets"

    dataset_id: Mapped[str] = mapped_column(String(128), primary_key=True)

    name: Mapped[str | None] = mapped_column(String(255), nullable=True)
    description: Mapped[str | None] = mapped_column(Text, nullable=True)

    default_version: Mapped[str | None] = mapped_column(String(128), nullable=True)

    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=text("now()")
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        server_default=text("now()"),
        onupdate=text("now()"),
    )


class DatasetVersionModel(Base):
    """One committed DatasetVersion (ADR-015 §7, MS-8): inserted with all of
    its ``dataset_version_members`` in one transaction by
    COMPOSE_DATASET_VERSION, never updated (a trigger rejects UPDATE) and
    never deleted while it has members (RESTRICT). ``(dataset_id, version)``
    is bound once to ``composition_checksum``. The summary columns are
    computed from the member revisions' projections at composition."""

    __tablename__ = "dataset_versions"

    __table_args__ = (
        UniqueConstraint(
            "dataset_id",
            "version",
            name="uq_dataset_versions_dataset_id_version",
        ),
        CheckConstraint(
            "composition_checksum ~ '^sha256:[0-9a-f]{64}$'",
            name="ck_dataset_versions_composition_checksum",
        ),
        CheckConstraint(
            "scene_count >= 0 AND keyframe_count >= 0 AND observation_count >= 0 "
            "AND episode_count >= 0 AND scene_count + episode_count > 0",
            name="ck_dataset_versions_counts",
        ),
    )

    id: Mapped[str] = mapped_column(String(256), primary_key=True)

    dataset_id: Mapped[str] = mapped_column(
        String(128),
        ForeignKey("datasets.dataset_id", ondelete="RESTRICT"),
        nullable=False,
        index=True,
    )
    version: Mapped[str] = mapped_column(String(128), nullable=False)

    composition_checksum: Mapped[str] = mapped_column(String(71), nullable=False)
    # How the members were chosen (a RevisionSelector), or NULL when the
    # composing Job was given explicit pins.
    selector: Mapped[dict | None] = mapped_column(JSONB, nullable=True)

    scene_count: Mapped[int] = mapped_column(Integer, nullable=False)
    keyframe_count: Mapped[int] = mapped_column(Integer, nullable=False)
    observation_count: Mapped[int] = mapped_column(Integer, nullable=False)
    observed_channels: Mapped[list[str]] = mapped_column(
        JSONB,
        nullable=False,
        server_default=text("'[]'::jsonb"),
    )
    episode_count: Mapped[int] = mapped_column(Integer, nullable=False)

    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        server_default=text("now()"),
    )


Index("ix_dataset_versions_dataset_id", DatasetVersionModel.dataset_id)


class DatasetVersionMemberModel(Base):
    """One member of a DatasetVersion: a pin of exactly one registered Scene
    or Episode revision (ADR-015 §7). At most one member per unit, so a
    version pins at most one revision of each unit; the foreign key to the
    revision's full pin means a member can only name a registered revision,
    and RESTRICT keeps a pinned revision from being deleted. Rows are never
    updated (a trigger rejects UPDATE)."""

    __tablename__ = "dataset_version_members"
    __table_args__ = (
        ForeignKeyConstraint(
            ["scene_id", "manifest_artifact_id", "manifest_checksum"],
            [
                "scene_revisions.scene_id",
                "scene_revisions.manifest_artifact_id",
                "scene_revisions.manifest_checksum",
            ],
            ondelete="RESTRICT",
            name="fk_dataset_version_members_scene_revision",
        ),
        ForeignKeyConstraint(
            ["episode_id", "manifest_artifact_id", "manifest_checksum"],
            [
                "episode_revisions.episode_id",
                "episode_revisions.manifest_artifact_id",
                "episode_revisions.manifest_checksum",
            ],
            ondelete="RESTRICT",
            name="fk_dataset_version_members_episode_revision",
        ),
        # Exactly one revision reference, the one of the member's domain,
        # naming the member's own unit.
        CheckConstraint(
            "(domain = 'scene' AND scene_id = unit_id AND episode_id IS NULL) OR "
            "(domain = 'episode' AND episode_id = unit_id AND scene_id IS NULL)",
            name="ck_dataset_version_members_revision",
        ),
    )

    dataset_version_id: Mapped[str] = mapped_column(
        String(256),
        ForeignKey("dataset_versions.id", ondelete="RESTRICT"),
        primary_key=True,
    )
    unit_id: Mapped[str] = mapped_column(String(128), primary_key=True)
    domain: Mapped[str] = mapped_column(String(16), nullable=False)

    scene_id: Mapped[str | None] = mapped_column(String(128), nullable=True)
    episode_id: Mapped[str | None] = mapped_column(String(128), nullable=True)
    manifest_artifact_id: Mapped[str] = mapped_column(String(128), nullable=False)
    manifest_checksum: Mapped[str] = mapped_column(String(71), nullable=False)


Index("ix_dataset_version_members_scene_id", DatasetVersionMemberModel.scene_id)
Index("ix_dataset_version_members_episode_id", DatasetVersionMemberModel.episode_id)
