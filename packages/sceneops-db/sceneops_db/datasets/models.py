from __future__ import annotations

from datetime import datetime
from typing import Any

from sqlalchemy import (
    Boolean,
    DateTime,
    Float,
    ForeignKey,
    Integer,
    String,
    Text,
    UniqueConstraint,
    text,
)
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column, relationship

from sceneops_db.base import Base


class DatasetModel(Base):
    __tablename__ = "datasets"

    id: Mapped[str] = mapped_column(String(128), primary_key=True)

    name: Mapped[str | None] = mapped_column(String(255), nullable=True)
    dataset_type: Mapped[str] = mapped_column(String(64), nullable=False)
    description: Mapped[str | None] = mapped_column(Text, nullable=True)

    metadata_: Mapped[dict[str, Any]] = mapped_column(
        "metadata",
        JSONB,
        nullable=False,
        default=dict,
    )

    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        server_default=text("now()"),
        nullable=False,
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        server_default=text("now()"),
        onupdate=text("now()"),
        nullable=False,
    )

    versions: Mapped[list["DatasetVersionModel"]] = relationship(
        back_populates="dataset",
        cascade="all, delete-orphan",
    )


class DatasetVersionModel(Base):
    __tablename__ = "dataset_versions"

    id: Mapped[str] = mapped_column(String(256), primary_key=True)

    dataset_id: Mapped[str] = mapped_column(
        String(128),
        ForeignKey("datasets.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    version: Mapped[str] = mapped_column(String(128), nullable=False)

    dataset_type: Mapped[str] = mapped_column(String(64), nullable=False)

    # Source / manifest
    manifest_uri: Mapped[str | None] = mapped_column(Text, nullable=True)
    source_uri: Mapped[str | None] = mapped_column(Text, nullable=True)
    raw_data_uri: Mapped[str | None] = mapped_column(Text, nullable=True)

    # Dataset manifest summary
    scene_count: Mapped[int | None] = mapped_column(Integer, nullable=True)
    sample_count: Mapped[int | None] = mapped_column(Integer, nullable=True)
    annotation_count: Mapped[int | None] = mapped_column(Integer, nullable=True)

    # Lifecycle status:
    # registered / ingesting / ingested / validating / ready / failed / archived ...
    status: Mapped[str] = mapped_column(
        String(32),
        nullable=False,
        default="registered",
        index=True,
    )

    # Latest validation cache.
    # Source of truth is dataset_validation_runs.
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
    validation_report_uri: Mapped[str | None] = mapped_column(Text, nullable=True)

    validation_issue_count: Mapped[int | None] = mapped_column(Integer, nullable=True)
    validation_error_count: Mapped[int | None] = mapped_column(Integer, nullable=True)
    validation_warning_count: Mapped[int | None] = mapped_column(Integer, nullable=True)

    missing_scene_count: Mapped[int | None] = mapped_column(Integer, nullable=True)
    missing_sample_count: Mapped[int | None] = mapped_column(Integer, nullable=True)
    missing_channel_count: Mapped[int | None] = mapped_column(Integer, nullable=True)
    missing_artifact_count: Mapped[int | None] = mapped_column(Integer, nullable=True)

    # Latest profile cache.
    # Source of truth is dataset_profile_runs.
    latest_profile_run_id: Mapped[str | None] = mapped_column(
        String(128),
        nullable=True,
        index=True,
    )
    profile_report_uri: Mapped[str | None] = mapped_column(Text, nullable=True)

    profiled_scene_count: Mapped[int | None] = mapped_column(Integer, nullable=True)
    profiled_sample_count: Mapped[int | None] = mapped_column(Integer, nullable=True)

    observed_channel_count: Mapped[int | None] = mapped_column(Integer, nullable=True)
    observed_channels: Mapped[list[str] | None] = mapped_column(
        JSONB,
        nullable=True,
    )

    missing_required_channel_count: Mapped[int | None] = mapped_column(
        Integer,
        nullable=True,
    )
    sensor_coverage_ratio: Mapped[float | None] = mapped_column(
        Float,
        nullable=True,
    )

    empty_annotation_sample_count: Mapped[int | None] = mapped_column(
        Integer,
        nullable=True,
    )
    empty_annotation_sample_ratio: Mapped[float | None] = mapped_column(
        Float,
        nullable=True,
    )

    # Flexible extension area.
    # Do not store primary query fields only here; add columns above when frequently queried.
    metadata_: Mapped[dict[str, Any]] = mapped_column(
        "metadata",
        JSONB,
        nullable=False,
        default=dict,
    )

    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        server_default=text("now()"),
        nullable=False,
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        server_default=text("now()"),
        onupdate=text("now()"),
        nullable=False,
    )

    dataset: Mapped[DatasetModel] = relationship(back_populates="versions")

    __table_args__ = (
        UniqueConstraint(
            "dataset_id",
            "version",
            name="uq_dataset_versions_dataset_id_version",
        ),
    )
