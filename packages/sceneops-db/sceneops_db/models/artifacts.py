from __future__ import annotations

from datetime import datetime
from typing import Any

from sqlalchemy import BigInteger, DateTime, Index, String, Text, text
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from sceneops_db.base import Base


class ArtifactModel(Base):
    __tablename__ = "artifacts"

    artifact_id: Mapped[str] = mapped_column(String(128), primary_key=True)

    kind: Mapped[str] = mapped_column(String(64), nullable=False)
    uri: Mapped[str] = mapped_column(Text, nullable=False)

    backend: Mapped[str | None] = mapped_column(String(64), nullable=True)

    owner_type: Mapped[str | None] = mapped_column(String(64), nullable=True)
    owner_id: Mapped[str | None] = mapped_column(String(128), nullable=True)

    dataset_id: Mapped[str | None] = mapped_column(String(128), nullable=True)
    dataset_version: Mapped[str | None] = mapped_column(String(128), nullable=True)
    scene_id: Mapped[str | None] = mapped_column(String(128), nullable=True)
    scenario_set_id: Mapped[str | None] = mapped_column(String(128), nullable=True)
    # Owning resource linkage (one of these will be set)
    run_id: Mapped[str | None] = mapped_column(String(128), nullable=True)
    job_id: Mapped[str | None] = mapped_column(String(128), nullable=True)
    pipeline_run_id: Mapped[str | None] = mapped_column(String(128), nullable=True)

    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=text("now()")
    )

    metadata_: Mapped[dict[str, Any]] = mapped_column(
        "metadata", JSONB, nullable=False, server_default=text("'{}'::jsonb")
    )

    media_type: Mapped[str | None] = mapped_column(String(128), nullable=True)
    size_bytes: Mapped[int | None] = mapped_column(BigInteger, nullable=True)
    checksum: Mapped[str | None] = mapped_column(String(255), nullable=True)


Index("ix_artifacts_kind", ArtifactModel.kind)
Index("ix_artifacts_run_id", ArtifactModel.run_id)
Index("ix_artifacts_job_id", ArtifactModel.job_id)
Index("ix_artifacts_pipeline_run_id", ArtifactModel.pipeline_run_id)
Index(
    "ix_artifacts_dataset",
    ArtifactModel.dataset_id,
    ArtifactModel.dataset_version,
)
Index("ix_artifacts_scene_id", ArtifactModel.scene_id)
Index("ix_artifacts_owner", ArtifactModel.owner_type, ArtifactModel.owner_id)
