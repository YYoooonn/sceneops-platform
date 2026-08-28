from __future__ import annotations

import json
from dataclasses import dataclass, field
from typing import Any

from mcap.reader import make_reader

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

    v1 only decodes ``message_encoding="json"`` channels. Real ROS2 bags record
    CDR-encoded messages, which need the message schema (nav_msgs/Odometry etc.)
    to decode — that requires either rclpy or ``mcap-ros2-support``, neither of
    which is wired up yet because no ROS2 recorder exists in this repo to
    produce a real bag to test against (Phase 4's CanReplayNode). JSON-encoded
    channels are what this adapter's own test fixtures use, and are a reasonable
    bridge format until a real CDR-encoded bag exists to decode.
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

    def _read_bag(self) -> _BagContents:
        frames: list[RawSensorFrameManifest] = []
        channels: set[str] = set()
        modalities: set[SensorModality] = set()
        min_ts: int | None = None
        max_ts: int | None = None
        robot_state_payloads: dict[int, dict[str, Any]] = {}

        with open(self._source_root_uri, "rb") as stream:
            reader = make_reader(stream)
            for _schema, channel, message in reader.iter_messages():
                if channel.message_encoding != "json":
                    continue  # CDR decoding not yet supported, see class docstring

                timestamp_us = message.log_time // 1000
                if min_ts is None or timestamp_us < min_ts:
                    min_ts = timestamp_us
                if max_ts is None or timestamp_us > max_ts:
                    max_ts = timestamp_us

                sensor_topic = self._sensor_topics.get(channel.topic)
                if sensor_topic is not None:
                    modality, channel_name = sensor_topic
                    payload = json.loads(message.data)
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
                    payload = json.loads(message.data)
                    robot_state_payloads.setdefault(timestamp_us, {}).update(payload)

        return _BagContents(
            frames=frames,
            channels=channels,
            modalities=modalities,
            min_timestamp_us=min_ts,
            max_timestamp_us=max_ts,
            robot_state_payloads=robot_state_payloads,
        )
