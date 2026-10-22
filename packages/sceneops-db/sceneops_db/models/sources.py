from __future__ import annotations

from datetime import datetime

from sqlalchemy import (
    BigInteger,
    CheckConstraint,
    DateTime,
    ForeignKey,
    ForeignKeyConstraint,
    Integer,
    String,
    UniqueConstraint,
    text,
)
from sqlalchemy.orm import Mapped, mapped_column

from sceneops_db.base import Base

_SHA256 = "'^sha256:[0-9a-f]{64}$'"


class SourceContentModel(Base):
    """One set of preserved native bytes (ADR-015 §5). Inserted once by
    REGISTER_SOURCE_SNAPSHOT together with its first snapshot, never updated
    (trigger); a deferred trigger refuses a commit that leaves a content
    without a snapshot (MS-1)."""

    __tablename__ = "source_contents"
    __table_args__ = (
        CheckConstraint(
            "content_id ~ '^content-[0-9a-f]{32}$'", name="ck_source_contents_id"
        ),
        CheckConstraint(
            f"content_checksum ~ {_SHA256}", name="ck_source_contents_checksum"
        ),
        CheckConstraint(
            "file_count > 0 AND total_bytes >= 0", name="ck_source_contents_counts"
        ),
        # Target of the snapshots' (content_id, format) foreign key, so a
        # snapshot can only declare the format the content was registered as.
        UniqueConstraint("content_id", "format", name="uq_source_contents_format"),
    )

    content_id: Mapped[str] = mapped_column(String(64), primary_key=True)
    format: Mapped[str] = mapped_column(String(64), nullable=False)
    content_checksum: Mapped[str] = mapped_column(
        String(71), nullable=False, unique=True
    )
    # RESTRICT: a content must never outlive the inventory that defines it.
    inventory_artifact_id: Mapped[str] = mapped_column(
        String(128),
        ForeignKey("artifacts.artifact_id", ondelete="RESTRICT"),
        nullable=False,
        unique=True,
    )
    file_count: Mapped[int] = mapped_column(Integer, nullable=False)
    total_bytes: Mapped[int] = mapped_column(BigInteger, nullable=False)
    registered_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=text("now()")
    )


class SourceSnapshotModel(Base):
    """One upstream declaration of a SourceContent (ADR-015 §5). Inserted
    once by REGISTER_SOURCE_SNAPSHOT, never updated (trigger)."""

    __tablename__ = "source_snapshots"
    __table_args__ = (
        CheckConstraint(
            "snapshot_id ~ '^snapshot-[0-9a-f]{32}$'", name="ck_source_snapshots_id"
        ),
        CheckConstraint(
            f"selection_checksum ~ {_SHA256} AND descriptor_checksum ~ {_SHA256}",
            name="ck_source_snapshots_checksums",
        ),
        ForeignKeyConstraint(
            ["content_id", "format"],
            ["source_contents.content_id", "source_contents.format"],
            name="fk_source_snapshots_content",
            ondelete="RESTRICT",
        ),
    )

    snapshot_id: Mapped[str] = mapped_column(String(64), primary_key=True)
    content_id: Mapped[str] = mapped_column(String(64), nullable=False, index=True)
    format: Mapped[str] = mapped_column(String(64), nullable=False)
    upstream: Mapped[str] = mapped_column(String(256), nullable=False)
    upstream_revision: Mapped[str] = mapped_column(String(256), nullable=False)
    selection_checksum: Mapped[str] = mapped_column(String(71), nullable=False)
    descriptor_artifact_id: Mapped[str] = mapped_column(
        String(128),
        ForeignKey("artifacts.artifact_id", ondelete="RESTRICT"),
        nullable=False,
        unique=True,
    )
    descriptor_checksum: Mapped[str] = mapped_column(String(71), nullable=False)
    registered_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=text("now()")
    )
