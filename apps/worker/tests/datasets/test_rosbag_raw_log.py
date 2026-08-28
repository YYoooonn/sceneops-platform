"""Tests for RosbagAdapter (MCAP raw log adapter).

Covers:
- build_raw_log: sensor topics (json-encoded) become RawSensorFrameManifest
  entries with correct channel/modality/timestamp, aggregated into a manifest
- build_raw_log: non-sensor, non-robot-state topics are ignored
- build_raw_log: non-json channels are skipped (CDR not supported yet)
- extract_robot_states: robot-state topics are merged by timestamp into
  RobotStateRecord rows
"""

from __future__ import annotations

import json
from unittest.mock import AsyncMock, MagicMock

import pytest
from mcap.writer import Writer

from sceneops_core.sensors import SensorModality
from sceneops_worker.datasets.ingestion.rosbag_raw_log import RosbagAdapter


def _write_mcap(path: str, messages: list[tuple[str, int, dict, str]]) -> None:
    """messages: list of (topic, log_time_ns, payload_dict, message_encoding)."""
    with open(path, "wb") as f:
        writer = Writer(f)
        writer.start()
        schema_id = writer.register_schema(
            name="sceneops_json", encoding="jsonschema", data=b"{}"
        )
        channel_ids: dict[str, int] = {}
        for topic, log_time, payload, encoding in messages:
            if topic not in channel_ids:
                channel_ids[topic] = writer.register_channel(
                    topic=topic,
                    message_encoding=encoding,
                    schema_id=schema_id if encoding == "json" else 0,
                )
            writer.add_message(
                channel_ids[topic],
                log_time=log_time,
                data=json.dumps(payload).encode()
                if encoding == "json"
                else b"\x00\x01binarygarbage",
                publish_time=log_time,
            )
        writer.finish()


def _make_adapter(bag_path: str) -> tuple[RosbagAdapter, AsyncMock]:
    obs_store = AsyncMock()
    obs_store.raw_log_manifest_uri = MagicMock(return_value="mem://manifest.json")
    obs_store.raw_frame_index_uri = MagicMock(return_value="mem://frames.json")
    adapter = RosbagAdapter(
        source_store=MagicMock(),
        source_root_uri=bag_path,
        observation_store=obs_store,
    )
    return adapter, obs_store


_BUILD_RAW_LOG_KWARGS = dict(
    dataset_id="robot-fleet",
    dataset_version="v1",
    raw_log_id="rawlog-001",
    version_root_uri="mem://root/",
    params={},
)


class TestBuildRawLogSensorFrames:
    @pytest.mark.asyncio
    async def test_sensor_topics_become_frames(self, tmp_path) -> None:
        bag_path = str(tmp_path / "run.mcap")
        _write_mcap(
            bag_path,
            [
                (
                    "/camera/front/image",
                    1_000_000_000,
                    {"uri": "s3://bucket/img1.jpg"},
                    "json",
                ),
                (
                    "/lidar/top/points",
                    1_050_000_000,
                    {"uri": "s3://bucket/scan1.pcd"},
                    "json",
                ),
            ],
        )
        adapter, obs_store = _make_adapter(bag_path)

        (
            manifest,
            frame_index,
            manifest_uri,
            frame_index_uri,
        ) = await adapter.build_raw_log(**_BUILD_RAW_LOG_KWARGS)

        assert manifest.frame_count == 2
        assert manifest.channels == ["CAM_FRONT", "LIDAR_TOP"]
        assert set(manifest.modalities) == {"camera", "lidar"}
        assert manifest.time_range.start_timestamp_us == 1_000_000
        assert manifest.time_range.end_timestamp_us == 1_050_000

        cam_frame = next(f for f in frame_index.frames if f.channel == "CAM_FRONT")
        assert cam_frame.modality == SensorModality.CAMERA
        assert cam_frame.uri == "s3://bucket/img1.jpg"
        assert cam_frame.timestamp_us == 1_000_000

        obs_store.save_raw_log_manifest.assert_awaited_once()
        obs_store.save_raw_frame_index.assert_awaited_once()
        assert manifest_uri == "mem://manifest.json"
        assert frame_index_uri == "mem://frames.json"

    @pytest.mark.asyncio
    async def test_unknown_topics_are_ignored(self, tmp_path) -> None:
        bag_path = str(tmp_path / "run.mcap")
        _write_mcap(
            bag_path,
            [("/some/unrelated/topic", 1_000_000_000, {"foo": "bar"}, "json")],
        )
        adapter, _ = _make_adapter(bag_path)

        manifest, frame_index, _, _ = await adapter.build_raw_log(
            **_BUILD_RAW_LOG_KWARGS
        )

        assert manifest.frame_count == 0
        assert frame_index.frames == []

    @pytest.mark.asyncio
    async def test_non_json_channels_are_skipped(self, tmp_path) -> None:
        bag_path = str(tmp_path / "run.mcap")
        _write_mcap(
            bag_path,
            [
                ("/camera/front/image", 1_000_000_000, {}, "cdr"),
                (
                    "/lidar/top/points",
                    1_050_000_000,
                    {"uri": "s3://bucket/scan1.pcd"},
                    "json",
                ),
            ],
        )
        adapter, _ = _make_adapter(bag_path)

        manifest, frame_index, _, _ = await adapter.build_raw_log(
            **_BUILD_RAW_LOG_KWARGS
        )

        assert manifest.frame_count == 1
        assert frame_index.frames[0].channel == "LIDAR_TOP"


class TestExtractRobotStates:
    def test_merges_payloads_by_timestamp(self, tmp_path) -> None:
        bag_path = str(tmp_path / "run.mcap")
        _write_mcap(
            bag_path,
            [
                (
                    "/vehicle/odom",
                    1_000_000_000,
                    {"position": [1.0, 2.0, 0.0], "orientation": [0, 0, 0, 1]},
                    "json",
                ),
                (
                    "/vehicle/status",
                    1_000_000_000,
                    {"battery": 87.5, "operation_state": "running"},
                    "json",
                ),
                (
                    "/vehicle/odom",
                    1_100_000_000,
                    {"position": [1.5, 2.0, 0.0]},
                    "json",
                ),
            ],
        )
        adapter, _ = _make_adapter(bag_path)

        states = adapter.extract_robot_states(robot_id="robot-1", robot_run_id="run-1")

        assert len(states) == 2
        first, second = states
        assert first.timestamp_us == 1_000_000
        assert first.position == [1.0, 2.0, 0.0]
        assert first.battery == 87.5
        assert first.operation_state == "running"
        assert first.robot_id == "robot-1"
        assert first.robot_run_id == "run-1"

        assert second.timestamp_us == 1_100_000
        assert second.position == [1.5, 2.0, 0.0]
        assert second.battery is None

    def test_no_robot_state_topics_returns_empty(self, tmp_path) -> None:
        bag_path = str(tmp_path / "run.mcap")
        _write_mcap(
            bag_path,
            [("/camera/front/image", 1_000_000_000, {"uri": "x"}, "json")],
        )
        adapter, _ = _make_adapter(bag_path)

        assert adapter.extract_robot_states(robot_id="robot-1") == []
