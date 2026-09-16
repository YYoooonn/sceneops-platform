from __future__ import annotations

from datetime import datetime
from typing import Any

from sqlalchemy import DateTime, Float, Index, Integer, String, Text, text
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from sceneops_db.base import Base


class EpisodeModel(Base):
    __tablename__ = "episodes"

    episode_id: Mapped[str] = mapped_column(String(128), primary_key=True)

    dataset_id: Mapped[str | None] = mapped_column(String(128), nullable=True)
    dataset_version: Mapped[str | None] = mapped_column(String(128), nullable=True)

    # Lineage into the raw-log / robot domains. Plain indexed columns, not
    # foreign keys — same cross-domain-reference convention as
    # RobotStateModel.scene_id (the referenced row may live in a table this
    # domain doesn't own, or may not exist yet at write time).
    raw_log_id: Mapped[str | None] = mapped_column(String(128), nullable=True)
    robot_id: Mapped[str | None] = mapped_column(String(128), nullable=True)
    robot_run_id: Mapped[str | None] = mapped_column(String(128), nullable=True)
    mission_id: Mapped[str | None] = mapped_column(String(128), nullable=True)

    status: Mapped[str] = mapped_column(String(32), nullable=False)

    task: Mapped[str | None] = mapped_column(String(255), nullable=True)
    outcome: Mapped[str] = mapped_column(
        String(32), nullable=False, server_default=text("'unknown'")
    )

    episode_manifest_uri: Mapped[str | None] = mapped_column(Text, nullable=True)

    observation_channels: Mapped[list[str]] = mapped_column(
        JSONB, nullable=False, server_default=text("'[]'::jsonb")
    )
    action_channels: Mapped[list[str]] = mapped_column(
        JSONB, nullable=False, server_default=text("'[]'::jsonb")
    )
    control_frequency_hz: Mapped[float | None] = mapped_column(Float, nullable=True)

    frame_count: Mapped[int] = mapped_column(
        Integer, nullable=False, server_default=text("0")
    )

    started_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    ended_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
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

    metadata_: Mapped[dict[str, Any]] = mapped_column(
        "metadata", JSONB, nullable=False, server_default=text("'{}'::jsonb")
    )


Index("ix_episodes_dataset", EpisodeModel.dataset_id, EpisodeModel.dataset_version)
Index("ix_episodes_raw_log_id", EpisodeModel.raw_log_id)
Index("ix_episodes_robot_id", EpisodeModel.robot_id)
Index("ix_episodes_robot_run_id", EpisodeModel.robot_run_id)
Index("ix_episodes_mission_id", EpisodeModel.mission_id)
Index("ix_episodes_status", EpisodeModel.status)
