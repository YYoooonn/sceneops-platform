from __future__ import annotations

import json
from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any

from mcap.reader import make_reader
from mcap.records import Channel, Message, Schema
from mcap.well_known import MessageEncoding
from mcap_ros2.decoder import DecoderFactory as Ros2DecoderFactory

from sceneops_core.robots.schemas import MissionRecord, MissionStatus, RobotStateRecord

# Robot runtime state topics (docs/workflows/robot-run-and-mcap.md §2).
_DEFAULT_ROBOT_STATE_TOPICS = {
    "/vehicle/odom",
    "/vehicle/imu",
    "/vehicle/control",
    "/vehicle/status",
}

# /mission/status is deliberately NOT in _DEFAULT_ROBOT_STATE_TOPICS — its
# operation_state values ("running"/"completed") are MissionStatus lifecycle
# states, not RobotOperationState values, and mixing the two crashes
# RobotStateRecord validation. It's a separate topic set consumed by
# extract_missions() instead of extract_robot_states().
_DEFAULT_MISSION_TOPICS = {"/mission/status"}

_MISSION_STATUS_BY_OPERATION_STATE: dict[str, MissionStatus] = {
    "running": MissionStatus.RUNNING,
    "completed": MissionStatus.COMPLETED,
    "failed": MissionStatus.FAILED,
    "aborted": MissionStatus.ABORTED,
}

_STD_MSGS_STRING_SCHEMA = "std_msgs/msg/String"

_ROBOT_STATE_FIELDS = (
    "position",
    "orientation",
    "velocity",
    "acceleration",
    "steering",
    "throttle",
    "brake",
    "battery",
    "operation_state",
)


def _decoded_message_to_dict(value: Any) -> Any:
    """Recursively convert a mcap_ros2 dynamic message object into plain dicts/lists.

    Dynamic message classes (``mcap_ros2._dynamic``) expose their ROS2 field
    names via ``__slots__`` — that's the only generic hook available (they
    don't populate ``__dict__``), so this walks ``__slots__`` instead of
    hardcoding a schema-specific reader for every ROS2 message type.
    """
    slots = getattr(type(value), "__slots__", None)
    if slots:
        return {slot: _decoded_message_to_dict(getattr(value, slot)) for slot in slots}
    if isinstance(value, (list, tuple)):
        return [_decoded_message_to_dict(v) for v in value]
    return value


def _flatten_odometry(payload: dict[str, Any]) -> dict[str, Any]:
    position = payload["pose"]["pose"]["position"]
    orientation = payload["pose"]["pose"]["orientation"]
    linear_velocity = payload["twist"]["twist"]["linear"]
    return {
        "position": [position["x"], position["y"], position["z"]],
        "orientation": [
            orientation["x"],
            orientation["y"],
            orientation["z"],
            orientation["w"],
        ],
        "velocity": [linear_velocity["x"], linear_velocity["y"], linear_velocity["z"]],
    }


def _flatten_imu(payload: dict[str, Any]) -> dict[str, Any]:
    orientation = payload["orientation"]
    acceleration = payload["linear_acceleration"]
    return {
        "orientation": [
            orientation["x"],
            orientation["y"],
            orientation["z"],
            orientation["w"],
        ],
        "acceleration": [acceleration["x"], acceleration["y"], acceleration["z"]],
    }


def _flatten_battery_state(payload: dict[str, Any]) -> dict[str, Any]:
    return {"battery": payload.get("percentage")}


def _us_to_datetime(timestamp_us: int) -> datetime:
    """message.log_time is real wall-clock time (rclpy's system clock, not a
    simulated one), so treating it as a genuine UTC timestamp is valid here —
    unlike RobotState.timestamp_us, which stays a raw int throughout since it
    represents "when in the recording", not "when was this row created"."""
    return datetime.fromtimestamp(timestamp_us / 1_000_000, tz=timezone.utc)


# Standard ROS2 message schema name -> translator into this module's flat
# RobotState field shape (roadmap §10.1 calls for using these standard
# messages where possible). Messages not listed here (including SceneOps
# custom messages like a future /vehicle/control type) are assumed to already
# use flat field names matching _ROBOT_STATE_FIELDS and pass through as-is —
# see extract_robot_states().
_ROS2_ROBOT_STATE_FLATTENERS: dict[str, Callable[[dict[str, Any]], dict[str, Any]]] = {
    "nav_msgs/msg/Odometry": _flatten_odometry,
    "sensor_msgs/msg/Imu": _flatten_imu,
    "sensor_msgs/msg/BatteryState": _flatten_battery_state,
}


@dataclass(frozen=True)
class _BagContents:
    min_timestamp_us: int | None
    max_timestamp_us: int | None
    robot_state_payloads: dict[int, dict[str, Any]] = field(default_factory=dict)
    # Chronological (timestamp_us, payload) pairs — unlike robot_state_payloads
    # this isn't merged by timestamp, since multiple distinct status updates
    # for the same mission_id are expected (e.g. start and end).
    mission_updates: list[tuple[int, dict[str, Any]]] = field(default_factory=list)


class RecordingTelemetryReader:
    """Reads an MCAP-recorded rosbag2 file into the derived robot telemetry
    projection (``extract_robot_states``, ``extract_missions``) that
    ``INGEST_ROBOT_STATES`` persists (L1 -> L3, ADR-007 §29.3). It is not a
    canonical ingress: canonical Scenes and Episodes are built by
    ``sceneops_scenes.recording_builder`` and
    ``sceneops_episodes.recording_builder`` from build configuration.

    Decodes two message encodings:

    - ``cdr``: real ROS2 messages, decoded via ``mcap-ros2-support`` using the
      schema text embedded in the MCAP file itself. Standard messages with
      nested nav_msgs/sensor_msgs shapes (Odometry, Imu, BatteryState) are
      flattened into this module's flat field names. Topics with no matching
      standard ROS2 message (``/vehicle/control``, ``/mission/status``) carry
      a flat JSON object in a ``std_msgs/String`` and are unwrapped here.
    - ``json``: the same flat format message-encoded as ``json`` directly,
      used by synthetic test fixtures.
    """

    def __init__(
        self,
        *,
        recording_path: str,
        robot_state_topics: set[str] | None = None,
        mission_topics: set[str] | None = None,
    ) -> None:
        self._recording_path = recording_path
        self._robot_state_topics = robot_state_topics or _DEFAULT_ROBOT_STATE_TOPICS
        self._mission_topics = mission_topics or _DEFAULT_MISSION_TOPICS
        self._ros2_decoder_factory = Ros2DecoderFactory()

    def extract_robot_states(
        self,
        *,
        robot_id: str,
        robot_run_id: str | None = None,
    ) -> list[RobotStateRecord]:
        """Read robot-state topics from the bag into RobotStateRecord rows.

        Pure read — does not persist. A future ingestion job handler is
        responsible for writing these through RobotStateRepository, matching
        this codebase's convention of keeping DB writes in job handlers rather
        than adapters.
        """
        bag = self._read_bag()
        return self._robot_states_from_bag(
            bag, robot_id=robot_id, robot_run_id=robot_run_id
        )

    def extract_missions(
        self,
        *,
        robot_id: str,
        robot_run_id: str | None = None,
    ) -> list[MissionRecord]:
        """Read /mission/status updates into one MissionRecord per mission_id.

        Multiple status updates for the same mission (e.g. start/end) are
        consolidated into a single row: status comes from the
        chronologically last update, started_at/ended_at from the first/last
        message timestamps seen for that mission_id. Pure read — does not
        persist, matching extract_robot_states().
        """
        bag = self._read_bag()
        return self._missions_from_bag(
            bag, robot_id=robot_id, robot_run_id=robot_run_id
        )

    @staticmethod
    def _robot_states_from_bag(
        bag: _BagContents,
        *,
        robot_id: str,
        robot_run_id: str | None,
    ) -> list[RobotStateRecord]:
        records: list[RobotStateRecord] = []
        for timestamp_us in sorted(bag.robot_state_payloads):
            payload = bag.robot_state_payloads[timestamp_us]
            records.append(
                RobotStateRecord(
                    state_id=f"{robot_run_id or robot_id}-{timestamp_us}",
                    robot_id=robot_id,
                    robot_run_id=robot_run_id,
                    timestamp_us=timestamp_us,
                    **{k: payload.get(k) for k in _ROBOT_STATE_FIELDS},
                )
            )
        return records

    @staticmethod
    def _missions_from_bag(
        bag: _BagContents,
        *,
        robot_id: str,
        robot_run_id: str | None,
    ) -> list[MissionRecord]:
        by_mission: dict[str, dict[str, Any]] = {}
        for timestamp_us, payload in bag.mission_updates:
            mission_id = payload.get("mission_id")
            if mission_id is None:
                continue
            entry = by_mission.setdefault(
                mission_id,
                {
                    "started_us": timestamp_us,
                    "latest_us": timestamp_us,
                    "operation_state": None,
                },
            )
            entry["started_us"] = min(entry["started_us"], timestamp_us)
            if timestamp_us >= entry["latest_us"]:
                entry["latest_us"] = timestamp_us
                entry["operation_state"] = payload.get("operation_state")

        records: list[MissionRecord] = []
        for mission_id, entry in by_mission.items():
            status = _MISSION_STATUS_BY_OPERATION_STATE.get(
                entry["operation_state"], MissionStatus.PENDING
            )
            is_terminal = status in (
                MissionStatus.COMPLETED,
                MissionStatus.FAILED,
                MissionStatus.ABORTED,
            )
            records.append(
                MissionRecord(
                    mission_id=mission_id,
                    robot_id=robot_id,
                    robot_run_id=robot_run_id,
                    status=status,
                    started_at=_us_to_datetime(entry["started_us"]),
                    ended_at=_us_to_datetime(entry["latest_us"])
                    if is_terminal
                    else None,
                )
            )
        return records

    def _decode_message(
        self,
        schema: Schema | None,
        channel: Channel,
        message: Message,
    ) -> dict[str, Any] | None:
        """Decode one message's payload into a plain dict, or None if it can't be."""
        if channel.message_encoding == MessageEncoding.JSON:
            return json.loads(message.data)
        if channel.message_encoding == MessageEncoding.CDR:
            decoder = self._ros2_decoder_factory.decoder_for(
                channel.message_encoding, schema
            )
            if decoder is None:
                return None  # not a valid ros2msg schema — can't decode
            decoded = _decoded_message_to_dict(decoder(message.data))
            if schema is not None and schema.name == _STD_MSGS_STRING_SCHEMA:
                # SceneOps JSON-over-String bridge (used by CanReplayNode for
                # topics with no standard ROS2 shape, e.g. /vehicle/control,
                # /mission/status): the real payload is JSON text inside the
                # String's `data` field, not the {"data": ...} wrapper itself.
                try:
                    return json.loads(decoded["data"])
                except (json.JSONDecodeError, TypeError):
                    return decoded  # plain text string, not our bridge convention
            return decoded
        return None  # unrecognized encoding (protobuf, flatbuffer, ...)

    def _read_bag(self) -> _BagContents:
        min_ts: int | None = None
        max_ts: int | None = None
        robot_state_payloads: dict[int, dict[str, Any]] = {}
        mission_updates: list[tuple[int, dict[str, Any]]] = []

        with open(self._recording_path, "rb") as stream:
            reader = make_reader(stream)
            for schema, channel, message in reader.iter_messages():
                payload = self._decode_message(schema, channel, message)
                if payload is None:
                    continue

                timestamp_us = message.log_time // 1000
                if min_ts is None or timestamp_us < min_ts:
                    min_ts = timestamp_us
                if max_ts is None or timestamp_us > max_ts:
                    max_ts = timestamp_us

                if channel.topic in self._robot_state_topics:
                    schema_name = schema.name if schema is not None else None
                    flattener = _ROS2_ROBOT_STATE_FLATTENERS.get(schema_name or "")
                    flat_payload = flattener(payload) if flattener else payload
                    robot_state_payloads.setdefault(timestamp_us, {}).update(
                        flat_payload
                    )
                    continue

                if channel.topic in self._mission_topics:
                    mission_updates.append((timestamp_us, payload))

        return _BagContents(
            min_timestamp_us=min_ts,
            max_timestamp_us=max_ts,
            robot_state_payloads=robot_state_payloads,
            mission_updates=mission_updates,
        )
