from __future__ import annotations

from typing import Any

from sceneops_core.robots.schemas import (
    MissionRecord,
    RobotRecord,
    RobotRunRecord,
    RobotStateRecord,
)

from sceneops_db.models.robots import (
    MissionModel,
    RobotModel,
    RobotRunModel,
    RobotStateModel,
)

from ._utils import enum_to_value, metadata_from_model, values_with_metadata

# ── Robot ────────────────────────────────────────────────────────────────────


def robot_model_to_record(model: RobotModel) -> RobotRecord:
    return RobotRecord(
        robot_id=model.robot_id,
        name=model.name,
        platform=model.platform,
        status=model.status,
        created_at=model.created_at,
        updated_at=model.updated_at,
        metadata=metadata_from_model(model),
    )


def robot_record_to_values(record: RobotRecord) -> dict[str, Any]:
    return values_with_metadata(
        {
            "robot_id": record.robot_id,
            "name": record.name,
            "platform": record.platform,
            "status": enum_to_value(record.status),
            "metadata": record.metadata,
        }
    )


# ── RobotRun ─────────────────────────────────────────────────────────────────


def robot_run_model_to_record(model: RobotRunModel) -> RobotRunRecord:
    return RobotRunRecord(
        run_id=model.run_id,
        robot_id=model.robot_id,
        status=model.status,
        dataset_id=model.dataset_id,
        dataset_version=model.dataset_version,
        raw_log_id=model.raw_log_id,
        rosbag_uri=model.rosbag_uri,
        mcap_uri=model.mcap_uri,
        started_at=model.started_at,
        ended_at=model.ended_at,
        created_at=model.created_at,
        updated_at=model.updated_at,
        metadata=metadata_from_model(model),
    )


def robot_run_record_to_values(record: RobotRunRecord) -> dict[str, Any]:
    return values_with_metadata(
        {
            "run_id": record.run_id,
            "robot_id": record.robot_id,
            "status": enum_to_value(record.status),
            "dataset_id": record.dataset_id,
            "dataset_version": record.dataset_version,
            "raw_log_id": record.raw_log_id,
            "rosbag_uri": record.rosbag_uri,
            "mcap_uri": record.mcap_uri,
            "started_at": record.started_at,
            "ended_at": record.ended_at,
            "metadata": record.metadata,
        }
    )


# ── Mission ──────────────────────────────────────────────────────────────────


def mission_model_to_record(model: MissionModel) -> MissionRecord:
    return MissionRecord(
        mission_id=model.mission_id,
        robot_id=model.robot_id,
        robot_run_id=model.robot_run_id,
        status=model.status,
        started_at=model.started_at,
        ended_at=model.ended_at,
        created_at=model.created_at,
        updated_at=model.updated_at,
        metadata=metadata_from_model(model),
    )


def mission_record_to_values(record: MissionRecord) -> dict[str, Any]:
    return values_with_metadata(
        {
            "mission_id": record.mission_id,
            "robot_id": record.robot_id,
            "robot_run_id": record.robot_run_id,
            "status": enum_to_value(record.status),
            "started_at": record.started_at,
            "ended_at": record.ended_at,
            "metadata": record.metadata,
        }
    )


# ── RobotState ───────────────────────────────────────────────────────────────


def robot_state_model_to_record(model: RobotStateModel) -> RobotStateRecord:
    return RobotStateRecord(
        state_id=model.state_id,
        robot_id=model.robot_id,
        robot_run_id=model.robot_run_id,
        mission_id=model.mission_id,
        scene_id=model.scene_id,
        timestamp_us=model.timestamp_us,
        position=model.position,
        orientation=model.orientation,
        rotation_format=model.rotation_format,
        velocity=model.velocity,
        acceleration=model.acceleration,
        steering=model.steering,
        throttle=model.throttle,
        brake=model.brake,
        battery=model.battery,
        operation_state=model.operation_state,
        created_at=model.created_at,
        metadata=metadata_from_model(model),
    )


def robot_state_record_to_values(record: RobotStateRecord) -> dict[str, Any]:
    return values_with_metadata(
        {
            "state_id": record.state_id,
            "robot_id": record.robot_id,
            "robot_run_id": record.robot_run_id,
            "mission_id": record.mission_id,
            "scene_id": record.scene_id,
            "timestamp_us": record.timestamp_us,
            "position": record.position,
            "orientation": record.orientation,
            "rotation_format": record.rotation_format,
            "velocity": record.velocity,
            "acceleration": record.acceleration,
            "steering": record.steering,
            "throttle": record.throttle,
            "brake": record.brake,
            "battery": record.battery,
            "operation_state": enum_to_value(record.operation_state),
            "metadata": record.metadata,
        }
    )
