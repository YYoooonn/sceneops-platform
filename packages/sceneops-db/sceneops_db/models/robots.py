from __future__ import annotations

from datetime import datetime
from typing import Any

from sqlalchemy import (
    BigInteger,
    DateTime,
    Float,
    ForeignKey,
    Index,
    String,
    Text,
    text,
)
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column, relationship

from sceneops_db.base import Base


class RobotModel(Base):
    __tablename__ = "robots"

    robot_id: Mapped[str] = mapped_column(String(128), primary_key=True)

    name: Mapped[str | None] = mapped_column(String(255), nullable=True)
    platform: Mapped[str | None] = mapped_column(String(128), nullable=True)

    status: Mapped[str] = mapped_column(
        String(32),
        nullable=False,
        default="registered",
        server_default=text("'registered'"),
        index=True,
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

    runs: Mapped[list["RobotRunModel"]] = relationship(
        back_populates="robot",
        cascade="all, delete-orphan",
    )
    missions: Mapped[list["MissionModel"]] = relationship(
        back_populates="robot",
        cascade="all, delete-orphan",
    )


class RobotRunModel(Base):
    __tablename__ = "robot_runs"

    run_id: Mapped[str] = mapped_column(String(128), primary_key=True)

    robot_id: Mapped[str] = mapped_column(
        String(128),
        ForeignKey("robots.robot_id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )

    status: Mapped[str] = mapped_column(
        String(32),
        nullable=False,
        default="recording",
        server_default=text("'recording'"),
        index=True,
    )

    dataset_id: Mapped[str | None] = mapped_column(
        String(128), nullable=True, index=True
    )
    dataset_version: Mapped[str | None] = mapped_column(String(128), nullable=True)
    raw_log_id: Mapped[str | None] = mapped_column(
        String(128), nullable=True, index=True
    )

    rosbag_uri: Mapped[str | None] = mapped_column(Text, nullable=True)
    mcap_uri: Mapped[str | None] = mapped_column(Text, nullable=True)

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

    robot: Mapped[RobotModel] = relationship(back_populates="runs")
    missions: Mapped[list["MissionModel"]] = relationship(back_populates="robot_run")


class MissionModel(Base):
    __tablename__ = "missions"

    mission_id: Mapped[str] = mapped_column(String(128), primary_key=True)

    robot_id: Mapped[str] = mapped_column(
        String(128),
        ForeignKey("robots.robot_id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    robot_run_id: Mapped[str | None] = mapped_column(
        String(128),
        ForeignKey("robot_runs.run_id", ondelete="SET NULL"),
        nullable=True,
        index=True,
    )

    status: Mapped[str] = mapped_column(
        String(32),
        nullable=False,
        default="pending",
        server_default=text("'pending'"),
        index=True,
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

    robot: Mapped[RobotModel] = relationship(back_populates="missions")
    robot_run: Mapped[RobotRunModel | None] = relationship(back_populates="missions")


class RobotStateModel(Base):
    """High-frequency robot runtime state sample.

    Not linked to ``scenes.scene_id`` with a foreign key — cross-domain refs in
    this codebase (dataset_id, pipeline_run_id, etc.) are kept as indexed plain
    columns rather than FK constraints, since the referenced row may not exist
    yet at write time (e.g. state samples land before scene segmentation runs).
    """

    __tablename__ = "robot_states"

    state_id: Mapped[str] = mapped_column(String(128), primary_key=True)

    robot_id: Mapped[str] = mapped_column(
        String(128),
        ForeignKey("robots.robot_id", ondelete="CASCADE"),
        nullable=False,
    )
    robot_run_id: Mapped[str | None] = mapped_column(
        String(128),
        ForeignKey("robot_runs.run_id", ondelete="CASCADE"),
        nullable=True,
        index=True,
    )
    mission_id: Mapped[str | None] = mapped_column(
        String(128),
        ForeignKey("missions.mission_id", ondelete="SET NULL"),
        nullable=True,
        index=True,
    )
    scene_id: Mapped[str | None] = mapped_column(String(128), nullable=True, index=True)

    timestamp_us: Mapped[int] = mapped_column(BigInteger, nullable=False)

    position: Mapped[list[float] | None] = mapped_column(JSONB, nullable=True)
    orientation: Mapped[list[float] | None] = mapped_column(JSONB, nullable=True)
    rotation_format: Mapped[str] = mapped_column(
        String(32),
        nullable=False,
        default="quaternion_wxyz",
        server_default=text("'quaternion_wxyz'"),
    )

    velocity: Mapped[list[float] | None] = mapped_column(JSONB, nullable=True)
    acceleration: Mapped[list[float] | None] = mapped_column(JSONB, nullable=True)

    steering: Mapped[float | None] = mapped_column(Float, nullable=True)
    throttle: Mapped[float | None] = mapped_column(Float, nullable=True)
    brake: Mapped[float | None] = mapped_column(Float, nullable=True)

    battery: Mapped[float | None] = mapped_column(Float, nullable=True)
    operation_state: Mapped[str | None] = mapped_column(String(32), nullable=True)

    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=text("now()")
    )

    metadata_: Mapped[dict[str, Any]] = mapped_column(
        "metadata", JSONB, nullable=False, server_default=text("'{}'::jsonb")
    )


Index("ix_robot_runs_robot_id", RobotRunModel.robot_id)
Index("ix_robot_runs_status", RobotRunModel.status)
Index("ix_missions_robot_id", MissionModel.robot_id)
Index("ix_missions_status", MissionModel.status)
Index(
    "ix_robot_states_robot_id_timestamp_us",
    RobotStateModel.robot_id,
    RobotStateModel.timestamp_us,
)
Index(
    "ix_robot_states_robot_run_id_timestamp_us",
    RobotStateModel.robot_run_id,
    RobotStateModel.timestamp_us,
)
