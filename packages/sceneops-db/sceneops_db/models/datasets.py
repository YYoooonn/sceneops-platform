from __future__ import annotations

from datetime import datetime
from typing import Any

from sqlalchemy import (
    DateTime,
    ForeignKey,
    Index,
    Integer,
    String,
    Text,
    UniqueConstraint,
    Boolean,
    text,
)
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column, relationship

from sceneops_db.base import Base


class DatasetModel(Base):
    __tablename__ = "datasets"

    dataset_id: Mapped[str] = mapped_column(String(128), primary_key=True)

    name: Mapped[str | None] = mapped_column(String(255), nullable=True)
    description: Mapped[str | None] = mapped_column(Text, nullable=True)

    type: Mapped[str] = mapped_column(String(64), nullable=False)

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

    metadata_: Mapped[dict[str, Any]] = mapped_column(
        "metadata", JSONB, nullable=False, server_default=text("'{}'::jsonb")
    )

    versions: Mapped[list["DatasetVersionModel"]] = relationship(
        back_populates="dataset",
        cascade="all, delete-orphan",
    )


class DatasetVersionModel(Base):
    __tablename__ = "dataset_versions"

    __table_args__ = (
        UniqueConstraint(
            "dataset_id",
            "version",
            name="uq_dataset_versions_dataset_id_version",
        ),
    )

    id: Mapped[str] = mapped_column(String(256), primary_key=True)

    dataset_id: Mapped[str] = mapped_column(
        String(128),
        ForeignKey("datasets.dataset_id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    version: Mapped[str] = mapped_column(String(128), nullable=False)

    status: Mapped[str] = mapped_column(
        String(32),
        nullable=False,
        default="registered",
        server_default=text("'registered'"),
        index=True,
    )
    manifest_uri: Mapped[str | None] = mapped_column(Text, nullable=True)

    scene_count: Mapped[int] = mapped_column(
        Integer,
        nullable=False,
        default=0,
        server_default=text("0"),
    )
    sample_count: Mapped[int] = mapped_column(
        Integer,
        nullable=False,
        default=0,
        server_default=text("0"),
    )
    frame_count: Mapped[int] = mapped_column(
        Integer,
        nullable=False,
        default=0,
        server_default=text("0"),
    )
    episode_count: Mapped[int] = mapped_column(
        Integer,
        nullable=False,
        default=0,
        server_default=text("0"),
    )

    channels: Mapped[list[str]] = mapped_column(
        JSONB,
        nullable=False,
        default=list,
        server_default=text("'[]'::jsonb"),
    )

    required_channels: Mapped[list[str]] = mapped_column(
        JSONB,
        nullable=False,
        default=list,
        server_default=text("'[]'::jsonb"),
    )

    source_dataset_id: Mapped[str | None] = mapped_column(String(128), nullable=True)
    source_dataset_version: Mapped[str | None] = mapped_column(
        String(128), nullable=True
    )

    raw_source_root_uri: Mapped[str | None] = mapped_column(Text, nullable=True)

    latest_validation_run_id: Mapped[str | None] = mapped_column(
        String(128),
        nullable=True,
        index=True,
    )
    validation_status: Mapped[str | None] = mapped_column(
        String(32),
        nullable=True,
        index=True,
    )
    should_block_pipeline: Mapped[bool | None] = mapped_column(
        Boolean,
        nullable=True,
    )
    validation_report_uri: Mapped[str | None] = mapped_column(
        Text,
        nullable=True,
    )

    latest_profile_run_id: Mapped[str | None] = mapped_column(
        String(128),
        nullable=True,
        index=True,
    )
    profile_report_uri: Mapped[str | None] = mapped_column(
        Text,
        nullable=True,
    )

    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        server_default=text("now()"),
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        server_default=text("now()"),
        onupdate=text("now()"),
    )

    metadata_: Mapped[dict[str, Any]] = mapped_column(
        "metadata",
        JSONB,
        nullable=False,
        default=dict,
        server_default=text("'{}'::jsonb"),
    )

    dataset: Mapped[DatasetModel] = relationship(back_populates="versions")


Index("ix_dataset_versions_dataset_id", DatasetVersionModel.dataset_id)
Index("ix_dataset_versions_status", DatasetVersionModel.status)
Index(
    "ix_dataset_versions_latest_validation_run_id",
    DatasetVersionModel.latest_validation_run_id,
)
Index(
    "ix_dataset_versions_latest_profile_run_id",
    DatasetVersionModel.latest_profile_run_id,
)
Index(
    "ix_dataset_versions_validation_status",
    DatasetVersionModel.validation_status,
)
Index(
    "ix_dataset_versions_should_block_pipeline",
    DatasetVersionModel.should_block_pipeline,
)
