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
    status: Mapped[str] = mapped_column(String(32), nullable=False)

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

    channels: Mapped[list[str]] = mapped_column(
        JSONB,
        nullable=False,
        default=list,
        server_default=text("'[]'::jsonb"),
    )

    source_dataset_id: Mapped[str | None] = mapped_column(String(128), nullable=True)
    source_dataset_version: Mapped[str | None] = mapped_column(
        String(128), nullable=True
    )

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

    latest_distribution_run_id: Mapped[str | None] = mapped_column(
        String(128),
        nullable=True,
        index=True,
    )
    distribution_report_uri: Mapped[str | None] = mapped_column(
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


class DatasetRunRecordModel(Base):
    """Unified run record for dataset-scoped run types.

    Covers: dataset_validation, dataset_profile, dataset_distribution, dataset_export.
    Use ``type`` to discriminate.
    """

    __tablename__ = "dataset_run_records"

    run_id: Mapped[str] = mapped_column(String(128), primary_key=True)
    type: Mapped[str] = mapped_column(String(64), nullable=False)
    status: Mapped[str] = mapped_column(String(32), nullable=False)

    dataset_id: Mapped[str] = mapped_column(String(128), nullable=False)
    dataset_version: Mapped[str] = mapped_column(String(128), nullable=False)

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

    dataset_manifest_uri: Mapped[str | None] = mapped_column(Text, nullable=True)
    report_uri: Mapped[str | None] = mapped_column(Text, nullable=True)
    export_uri: Mapped[str | None] = mapped_column(Text, nullable=True)

    scope: Mapped[str | None] = mapped_column(String(64), nullable=True)
    output_format: Mapped[str | None] = mapped_column(String(64), nullable=True)

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
    "ix_dataset_versions_latest_distribution_run_id",
    DatasetVersionModel.latest_distribution_run_id,
)
Index(
    "ix_dataset_versions_validation_status",
    DatasetVersionModel.validation_status,
)
Index(
    "ix_dataset_versions_should_block_pipeline",
    DatasetVersionModel.should_block_pipeline,
)

Index(
    "ix_dataset_run_records_type_status",
    DatasetRunRecordModel.type,
    DatasetRunRecordModel.status,
)
Index(
    "ix_dataset_run_records_dataset",
    DatasetRunRecordModel.dataset_id,
    DatasetRunRecordModel.dataset_version,
)
Index("ix_dataset_run_records_job_id", DatasetRunRecordModel.job_id)
Index("ix_dataset_run_records_pipeline_run_id", DatasetRunRecordModel.pipeline_run_id)
Index("ix_dataset_run_records_created_at", DatasetRunRecordModel.created_at)
