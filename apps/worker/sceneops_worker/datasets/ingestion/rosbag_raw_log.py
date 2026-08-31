from __future__ import annotations

import json
from collections.abc import Callable
from dataclasses import dataclass, field
from typing import Any

from mcap.reader import make_reader
from mcap.records import Channel, Message, Schema
from mcap.well_known import MessageEncoding
from mcap_ros2.decoder import DecoderFactory as Ros2DecoderFactory

from sceneops_core.artifacts.contracts import ArtifactStore
from sceneops_core.observations.schemas import (
    RawLogFrameIndex,
    RawLogManifest,
    RawLogSourceFormat,
    RawLogSourceType,
    RawSensorFrameManifest,
    TimeRange,
)
from sceneops_core.robots.schemas import RobotStateRecord
from sceneops_core.sensors import SensorModality
from sceneops_worker.observations.artifacts import ObservationArtifactStore

# Topic -> (modality, scene channel name) for topics that become scene frames.
_DEFAULT_SENSOR_TOPICS: dict[str, tuple[SensorModality, str]] = {
    "/camera/front/image": (SensorModality.CAMERA, "CAM_FRONT"),
    "/lidar/top/points": (SensorModality.LIDAR, "LIDAR_TOP"),
}

# Robot runtime state topics (docs/robot-data-model.md §3).
_DEFAULT_ROBOT_STATE_TOPICS = {
    "/vehicle/odom",
    "/vehicle/imu",
    "/vehicle/control",
    "/vehicle/status",
}

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
    frames: list[RawSensorFrameManifest]
    channels: set[str]
    modalities: set[SensorModality]
    min_timestamp_us: int | None
    max_timestamp_us: int | None
    robot_state_payloads: dict[int, dict[str, Any]] = field(default_factory=dict)


class RosbagAdapter:
    """Reads an MCAP-recorded rosbag2 file into generic raw log artifacts.

    Implements the same ``RawLogAdapter`` interface as ``NuScenesRawLogMocker``
    (see ``base.RawLogAdapter``) so ``BuildScenesJobHandler`` treats a robot
    rosbag identically to any other raw log source — no changes needed to the
    scene-building pipeline itself (docs/robot-data-model.md §4).

    Decodes two message encodings:

    - ``cdr``: real ROS2 messages, decoded via ``mcap-ros2-support`` using the
      schema text embedded in the MCAP file itself — no ``rclpy``/ROS2
      install needed to read a bag. Verified against real bags recorded with
      ``ros2 bag record --storage mcap`` (see the ``ros2`` Docker sandbox and
      this module's test fixtures). Standard messages with nested nav_msgs/
      sensor_msgs shapes (Odometry, Imu, BatteryState) are flattened into this
      module's flat field names; anything else (including future custom
      messages like ``/vehicle/control``) is assumed already flat.
    - ``json``: a bridge format used by this adapter's own synthetic test
      fixtures, kept because it requires no ROS2 tooling at all to produce.

    Not yet implemented: binary sensor payloads (``sensor_msgs/Image``,
    ``PointCloud2``) aren't written out to files — a CDR-decoded camera/lidar
    frame currently gets an empty ``uri`` and its raw decoded structure in
    ``metadata`` only. Writing those to ArtifactStore is follow-up work once a
    real sensor-publishing node exists.
    """

    def __init__(
        self,
        *,
        source_store: ArtifactStore,
        source_root_uri: str,
        observation_store: ObservationArtifactStore,
        sensor_topics: dict[str, tuple[SensorModality, str]] | None = None,
        robot_state_topics: set[str] | None = None,
    ) -> None:
        self._source_store = source_store
        self._source_root_uri = source_root_uri
        self._observation_store = observation_store
        self._sensor_topics = sensor_topics or _DEFAULT_SENSOR_TOPICS
        self._robot_state_topics = robot_state_topics or _DEFAULT_ROBOT_STATE_TOPICS
        self._ros2_decoder_factory = Ros2DecoderFactory()

    async def build_raw_log(
        self,
        *,
        dataset_id: str,
        dataset_version: str,
        raw_log_id: str,
        version_root_uri: str,
        params: dict,
    ) -> tuple[RawLogManifest, RawLogFrameIndex, str, str]:
        bag = self._read_bag()

        frame_index_uri = self._observation_store.raw_frame_index_uri(version_root_uri)
        frame_index = RawLogFrameIndex(
            raw_log_id=raw_log_id,
            dataset_id=dataset_id,
            dataset_version=dataset_version,
            frames=bag.frames,
        )

        manifest = RawLogManifest(
            raw_log_id=raw_log_id,
            dataset_id=dataset_id,
            dataset_version=dataset_version,
            dataset_type="rosbag",
            source_format=RawLogSourceFormat.ROSBAG,
            source_type=RawLogSourceType.REAL_ROBOT_LOG,
            root_uri=self._source_root_uri,
            channels=sorted(bag.channels),
            modalities=sorted(m.value for m in bag.modalities),
            frame_count=len(bag.frames),
            sequence_count=1,
            time_range=(
                TimeRange(
                    start_timestamp_us=bag.min_timestamp_us,
                    end_timestamp_us=bag.max_timestamp_us,
                )
                if bag.min_timestamp_us is not None and bag.max_timestamp_us is not None
                else None
            ),
            frame_index_uri=frame_index_uri,
        )

        manifest_uri = self._observation_store.raw_log_manifest_uri(version_root_uri)
        await self._observation_store.save_raw_log_manifest(
            uri=manifest_uri, manifest=manifest
        )
        await self._observation_store.save_raw_frame_index(
            uri=frame_index_uri, frame_index=frame_index
        )

        return manifest, frame_index, manifest_uri, frame_index_uri

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
        than adapters (e.g. IngestScenesJobHandler, BuildScenesJobHandler).
        """
        bag = self._read_bag()

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
            return _decoded_message_to_dict(decoder(message.data))
        return None  # unrecognized encoding (protobuf, flatbuffer, ...)

    def _read_bag(self) -> _BagContents:
        frames: list[RawSensorFrameManifest] = []
        channels: set[str] = set()
        modalities: set[SensorModality] = set()
        min_ts: int | None = None
        max_ts: int | None = None
        robot_state_payloads: dict[int, dict[str, Any]] = {}

        with open(self._source_root_uri, "rb") as stream:
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

                sensor_topic = self._sensor_topics.get(channel.topic)
                if sensor_topic is not None:
                    modality, channel_name = sensor_topic
                    frames.append(
                        RawSensorFrameManifest(
                            frame_id=f"{channel.topic}-{message.sequence}",
                            timestamp_us=timestamp_us,
                            channel=channel_name,
                            modality=modality,
                            uri=payload.get("uri", ""),
                            metadata={"topic": channel.topic, **payload},
                        )
                    )
                    channels.add(channel_name)
                    modalities.add(modality)
                    continue

                if channel.topic in self._robot_state_topics:
                    schema_name = schema.name if schema is not None else None
                    flattener = _ROS2_ROBOT_STATE_FLATTENERS.get(schema_name or "")
                    flat_payload = flattener(payload) if flattener else payload
                    robot_state_payloads.setdefault(timestamp_us, {}).update(
                        flat_payload
                    )

        return _BagContents(
            frames=frames,
            channels=channels,
            modalities=modalities,
            min_timestamp_us=min_ts,
            max_timestamp_us=max_ts,
            robot_state_payloads=robot_state_payloads,
        )
