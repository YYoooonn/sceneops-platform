"""Tests for RosbagAdapter (MCAP raw log adapter).

Covers:
- build_raw_log: sensor topics (json-encoded) become RawSensorFrameManifest
  entries with correct channel/modality/timestamp, aggregated into a manifest
- build_raw_log: non-sensor, non-robot-state topics are ignored
- build_raw_log / extract_robot_states: unrecognized encodings and
  undecodable CDR schemas are skipped without raising
- extract_robot_states: robot-state topics are merged by timestamp into
  RobotStateRecord rows (json bridge format, and real CDR nav_msgs/Odometry)
- real-fixture tests (fixtures/rosbag/*.mcap) were recorded with an actual
  `ros2 bag record --storage mcap` inside the ros2 Docker sandbox — not
  hand-crafted — to prove CDR decoding against genuine ROS2 output, not just
  bytes this test suite made up itself.
"""

from __future__ import annotations

import json
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from mcap.writer import Writer

from sceneops_core.robots.schemas import MissionStatus
from sceneops_core.sensors import SensorModality
from sceneops_worker.datasets.ingestion.rosbag_raw_log import RosbagAdapter

_FIXTURES_DIR = Path(__file__).parent.parent / "fixtures" / "rosbag"


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
    async def test_unrecognized_encoding_is_skipped(self, tmp_path) -> None:
        bag_path = str(tmp_path / "run.mcap")
        _write_mcap(
            bag_path,
            [
                ("/camera/front/image", 1_000_000_000, {}, "protobuf"),
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

    @pytest.mark.asyncio
    async def test_cdr_without_schema_is_skipped(self, tmp_path) -> None:
        """A cdr channel with no registered schema can't be decoded (no ros2msg
        text to parse) — decoder_for returns None, message is skipped, not raised."""
        bag_path = str(tmp_path / "run.mcap")
        _write_mcap(
            bag_path,
            [("/camera/front/image", 1_000_000_000, {}, "cdr")],
        )
        adapter, _ = _make_adapter(bag_path)

        manifest, frame_index, _, _ = await adapter.build_raw_log(
            **_BUILD_RAW_LOG_KWARGS
        )

        assert manifest.frame_count == 0
        assert frame_index.frames == []


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


class TestExtractMissions:
    def test_consolidates_start_and_end_into_one_record(self, tmp_path) -> None:
        bag_path = str(tmp_path / "run.mcap")
        _write_mcap(
            bag_path,
            [
                (
                    "/mission/status",
                    1_000_000_000,
                    {"mission_id": "mission-1", "operation_state": "running"},
                    "json",
                ),
                (
                    "/mission/status",
                    1_500_000_000,
                    {"mission_id": "mission-1", "operation_state": "completed"},
                    "json",
                ),
            ],
        )
        adapter, _ = _make_adapter(bag_path)

        missions = adapter.extract_missions(robot_id="robot-1", robot_run_id="run-1")

        assert len(missions) == 1
        mission = missions[0]
        assert mission.mission_id == "mission-1"
        assert mission.status == MissionStatus.COMPLETED
        assert mission.robot_id == "robot-1"
        assert mission.robot_run_id == "run-1"
        assert mission.started_at < mission.ended_at

    def test_non_terminal_status_has_no_ended_at(self, tmp_path) -> None:
        bag_path = str(tmp_path / "run.mcap")
        _write_mcap(
            bag_path,
            [
                (
                    "/mission/status",
                    1_000_000_000,
                    {"mission_id": "mission-1", "operation_state": "running"},
                    "json",
                )
            ],
        )
        adapter, _ = _make_adapter(bag_path)

        mission = adapter.extract_missions(robot_id="robot-1")[0]
        assert mission.status == MissionStatus.RUNNING
        assert mission.ended_at is None

    def test_unrecognized_operation_state_defaults_to_pending(self, tmp_path) -> None:
        bag_path = str(tmp_path / "run.mcap")
        _write_mcap(
            bag_path,
            [
                (
                    "/mission/status",
                    1_000_000_000,
                    {
                        "mission_id": "mission-1",
                        "operation_state": "some_unknown_value",
                    },
                    "json",
                )
            ],
        )
        adapter, _ = _make_adapter(bag_path)

        mission = adapter.extract_missions(robot_id="robot-1")[0]
        assert mission.status == MissionStatus.PENDING

    def test_no_mission_topics_returns_empty(self, tmp_path) -> None:
        bag_path = str(tmp_path / "run.mcap")
        _write_mcap(
            bag_path,
            [("/camera/front/image", 1_000_000_000, {"uri": "x"}, "json")],
        )
        adapter, _ = _make_adapter(bag_path)

        assert adapter.extract_missions(robot_id="robot-1") == []

    def test_mission_status_does_not_leak_into_robot_states(self, tmp_path) -> None:
        """/mission/status must stay out of extract_robot_states() — its
        operation_state values are MissionStatus, not RobotOperationState,
        and would fail RobotStateRecord validation if merged in."""
        bag_path = str(tmp_path / "run.mcap")
        _write_mcap(
            bag_path,
            [
                (
                    "/mission/status",
                    1_000_000_000,
                    {"mission_id": "mission-1", "operation_state": "completed"},
                    "json",
                )
            ],
        )
        adapter, _ = _make_adapter(bag_path)

        assert adapter.extract_robot_states(robot_id="robot-1") == []


class TestExtractEpisodeSource:
    """Episode-domain extraction (SceneOps V2 Request 12) — a single-read
    counterpart to build_raw_log() that never touches ObservationArtifactStore
    or produces RawLogManifest/RawLogFrameIndex."""

    def test_no_observation_store_required(self, tmp_path) -> None:
        bag_path = str(tmp_path / "run.mcap")
        _write_mcap(
            bag_path,
            [("/vehicle/odom", 1_000_000_000, {"position": [1.0, 0.0, 0.0]}, "json")],
        )
        # Deliberately no observation_store — proves the Episode path has no
        # Scene-domain collaborator dependency.
        adapter = RosbagAdapter(source_store=MagicMock(), source_root_uri=bag_path)

        source = adapter.extract_episode_source(robot_id="robot-1")

        assert len(source.robot_states) == 1
        assert source.robot_states[0].position == [1.0, 0.0, 0.0]

    @pytest.mark.asyncio
    async def test_build_raw_log_without_observation_store_raises(
        self, tmp_path
    ) -> None:
        bag_path = str(tmp_path / "run.mcap")
        _write_mcap(bag_path, [("/camera/front/image", 1_000_000_000, {}, "json")])
        adapter = RosbagAdapter(source_store=MagicMock(), source_root_uri=bag_path)

        with pytest.raises(ValueError, match="observation_store"):
            await adapter.build_raw_log(**_BUILD_RAW_LOG_KWARGS)

    def test_combines_frames_states_and_missions_from_one_bag(self, tmp_path) -> None:
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
                    "/vehicle/odom",
                    1_000_000_000,
                    {"position": [1.0, 2.0, 0.0]},
                    "json",
                ),
                (
                    "/mission/status",
                    1_000_000_000,
                    {"mission_id": "mission-1", "operation_state": "running"},
                    "json",
                ),
                (
                    "/mission/status",
                    1_500_000_000,
                    {"mission_id": "mission-1", "operation_state": "completed"},
                    "json",
                ),
            ],
        )
        adapter, _ = _make_adapter(bag_path)

        source = adapter.extract_episode_source(
            robot_id="robot-1", robot_run_id="run-1"
        )

        assert len(source.frames) == 1
        assert source.frames[0].channel == "CAM_FRONT"
        assert len(source.robot_states) == 1
        assert source.robot_states[0].position == [1.0, 2.0, 0.0]
        assert len(source.missions) == 1
        assert source.missions[0].mission_id == "mission-1"
        assert source.missions[0].status == MissionStatus.COMPLETED

    def test_reads_bag_exactly_once(self, tmp_path) -> None:
        bag_path = str(tmp_path / "run.mcap")
        _write_mcap(
            bag_path,
            [("/vehicle/odom", 1_000_000_000, {"position": [1.0, 0.0, 0.0]}, "json")],
        )
        adapter, _ = _make_adapter(bag_path)

        with patch.object(RosbagAdapter, "_read_bag", wraps=adapter._read_bag) as spy:
            adapter.extract_episode_source(robot_id="robot-1")

        assert spy.call_count == 1

    def test_real_can_replay_bag_matches_individual_extractors(self) -> None:
        """Cross-check against the separately-verified extract_robot_states()/
        extract_missions() counts in TestRealCdrFixtures below."""
        bag_path = str(_FIXTURES_DIR / "can_replay_scene_0061.mcap")
        adapter, _ = _make_adapter(bag_path)

        source = adapter.extract_episode_source(
            robot_id="robot-nuscenes-01", robot_run_id="run-scene-0061"
        )

        assert len(source.robot_states) == len(
            adapter.extract_robot_states(
                robot_id="robot-nuscenes-01", robot_run_id="run-scene-0061"
            )
        )
        assert len(source.missions) == 1
        assert source.missions[0].mission_id == "mission-scene-0061"
        assert source.frames == []  # CAN replay publishes no camera/lidar topics


class TestRealCdrFixtures:
    """Exercises decoding against bags recorded by an actual `ros2 bag record
    --storage mcap`, not bytes fabricated by this test suite."""

    def test_real_odometry_bag_flattens_into_robot_state(self) -> None:
        adapter, _ = _make_adapter(str(_FIXTURES_DIR / "nav_msgs_odometry.mcap"))

        states = adapter.extract_robot_states(robot_id="robot-1", robot_run_id="run-1")

        assert len(states) == 3
        # Published with x = 1.0, 2.0, 3.0; y/z, orientation, and velocity held
        # constant — see the record used to generate this fixture.
        assert [s.position[0] for s in states] == [1.0, 2.0, 3.0]
        for state in states:
            assert state.position[1:] == [2.0, 0.0]
            assert state.orientation == [0.0, 0.0, 0.0, 1.0]
            assert state.velocity == [0.5, 0.0, 0.0]
            assert state.robot_id == "robot-1"
            assert state.robot_run_id == "run-1"
        # timestamps strictly increasing and already in microseconds
        assert states[0].timestamp_us < states[1].timestamp_us < states[2].timestamp_us

    @pytest.mark.asyncio
    async def test_real_string_bag_decodes_without_crashing_but_is_ignored(
        self,
    ) -> None:
        """/chatter (std_msgs/String) isn't a sensor or robot-state topic — it
        should decode cleanly (proving generic CDR decoding doesn't blow up on
        an arbitrary message type) but contribute no frames or states."""
        adapter, _ = _make_adapter(str(_FIXTURES_DIR / "std_msgs_string.mcap"))

        manifest, frame_index, _, _ = await adapter.build_raw_log(
            **_BUILD_RAW_LOG_KWARGS
        )

        assert manifest.frame_count == 0
        assert frame_index.frames == []
        assert adapter.extract_robot_states(robot_id="robot-1") == []

    def test_real_can_replay_bag_closes_the_full_phase4_loop(self) -> None:
        """This fixture is the actual output of the full roadmap Phase 4 chain:
        nuScenes CAN bus data -> ros2/nodes/can_replay_node.py (real rclpy
        publisher, run inside the ros2 Docker sandbox) -> `ros2 bag record
        --storage mcap` on /vehicle/odom, /vehicle/imu, /vehicle/status,
        /vehicle/control, /mission/status -> this adapter. Counts match the
        scene-0061 CAN bus data exactly (938 pose, 1899 ms_imu, 38
        vehicle_monitor messages)."""
        adapter, _ = _make_adapter(str(_FIXTURES_DIR / "can_replay_scene_0061.mcap"))

        states = adapter.extract_robot_states(
            robot_id="robot-nuscenes-01", robot_run_id="run-scene-0061"
        )

        has = lambda field: sum(1 for s in states if getattr(s, field) is not None)  # noqa: E731
        assert has("position") == 938
        assert has("velocity") == 938
        assert has("orientation") > 0  # contributed by both pose and ms_imu
        assert has("acceleration") == 1899
        assert has("battery") == 38
        assert has("steering") == 38
        assert has("throttle") == 38
        assert has("brake") == 38
        # /mission/status is deliberately excluded — see
        # _DEFAULT_ROBOT_STATE_TOPICS's comment on the operation_state
        # semantic mismatch with MissionStatus.
        assert has("operation_state") == 0

        control_state = next(s for s in states if s.steering is not None)
        assert control_state.robot_id == "robot-nuscenes-01"
        assert control_state.robot_run_id == "run-scene-0061"

        # /mission/status was excluded from robot states above, but it's a
        # real topic in this bag (start + end) — extract_missions() is its
        # actual consumer.
        missions = adapter.extract_missions(
            robot_id="robot-nuscenes-01", robot_run_id="run-scene-0061"
        )
        assert len(missions) == 1
        assert missions[0].mission_id == "mission-scene-0061"
        assert missions[0].status == MissionStatus.COMPLETED
        assert missions[0].started_at < missions[0].ended_at
