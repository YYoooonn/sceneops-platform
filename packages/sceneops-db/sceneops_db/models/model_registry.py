from __future__ import annotations

from datetime import datetime
from typing import Any

from sqlalchemy import DateTime, ForeignKey, Index, String, Text, UniqueConstraint, text
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column, relationship

from sceneops_db.base import Base


class ModelModel(Base):
    __tablename__ = "models"

    model_id: Mapped[str] = mapped_column(String(128), primary_key=True)

    name: Mapped[str | None] = mapped_column(String(255), nullable=True)
    description: Mapped[str | None] = mapped_column(Text, nullable=True)

    task_type: Mapped[str] = mapped_column(String(64), nullable=False)
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

    versions: Mapped[list["ModelVersionModel"]] = relationship(
        back_populates="model",
        cascade="all, delete-orphan",
    )


class ModelVersionModel(Base):
    __tablename__ = "model_versions"

    __table_args__ = (
        UniqueConstraint(
            "model_id",
            "version",
            name="uq_model_versions_model_id_version",
        ),
    )

    id: Mapped[str] = mapped_column(String(256), primary_key=True)

    model_id: Mapped[str] = mapped_column(
        String(128),
        ForeignKey("models.model_id", ondelete="CASCADE"),
        nullable=False,
    )
    version: Mapped[str] = mapped_column(String(128), nullable=False)

    task_type: Mapped[str] = mapped_column(String(64), nullable=False)

    backend: Mapped[str] = mapped_column(String(64), nullable=False)
    status: Mapped[str] = mapped_column(String(32), nullable=False)

    model_uri: Mapped[str | None] = mapped_column(Text, nullable=True)
    endpoint_url: Mapped[str | None] = mapped_column(Text, nullable=True)
    artifact_manifest_uri: Mapped[str | None] = mapped_column(Text, nullable=True)

    runtime: Mapped[dict[str, Any]] = mapped_column(
        JSONB, nullable=False, server_default=text("'{}'::jsonb")
    )
    metadata_: Mapped[dict[str, Any]] = mapped_column(
        "metadata", JSONB, nullable=False, server_default=text("'{}'::jsonb")
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

    model: Mapped[ModelModel] = relationship(back_populates="versions")


Index("ix_model_versions_model_id", ModelVersionModel.model_id)
Index("ix_model_versions_task_type", ModelVersionModel.task_type)
Index("ix_model_versions_status", ModelVersionModel.status)
