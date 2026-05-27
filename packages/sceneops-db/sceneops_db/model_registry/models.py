from __future__ import annotations

from datetime import datetime
from typing import Any

from sqlalchemy import DateTime, ForeignKey, String, Text, UniqueConstraint, text
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column, relationship

from sceneops_db.base import Base


class ModelModel(Base):
    __tablename__ = "models"

    id: Mapped[str] = mapped_column(String(128), primary_key=True)
    name: Mapped[str | None] = mapped_column(String(255))
    description: Mapped[str | None] = mapped_column(Text)

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

    versions: Mapped[list["ModelVersionModel"]] = relationship(
        back_populates="model",
        cascade="all, delete-orphan",
    )


class ModelVersionModel(Base):
    __tablename__ = "model_versions"

    id: Mapped[str] = mapped_column(String(256), primary_key=True)

    model_id: Mapped[str] = mapped_column(
        String(128),
        ForeignKey("models.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    version: Mapped[str] = mapped_column(String(128), nullable=False)

    backend: Mapped[str] = mapped_column(String(64), nullable=False)
    status: Mapped[str] = mapped_column(String(32), nullable=False)

    model_uri: Mapped[str | None] = mapped_column(Text)
    endpoint_url: Mapped[str | None] = mapped_column(Text)

    runtime: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False, default=dict)
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

    model: Mapped[ModelModel] = relationship(back_populates="versions")

    __table_args__ = (
        UniqueConstraint(
            "model_id",
            "version",
            name="uq_model_versions_model_id_version",
        ),
    )
