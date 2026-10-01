"""Unit tests for StreamingBridgeNode.

Runs ONLY inside the ros2 container -- rclpy and the ROS2 message
packages (nav_msgs, sensor_msgs, std_msgs) are apt-installed system
packages there, not importable from the main workspace venv. Invoke via:

    docker compose --profile ros2 run --rm ros2 \\
        python3 -m pytest /workspace/nodes/tests/ -v

(or `make e2e-ros2-streaming`, which runs this suite as its first stage).

No Kafka broker and no live ROS graph (no discovery, no other
participants) -- a FakeProducerBridge stands in for
_AsyncProducerBridge/KafkaTelemetryProducer, and rclpy.init() is only
used to give StreamingBridgeNode a valid node context, never to spin or
exchange messages over the network. CDR is never mocked -- every payload
here is produced by the real rclpy.serialization.serialize_message and
proven round-trippable via the real deserialize_message.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest
import rclpy
from builtin_interfaces.msg import Time
from nav_msgs.msg import Odometry
from rclpy.serialization import deserialize_message
from sensor_msgs.msg import BatteryState, Imu
from std_msgs.msg import String

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from sceneops_core.streaming import (  # noqa: E402
    EnvelopeEncoding,
    RunEventType,
    TelemetryEnvelope,
    parse_run_event,
)

from streaming_bridge_node import (  # noqa: E402
    TOPIC_SPECS,
    SourceTimestampRule,
    StreamingBridgeNode,
)

ROBOT_ID = "robot-unit-test"
ROBOT_RUN_ID = "run-unit-test-1"


class FakeProducerBridge:
    """Stands in for _AsyncProducerBridge -- no Kafka, no asyncio, no
    background thread. Records every envelope handed to it."""

    def __init__(self) -> None:
        self.published: list[TelemetryEnvelope] = []
        self.closed = False

    def publish(self, envelope: TelemetryEnvelope) -> None:
        self.published.append(envelope)

    def close(self, timeout_seconds: float = 10.0) -> None:
        self.closed = True


@pytest.fixture(scope="module", autouse=True)
def ros_context():
    rclpy.init()
    yield
    rclpy.shutdown()


@pytest.fixture
def fake_bridge() -> FakeProducerBridge:
    return FakeProducerBridge()


@pytest.fixture
def node(fake_bridge: FakeProducerBridge):
    n = StreamingBridgeNode(
        robot_id=ROBOT_ID,
        robot_run_id=ROBOT_RUN_ID,
        producer_bridge=fake_bridge,
    )
    yield n
    n.destroy_node()


def _make_odometry(stamp_sec: int, stamp_nanosec: int) -> Odometry:
    msg = Odometry()
    msg.header.stamp = Time(sec=stamp_sec, nanosec=stamp_nanosec)
    msg.header.frame_id = "odom"
    msg.pose.pose.position.x = 1.5
    msg.pose.pose.position.y = -2.5
    msg.pose.pose.position.z = 0.0
    msg.pose.pose.orientation.w = 1.0
    msg.twist.twist.linear.x = 3.0
    return msg


def _make_imu(stamp_sec: int, stamp_nanosec: int) -> Imu:
    msg = Imu()
    msg.header.stamp = Time(sec=stamp_sec, nanosec=stamp_nanosec)
    msg.header.frame_id = "imu"
    msg.angular_velocity.x = 0.1
    msg.linear_acceleration.z = 9.81
    return msg


def _make_battery_state(stamp_sec: int = 1_700_000_300, stamp_nanosec: int = 55) -> BatteryState:
    msg = BatteryState()
    msg.header.stamp = Time(sec=stamp_sec, nanosec=stamp_nanosec)
    msg.percentage = 0.87
    msg.present = True
    return msg


def _make_control_string(source_timestamp_ns: int = 1_700_000_400_000_000_000) -> String:
    msg = String()
    msg.data = json.dumps(
        {"steering": 3.0, "throttle": 0.2, "brake": 0.0, "source_timestamp_ns": source_timestamp_ns}
    )
    return msg


def _make_mission_string(
    operation_state: str, source_timestamp_ns: int = 1_700_000_500_000_000_000
) -> String:
    msg = String()
    msg.data = json.dumps(
        {
            "mission_id": "mission-test",
            "robot_id": ROBOT_ID,
            "operation_state": operation_state,
            "source_timestamp_ns": source_timestamp_ns,
        }
    )
    return msg


class TestEnvelopeFieldMapping:
    def test_odometry_maps_all_envelope_fields(self, node, fake_bridge):
        msg = _make_odometry(stamp_sec=1_700_000_000, stamp_nanosec=123_456_789)
        spec = TOPIC_SPECS["/vehicle/odom"]

        node._handle_message("/vehicle/odom", spec, msg)

        assert len(fake_bridge.published) == 1
        envelope = fake_bridge.published[0]

        assert envelope.robot_id == ROBOT_ID
        assert envelope.robot_run_id == ROBOT_RUN_ID
        assert envelope.channel == "/vehicle/odom"
        assert envelope.message_type == "nav_msgs/msg/Odometry"
        assert envelope.encoding == EnvelopeEncoding.ROS2_CDR
        assert envelope.source_timestamp_ns == 1_700_000_000 * 1_000_000_000 + 123_456_789
        assert envelope.sequence_number == 0
        assert envelope.ingest_timestamp_ns > 0
        assert len(envelope.payload) > 0

    def test_odometry_payload_deserializes_to_original_message(self, node, fake_bridge):
        msg = _make_odometry(stamp_sec=1_700_000_100, stamp_nanosec=42)
        spec = TOPIC_SPECS["/vehicle/odom"]

        node._handle_message("/vehicle/odom", spec, msg)

        envelope = fake_bridge.published[0]
        decoded = deserialize_message(envelope.payload, Odometry)

        assert decoded.pose.pose.position.x == msg.pose.pose.position.x
        assert decoded.pose.pose.position.y == msg.pose.pose.position.y
        assert decoded.pose.pose.orientation.w == msg.pose.pose.orientation.w
        assert decoded.twist.twist.linear.x == msg.twist.twist.linear.x
        assert decoded.header.stamp.sec == msg.header.stamp.sec
        assert decoded.header.stamp.nanosec == msg.header.stamp.nanosec

    def test_imu_maps_header_timestamp_and_type(self, node, fake_bridge):
        msg = _make_imu(stamp_sec=1_700_000_200, stamp_nanosec=999)
        spec = TOPIC_SPECS["/vehicle/imu"]

        node._handle_message("/vehicle/imu", spec, msg)

        envelope = fake_bridge.published[0]
        assert envelope.channel == "/vehicle/imu"
        assert envelope.message_type == "sensor_msgs/msg/Imu"
        assert envelope.source_timestamp_ns == 1_700_000_200 * 1_000_000_000 + 999

        decoded = deserialize_message(envelope.payload, Imu)
        assert decoded.angular_velocity.x == msg.angular_velocity.x
        assert decoded.linear_acceleration.z == msg.linear_acceleration.z

    def test_battery_state_uses_real_header_timestamp(self, node, fake_bridge):
        """can_replay_node.py sets BatteryState's header.stamp to the
        real CAN vehicle_monitor observation time -- the bridge must use
        it via the HEADER rule."""
        msg = _make_battery_state(stamp_sec=1_700_000_300, stamp_nanosec=55)
        spec = TOPIC_SPECS["/vehicle/status"]
        assert spec.timestamp_rule is SourceTimestampRule.HEADER

        node._handle_message("/vehicle/status", spec, msg)

        envelope = fake_bridge.published[0]
        assert envelope.channel == "/vehicle/status"
        assert envelope.message_type == "sensor_msgs/msg/BatteryState"
        assert envelope.source_timestamp_ns == 1_700_000_300 * 1_000_000_000 + 55

        decoded = deserialize_message(envelope.payload, BatteryState)
        assert decoded.percentage == pytest.approx(msg.percentage)
        assert decoded.present == msg.present
        assert decoded.header.stamp.sec == 1_700_000_300
        assert decoded.header.stamp.nanosec == 55

    def test_battery_state_zero_header_is_rejected_not_silently_used(self, node, fake_bridge):
        """If a BatteryState message arrives with an unpopulated (zero)
        header, the bridge must fail loudly (TelemetryEnvelope's
        Field(gt=0)), never silently publish source_timestamp_ns=0."""
        msg = BatteryState()  # header.stamp defaults to sec=0/nanosec=0
        assert msg.header.stamp.sec == 0 and msg.header.stamp.nanosec == 0
        spec = TOPIC_SPECS["/vehicle/status"]

        node._handle_message("/vehicle/status", spec, msg)

        assert node.failed_count == 1
        assert node.published_count == 0
        assert fake_bridge.published == []

    def test_vehicle_control_preserves_observed_feedback_json(self, node, fake_bridge):
        """/vehicle/control remains observed vehicle feedback -- the
        bridge must not rename the channel or reinterpret the payload."""
        msg = _make_control_string(source_timestamp_ns=1_700_000_400_000_000_000)
        spec = TOPIC_SPECS["/vehicle/control"]
        assert spec.timestamp_rule is SourceTimestampRule.JSON_FIELD

        node._handle_message("/vehicle/control", spec, msg)

        envelope = fake_bridge.published[0]
        assert envelope.channel == "/vehicle/control"
        assert envelope.message_type == "std_msgs/msg/String"
        assert envelope.source_timestamp_ns == 1_700_000_400_000_000_000

        decoded = deserialize_message(envelope.payload, String)
        assert decoded.data == msg.data
        assert "steering" in decoded.data

    def test_vehicle_control_missing_timestamp_field_fails_loudly(self, node, fake_bridge):
        """No fabricated timestamp when the JSON field is absent -- fails
        and is counted, never silently defaults to callback time."""
        msg = String()
        msg.data = json.dumps({"steering": 1.0, "throttle": 0.0, "brake": 0.0})
        spec = TOPIC_SPECS["/vehicle/control"]

        node._handle_message("/vehicle/control", spec, msg)

        assert node.failed_count == 1
        assert fake_bridge.published == []

    def test_mission_status_preserves_boundary_payload(self, node, fake_bridge):
        msg = _make_mission_string("running", source_timestamp_ns=1_700_000_500_000_000_000)
        spec = TOPIC_SPECS["/mission/status"]
        assert spec.timestamp_rule is SourceTimestampRule.JSON_FIELD

        node._handle_message("/mission/status", spec, msg)

        envelope = fake_bridge.published[0]
        assert envelope.channel == "/mission/status"
        assert envelope.message_type == "std_msgs/msg/String"
        assert envelope.source_timestamp_ns == 1_700_000_500_000_000_000

        decoded = deserialize_message(envelope.payload, String)
        assert decoded.data == msg.data
        assert "running" in decoded.data


class TestSequenceNumbering:
    def test_sequence_is_monotonic_across_different_channels(self, node, fake_bridge):
        node._handle_message("/vehicle/odom", TOPIC_SPECS["/vehicle/odom"], _make_odometry(1, 0))
        node._handle_message("/vehicle/imu", TOPIC_SPECS["/vehicle/imu"], _make_imu(1, 0))
        node._handle_message(
            "/vehicle/status", TOPIC_SPECS["/vehicle/status"], _make_battery_state()
        )
        node._handle_message(
            "/mission/status", TOPIC_SPECS["/mission/status"], _make_mission_string("completed")
        )

        seqs = [e.sequence_number for e in fake_bridge.published]
        assert seqs == [0, 1, 2, 3]

    def test_sequence_not_sorted_by_source_timestamp(self, node, fake_bridge):
        # A LATER source timestamp published FIRST must still get the
        # earlier sequence number -- sequence tracks bridge arrival
        # order, never source-timestamp order.
        node._handle_message(
            "/vehicle/odom", TOPIC_SPECS["/vehicle/odom"], _make_odometry(stamp_sec=2000, stamp_nanosec=0)
        )
        node._handle_message(
            "/vehicle/imu", TOPIC_SPECS["/vehicle/imu"], _make_imu(stamp_sec=1000, stamp_nanosec=0)
        )

        odom_envelope, imu_envelope = fake_bridge.published
        assert odom_envelope.sequence_number == 0
        assert imu_envelope.sequence_number == 1
        assert odom_envelope.source_timestamp_ns > imu_envelope.source_timestamp_ns


class TestFailureHandling:
    def test_failed_message_is_counted_and_does_not_raise(self, node, fake_bridge):
        class RaisingBridge:
            def publish(self, envelope):
                raise RuntimeError("simulated Kafka failure")

        node._bridge = RaisingBridge()
        msg = _make_odometry(1, 0)

        node._handle_message("/vehicle/odom", TOPIC_SPECS["/vehicle/odom"], msg)

        assert node.failed_count == 1
        assert node.published_count == 0

    def test_successful_messages_increment_published_count(self, node, fake_bridge):
        node._handle_message("/vehicle/odom", TOPIC_SPECS["/vehicle/odom"], _make_odometry(1, 0))
        node._handle_message("/vehicle/imu", TOPIC_SPECS["/vehicle/imu"], _make_imu(1, 0))

        assert node.published_count == 2
        assert node.failed_count == 0


class TestTopicSpecs:
    def test_all_five_expected_topics_are_registered(self):
        assert set(TOPIC_SPECS) == {
            "/vehicle/odom",
            "/vehicle/imu",
            "/vehicle/status",
            "/vehicle/control",
            "/mission/status",
        }

    def test_header_bearing_topics_use_header_rule(self):
        assert TOPIC_SPECS["/vehicle/odom"].timestamp_rule is SourceTimestampRule.HEADER
        assert TOPIC_SPECS["/vehicle/imu"].timestamp_rule is SourceTimestampRule.HEADER

    def test_battery_state_uses_header_rule(self):
        """/vehicle/status uses HEADER because can_replay_node.py
        populates BatteryState's header.stamp with a real CAN
        observation time."""
        assert TOPIC_SPECS["/vehicle/status"].timestamp_rule is SourceTimestampRule.HEADER

    def test_json_string_channels_use_json_field_rule(self):
        assert TOPIC_SPECS["/vehicle/control"].timestamp_rule is SourceTimestampRule.JSON_FIELD
        assert TOPIC_SPECS["/mission/status"].timestamp_rule is SourceTimestampRule.JSON_FIELD


class TestLifecycleEvents:
    """Phase 7.2 -- emit_lifecycle_events is opt-in (default False),
    verified by construction: every test above constructs the node with
    no such argument and asserts exact published-envelope counts/
    indices/sequence numbers, all still passing unchanged -- proof the
    default genuinely changes nothing for an existing caller."""

    def test_disabled_by_default_publishes_no_lifecycle_events(self, fake_bridge):
        node = StreamingBridgeNode(
            robot_id=ROBOT_ID, robot_run_id=ROBOT_RUN_ID, producer_bridge=fake_bridge
        )
        try:
            assert fake_bridge.published == []
        finally:
            node.destroy_node()

    def test_enabled_publishes_run_start_on_construction(self, fake_bridge):
        node = StreamingBridgeNode(
            robot_id=ROBOT_ID,
            robot_run_id=ROBOT_RUN_ID,
            producer_bridge=fake_bridge,
            emit_lifecycle_events=True,
        )
        try:
            assert len(fake_bridge.published) == 1
            envelope = fake_bridge.published[0]
            assert parse_run_event(envelope) is RunEventType.RUN_START
            assert envelope.robot_id == ROBOT_ID
            assert envelope.robot_run_id == ROBOT_RUN_ID
        finally:
            node.destroy_node()

    def test_enabled_publishes_run_start_then_telemetry_then_run_end_in_order(
        self, fake_bridge
    ):
        node = StreamingBridgeNode(
            robot_id=ROBOT_ID,
            robot_run_id=ROBOT_RUN_ID,
            producer_bridge=fake_bridge,
            emit_lifecycle_events=True,
        )
        try:
            node._handle_message(
                "/vehicle/odom", TOPIC_SPECS["/vehicle/odom"], _make_odometry(1, 0)
            )
            node.shutdown()

            assert len(fake_bridge.published) == 3
            assert parse_run_event(fake_bridge.published[0]) is RunEventType.RUN_START
            assert fake_bridge.published[1].channel == "/vehicle/odom"
            assert parse_run_event(fake_bridge.published[2]) is RunEventType.RUN_END
        finally:
            node.destroy_node()

    def test_run_start_does_not_consume_the_telemetry_sequence_counter(self, fake_bridge):
        """The first TELEMETRY message must still get sequence_number=0
        -- RUN_START uses its own, entirely separate lifecycle sequence
        counter (Phase 7.2.1), never telemetry's, exactly matching every
        existing (emit_lifecycle_events=False) test's assumption that
        telemetry sequencing starts at 0."""
        node = StreamingBridgeNode(
            robot_id=ROBOT_ID,
            robot_run_id=ROBOT_RUN_ID,
            producer_bridge=fake_bridge,
            emit_lifecycle_events=True,
        )
        try:
            node._handle_message(
                "/vehicle/odom", TOPIC_SPECS["/vehicle/odom"], _make_odometry(1, 0)
            )
            telemetry_envelope = fake_bridge.published[1]
            assert telemetry_envelope.sequence_number == 0
        finally:
            node.destroy_node()

    def test_lifecycle_events_sequence_independently_starting_at_zero(self, fake_bridge):
        """RUN_START and RUN_END occupy their OWN 0..1 sequence space
        (Phase 7.2.1) -- regardless of how many telemetry messages were
        published in between, never colliding with telemetry's own
        sequence numbers."""
        node = StreamingBridgeNode(
            robot_id=ROBOT_ID,
            robot_run_id=ROBOT_RUN_ID,
            producer_bridge=fake_bridge,
            emit_lifecycle_events=True,
        )
        try:
            node._handle_message(
                "/vehicle/odom", TOPIC_SPECS["/vehicle/odom"], _make_odometry(1, 0)
            )
            node._handle_message("/vehicle/imu", TOPIC_SPECS["/vehicle/imu"], _make_imu(1, 0))
            node.shutdown()

            run_start, _odom, _imu, run_end = fake_bridge.published
            assert run_start.sequence_number == 0
            assert run_end.sequence_number == 1
        finally:
            node.destroy_node()

    def test_lifecycle_event_publish_failure_is_logged_and_never_raises(self):
        class RaisingBridge:
            def publish(self, envelope):
                raise RuntimeError("simulated Kafka failure")

            def close(self, timeout_seconds=10.0):
                pass

        # Must not raise out of __init__ even though the injected bridge
        # always fails -- a broken control-event publish must never
        # prevent the node from starting and serving real telemetry.
        node = StreamingBridgeNode(
            robot_id=ROBOT_ID,
            robot_run_id=ROBOT_RUN_ID,
            producer_bridge=RaisingBridge(),
            emit_lifecycle_events=True,
        )
        try:
            node.shutdown()  # must also not raise
        finally:
            node.destroy_node()
