from __future__ import annotations

from datetime import datetime
from typing import Any

from sqlalchemy import DateTime, Index, String, text
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from sceneops_db.base import Base


class ExecutionRecordModel(Base):
    __tablename__ = "execution_records"

    execution_id: Mapped[str] = mapped_column(String(128), primary_key=True)

    execution_backend: Mapped[str] = mapped_column(String(64), nullable=False)
    execution_kind: Mapped[str] = mapped_column(String(64), nullable=False)

    resource_id: Mapped[str] = mapped_column(String(128), nullable=False)
    status: Mapped[str] = mapped_column(String(32), nullable=False)

    external_id: Mapped[str | None] = mapped_column(String(255), nullable=True)

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


Index(
    "ix_execution_records_kind_resource",
    ExecutionRecordModel.execution_kind,
    ExecutionRecordModel.resource_id,
)
Index(
    "ix_execution_records_backend_external",
    ExecutionRecordModel.execution_backend,
    ExecutionRecordModel.external_id,
)
Index(
    "ix_execution_records_status_created_at",
    ExecutionRecordModel.status,
    ExecutionRecordModel.created_at,
)
