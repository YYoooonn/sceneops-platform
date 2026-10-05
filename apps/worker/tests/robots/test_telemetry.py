"""Tests for RecordingTelemetryReader, the derived robot telemetry projection of a
recording.

Covers:
- no canonical Scene / Episode read remains on the adapter
- non-robot-state topics are ignored
- unrecognized encodings and undecodable CDR schemas are skipped without
  raising
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

import pytest
from mcap.writer import Writer

from sceneops_core.robots.schemas import MissionStatus
from sceneops_worker.robots.telemetry import RecordingTelemetryReader

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


def _make_adapter(bag_path: str) -> tuple[RecordingTelemetryReader, None]:
    return RecordingTelemetryReader(recording_path=bag_path), None


class TestTelemetryProjectionOnly:
    def test_no_canonical_episode_or_scene_read_remains(self) -> None:
        """The adapter is the derived telemetry projection; canonical Scenes
        and Episodes come only from the recording builders."""
        assert not hasattr(RecordingTelemetryReader, "build_raw_log")
        assert not hasattr(RecordingTelemetryReader, "extract_episode_source")
        source = Path(
            __import__("sceneops_worker.robots.telemetry", fromlist=["x"]).__file__
        ).read_text()
        assert "CAM_FRONT" not in source and "LIDAR_TOP" not in source

    def test_unknown_topics_and_encodings_are_ignored(self, tmp_path) -> None:
        bag_path = str(tmp_path / "run.mcap")
        _write_mcap(
            bag_path,
            [
                ("/some/unrelated/topic", 1_000_000_000, {"foo": "bar"}, "json"),
                ("/vehicle/odom", 1_100_000_000, {}, "protobuf"),
            ],
        )
        adapter, _ = _make_adapter(bag_path)
        assert adapter.extract_robot_states(robot_id="robot-1") == []


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
