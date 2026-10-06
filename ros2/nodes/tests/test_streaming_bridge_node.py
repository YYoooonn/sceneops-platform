"""Unit tests for StreamingBridgeNode (raw, registry-driven subscriptions).

Runs ONLY inside the ros2 container -- rclpy and the ROS2 message
packages (nav_msgs, sensor_msgs, std_msgs) are apt-installed system
packages there, not importable from the main workspace venv. Invoke via:

    docker compose --profile ros2 run --rm ros2 \\
        python3 -m pytest /workspace/nodes/tests/ -v

(or `make ros2-test`, which runs this suite with the capture tests).

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
from geometry_msgs.msg import TransformStamped
from rclpy.qos import QoSDurabilityPolicy, QoSHistoryPolicy
from rclpy.serialization import deserialize_message, serialize_message
from sensor_msgs.msg import BatteryState, CameraInfo, CompressedImage, Imu, PointCloud2
from std_msgs.msg import String
from tf2_msgs.msg import TFMessage

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from sceneops_core.streaming import (  # noqa: E402
    DEFAULT_REGISTRY,
    ChannelSpec,
    EnvelopeEncoding,
    RunEventType,
    TelemetryEnvelope,
    TimestampRule,
    build_channel_registry,
    parse_run_event,
)

from streaming_bridge_node import (  # noqa: E402
    StreamingBridgeNode,
    subscription_qos,
)

REPO_ROOT = Path(__file__).resolve().parents[2]
SURROUND_CHANNELS = REPO_ROOT / "channels" / "surround-camera-lidar.json"

# One spec per default channel, as the node's callbacks receive them.
TOPIC_SPECS = {spec.topic: spec for spec in DEFAULT_REGISTRY}

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


def _make_tf(stamp_sec: int, stamp_nanosec: int, *, count: int = 1) -> TFMessage:
    msg = TFMessage()
    for index in range(count):
        transform = TransformStamped()
        transform.header.stamp = Time(sec=stamp_sec, nanosec=stamp_nanosec + index)
        transform.header.frame_id = "map"
        transform.child_frame_id = f"base_link_{index}"
        transform.transform.rotation.w = 1.0
        msg.transforms.append(transform)
    return msg


def _handle(node, topic: str, message) -> None:
    """Deliver ``message`` to the node's callback path as DDS would: the
    serialized CDR bytes of a raw subscription."""
    node._handle_message(TOPIC_SPECS[topic], serialize_message(message))


class TestEnvelopeFieldMapping:
    def test_odometry_maps_all_envelope_fields(self, node, fake_bridge):
        msg = _make_odometry(stamp_sec=1_700_000_000, stamp_nanosec=123_456_789)

        _handle(node, "/vehicle/odom", msg)

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

    def test_payload_bytes_are_forwarded_unchanged(self, node, fake_bridge):
        """What the bridge received is what it forwards: it never
        replaces the bytes with a reserialization. A tail it cannot prove
        to be DDS padding (here 4 bytes, past the 1-3 byte padding range)
        survives."""
        raw = serialize_message(_make_odometry(1_700_000_100, 42)) + b"\x00\x00\x00\x00"

        node._handle_message(TOPIC_SPECS["/vehicle/odom"], raw)

        assert fake_bridge.published[0].payload == raw

    @pytest.mark.parametrize("padding", [1, 2, 3])
    def test_dds_alignment_padding_is_removed(self, node, fake_bridge, padding):
        """DDS pads a small serialized sample to a 4-byte multiple. The
        recording must hold the publisher's bytes, not the padded take."""
        exact = serialize_message(_make_control_string())
        assert len(exact) % 4 != 0 or padding  # a real, unpadded layout
        node._handle_message(TOPIC_SPECS["/vehicle/control"], exact + bytes(padding))

        assert fake_bridge.published[0].payload == exact

    def test_padding_proof_does_not_depend_on_inner_padding_bytes(self, node, fake_bridge):
        """Alignment padding inside a CDR message is indeterminate in a
        re-serialization (uninitialized memory in serialize_message), so
        the proof uses the canonical length only. Garbage in an inner
        padding byte must neither defeat the trim nor be altered."""
        message = _make_tf(1_532_402_927, 5)
        message.transforms[0].child_frame_id = "cam_front"
        exact = bytearray(serialize_message(message))
        # The string is followed by padding up to the next 8-byte boundary
        # (CDR alignment is relative to the byte after the 4-byte header).
        end_of_string = bytes(exact).index(b"cam_front") + len(b"cam_front\x00")
        padding = (-(end_of_string - 4)) % 8
        assert padding > 0
        position = end_of_string
        assert exact[position] == 0
        exact[position] = 0x1D
        exact = bytes(exact)

        node._handle_message(TOPIC_SPECS["/tf"], exact + b"\x00\x00")

        assert fake_bridge.published[0].payload == exact

    def test_non_zero_tail_is_not_treated_as_padding(self, node, fake_bridge):
        exact = serialize_message(_make_control_string())
        raw = exact + b"\x00\x07"

        node._handle_message(TOPIC_SPECS["/vehicle/control"], raw)

        assert fake_bridge.published[0].payload == raw

    def test_exact_payload_with_trailing_zero_bytes_is_untouched(self, node, fake_bridge):
        """A message whose own last bytes are zero is not trimmed: the
        canonical length, not the zeros, decides."""
        raw = serialize_message(_make_battery_state())  # ends in zero fields

        node._handle_message(TOPIC_SPECS["/vehicle/status"], raw)

        assert fake_bridge.published[0].payload == raw

    def test_odometry_payload_deserializes_to_original_message(self, node, fake_bridge):
        msg = _make_odometry(stamp_sec=1_700_000_100, stamp_nanosec=42)

        _handle(node, "/vehicle/odom", msg)

        decoded = deserialize_message(fake_bridge.published[0].payload, Odometry)
        assert decoded.pose.pose.position.x == msg.pose.pose.position.x
        assert decoded.twist.twist.linear.x == msg.twist.twist.linear.x
        assert decoded.header.stamp.sec == msg.header.stamp.sec
        assert decoded.header.stamp.nanosec == msg.header.stamp.nanosec

    def test_imu_maps_header_timestamp_and_type(self, node, fake_bridge):
        _handle(node, "/vehicle/imu", _make_imu(stamp_sec=1_700_000_200, stamp_nanosec=999))

        envelope = fake_bridge.published[0]
        assert envelope.channel == "/vehicle/imu"
        assert envelope.message_type == "sensor_msgs/msg/Imu"
        assert envelope.source_timestamp_ns == 1_700_000_200 * 1_000_000_000 + 999

    def test_battery_state_uses_real_header_timestamp(self, node, fake_bridge):
        assert TOPIC_SPECS["/vehicle/status"].timestamp is TimestampRule.HEADER

        _handle(node, "/vehicle/status", _make_battery_state(1_700_000_300, 55))

        envelope = fake_bridge.published[0]
        assert envelope.message_type == "sensor_msgs/msg/BatteryState"
        assert envelope.source_timestamp_ns == 1_700_000_300 * 1_000_000_000 + 55

    def test_battery_state_zero_header_is_rejected_not_silently_used(self, node, fake_bridge):
        """An unpopulated (zero) header must fail loudly
        (TelemetryEnvelope's Field(gt=0)), never publish
        source_timestamp_ns=0 or substitute another time."""
        _handle(node, "/vehicle/status", BatteryState())

        assert node.failed_count == 1
        assert node.published_count == 0
        assert fake_bridge.published == []

    def test_vehicle_control_preserves_observed_feedback_json(self, node, fake_bridge):
        msg = _make_control_string(source_timestamp_ns=1_700_000_400_000_000_000)
        assert TOPIC_SPECS["/vehicle/control"].timestamp is TimestampRule.JSON_FIELD

        _handle(node, "/vehicle/control", msg)

        envelope = fake_bridge.published[0]
        assert envelope.channel == "/vehicle/control"
        assert envelope.source_timestamp_ns == 1_700_000_400_000_000_000
        assert deserialize_message(envelope.payload, String).data == msg.data

    def test_vehicle_control_missing_timestamp_field_fails_loudly(self, node, fake_bridge):
        msg = String()
        msg.data = json.dumps({"steering": 1.0, "throttle": 0.0, "brake": 0.0})

        _handle(node, "/vehicle/control", msg)

        assert node.failed_count == 1
        assert fake_bridge.published == []

    def test_mission_status_uses_the_source_timestamp_it_carries(self, node, fake_bridge):
        """/mission/status takes its time from the event's own
        source_timestamp_ns -- the source timeline -- never from the
        bridge's or the replayer's clock."""
        stamp = 1_700_000_500_000_000_000
        assert TOPIC_SPECS["/mission/status"].timestamp is TimestampRule.JSON_FIELD

        _handle(node, "/mission/status", _make_mission_string("running", stamp))

        envelope = fake_bridge.published[0]
        assert envelope.source_timestamp_ns == stamp
        assert envelope.ingest_timestamp_ns != stamp
        assert "running" in deserialize_message(envelope.payload, String).data


class TestSensorAndTransformChannels:
    @pytest.fixture
    def sensor_node(self, fake_bridge):
        registry = build_channel_registry([SURROUND_CHANNELS])
        n = StreamingBridgeNode(
            robot_id=ROBOT_ID,
            robot_run_id=ROBOT_RUN_ID,
            registry=registry,
            producer_bridge=fake_bridge,
        )
        n.specs = {spec.topic: spec for spec in registry}
        yield n
        n.destroy_node()

    def test_registry_channels_are_all_subscribed(self, sensor_node):
        assert len(sensor_node._subscriptions) == len(sensor_node._registry)
        assert "/lidar/top/points" in sensor_node.specs
        assert "/tf_static" in sensor_node.specs

    def test_compressed_image_header_stamp_and_bytes(self, sensor_node, fake_bridge):
        msg = CompressedImage()
        msg.header.stamp = Time(sec=1_532_402_927, nanosec=612_404_000)
        msg.header.frame_id = "cam_front"
        msg.format = "jpeg"
        msg.data = bytes(range(256)) * 40
        raw = serialize_message(msg)

        sensor_node._handle_message(sensor_node.specs["/camera/front/image/compressed"], raw)

        envelope = fake_bridge.published[0]
        assert envelope.source_timestamp_ns == 1_532_402_927_612_404_000
        assert envelope.message_type == "sensor_msgs/msg/CompressedImage"
        assert envelope.payload == raw

    def test_camera_info_and_point_cloud_use_header_stamp(self, sensor_node, fake_bridge):
        info = CameraInfo()
        info.header.stamp = Time(sec=5, nanosec=6)
        cloud = PointCloud2()
        cloud.header.stamp = Time(sec=7, nanosec=8)
        cloud.data = bytes(100_000)

        sensor_node._handle_message(
            sensor_node.specs["/camera/front/camera_info"], serialize_message(info)
        )
        sensor_node._handle_message(
            sensor_node.specs["/lidar/top/points"], serialize_message(cloud)
        )

        assert [e.source_timestamp_ns for e in fake_bridge.published] == [
            5_000_000_006,
            7_000_000_008,
        ]
        assert len(fake_bridge.published[1].payload) > 100_000

    def test_tf_uses_first_transform_stamp(self, node, fake_bridge):
        assert TOPIC_SPECS["/tf"].timestamp is TimestampRule.TRANSFORM_HEADER

        _handle(node, "/tf", _make_tf(1_532_402_927, 100, count=3))

        assert fake_bridge.published[0].source_timestamp_ns == 1_532_402_927_000_000_100
        assert fake_bridge.published[0].message_type == "tf2_msgs/msg/TFMessage"

    def test_zero_stamped_static_transform_is_forwarded_verbatim(self, node, fake_bridge):
        """/tf_static carries no observation time: an unstamped (zero)
        header is the source's own value. It stays 0 in the payload and in
        the envelope's source timestamp -- never replaced by the bridge's
        ingest time -- and the message is not rejected."""
        assert TOPIC_SPECS["/tf_static"].allow_zero_stamp is True
        message = _make_tf(0, 0, count=2)
        message.transforms[1].header.stamp = Time(sec=0, nanosec=0)
        raw = serialize_message(message)

        node._handle_message(TOPIC_SPECS["/tf_static"], raw)

        envelope = fake_bridge.published[0]
        assert node.failed_count == 0
        assert envelope.source_timestamp_ns == 0
        assert envelope.ingest_timestamp_ns > 0  # the transport's own time, separate
        assert envelope.payload == raw
        decoded = deserialize_message(envelope.payload, TFMessage)
        assert [(t.header.stamp.sec, t.header.stamp.nanosec) for t in decoded.transforms] == [
            (0, 0),
            (0, 0),
        ]

    def test_stamped_static_transform_keeps_its_stamp(self, node, fake_bridge):
        _handle(node, "/tf_static", _make_tf(1_532_402_927, 5))

        assert fake_bridge.published[0].source_timestamp_ns == 1_532_402_927_000_000_005

    def test_zero_stamp_on_a_non_static_transform_channel_fails_loudly(self, node, fake_bridge):
        assert TOPIC_SPECS["/tf"].allow_zero_stamp is False

        _handle(node, "/tf", _make_tf(0, 0))

        assert node.failed_count == 1
        assert fake_bridge.published == []

    def test_zero_stamp_on_a_sensor_channel_fails_loudly(self, sensor_node, fake_bridge):
        sensor_node._handle_message(
            sensor_node.specs["/lidar/top/points"], serialize_message(PointCloud2())
        )

        assert sensor_node.failed_count == 1
        assert fake_bridge.published == []

    def test_empty_tf_message_fails_loudly(self, node, fake_bridge):
        _handle(node, "/tf_static", TFMessage())

        assert node.failed_count == 1
        assert fake_bridge.published == []


class TestSubscriptionQos:
    def test_latched_channel_is_transient_local(self):
        qos = subscription_qos(TOPIC_SPECS["/tf_static"])
        assert qos.durability == QoSDurabilityPolicy.TRANSIENT_LOCAL

    def test_default_history_is_keep_all_so_bursts_are_not_dropped(self):
        qos = subscription_qos(TOPIC_SPECS["/tf"])
        assert qos.history == QoSHistoryPolicy.KEEP_ALL

    def test_ordinary_channel_is_volatile_with_registry_depth(self):
        spec = ChannelSpec(
            topic="/lidar/top/points",
            message_type="sensor_msgs/msg/PointCloud2",
            timestamp=TimestampRule.HEADER,
            queue_depth=37,
        )
        qos = subscription_qos(spec)
        assert qos.durability == QoSDurabilityPolicy.VOLATILE
        assert qos.history == QoSHistoryPolicy.KEEP_LAST
        assert qos.depth == 37


class TestSequenceNumbering:
    def test_sequence_is_monotonic_across_different_channels(self, node, fake_bridge):
        _handle(node, "/vehicle/odom", _make_odometry(1, 0))
        _handle(node, "/vehicle/imu", _make_imu(1, 0))
        _handle(node, "/vehicle/status", _make_battery_state())
        _handle(node, "/mission/status", _make_mission_string("completed"))

        assert [e.sequence_number for e in fake_bridge.published] == [0, 1, 2, 3]

    def test_sequence_not_sorted_by_source_timestamp(self, node, fake_bridge):
        # A LATER source timestamp published FIRST must still get the
        # earlier sequence number -- sequence tracks bridge arrival
        # order, never source-timestamp order.
        _handle(node, "/vehicle/odom", _make_odometry(stamp_sec=2000, stamp_nanosec=0))
        _handle(node, "/vehicle/imu", _make_imu(stamp_sec=1000, stamp_nanosec=0))

        odom_envelope, imu_envelope = fake_bridge.published
        assert odom_envelope.sequence_number == 0
        assert imu_envelope.sequence_number == 1
        assert odom_envelope.source_timestamp_ns > imu_envelope.source_timestamp_ns

    def test_a_failed_message_still_consumes_its_sequence_number(self, node, fake_bridge):
        """A dropped message leaves a sequence gap, which capture rejects:
        a bridge failure cannot yield a recording that silently lacks a
        message."""
        _handle(node, "/vehicle/odom", _make_odometry(1, 0))
        _handle(node, "/vehicle/status", BatteryState())  # zero stamp -> fails
        _handle(node, "/vehicle/odom", _make_odometry(2, 0))

        assert [e.sequence_number for e in fake_bridge.published] == [0, 2]

    def test_source_duplicates_are_both_forwarded_with_distinct_sequences(
        self, node, fake_bridge
    ):
        """Two identical source messages are two occurrences: the bridge
        never deduplicates, and gives each its own transport sequence."""
        raw = serialize_message(_make_imu(1_700_000_000, 7))

        node._handle_message(TOPIC_SPECS["/vehicle/imu"], raw)
        node._handle_message(TOPIC_SPECS["/vehicle/imu"], raw)

        first, second = fake_bridge.published
        assert first.payload == second.payload
        assert first.source_timestamp_ns == second.source_timestamp_ns
        assert (first.sequence_number, second.sequence_number) == (0, 1)


class TestFailureHandling:
    def test_failed_message_is_counted_and_does_not_raise(self, node, fake_bridge):
        class RaisingBridge:
            def publish(self, envelope):
                raise RuntimeError("simulated Kafka failure")

        node._bridge = RaisingBridge()

        _handle(node, "/vehicle/odom", _make_odometry(1, 0))

        assert node.failed_count == 1
        assert node.published_count == 0

    def test_successful_messages_increment_published_count(self, node, fake_bridge):
        _handle(node, "/vehicle/odom", _make_odometry(1, 0))
        _handle(node, "/vehicle/imu", _make_imu(1, 0))

        assert node.published_count == 2
        assert node.failed_count == 0

    def test_summary_reports_per_channel_counts(self, node, fake_bridge):
        _handle(node, "/vehicle/odom", _make_odometry(1, 0))
        _handle(node, "/vehicle/odom", _make_odometry(2, 0))
        _handle(node, "/vehicle/status", BatteryState())  # zero stamp -> failure

        assert node.summary() == {
            "published": 2,
            "failed": 1,
            "published_by_channel": {"/vehicle/odom": 2},
            "failed_by_channel": {"/vehicle/status": 1},
        }
        assert node.last_message_monotonic is not None


class TestDefaultRegistry:
    def test_default_channels_are_registered(self):
        assert set(TOPIC_SPECS) == {
            "/vehicle/odom",
            "/vehicle/imu",
            "/vehicle/status",
            "/vehicle/control",
            "/mission/status",
            "/tf",
            "/tf_static",
        }

    def test_header_bearing_topics_use_header_rule(self):
        for topic in ("/vehicle/odom", "/vehicle/imu", "/vehicle/status"):
            assert TOPIC_SPECS[topic].timestamp is TimestampRule.HEADER

    def test_json_string_channels_use_json_field_rule(self):
        assert TOPIC_SPECS["/vehicle/control"].timestamp is TimestampRule.JSON_FIELD
        assert TOPIC_SPECS["/mission/status"].timestamp is TimestampRule.JSON_FIELD

    def test_node_subscribes_exactly_to_its_registry(self, node):
        assert len(node._subscriptions) == len(DEFAULT_REGISTRY)


class TestLifecycleEvents:
    """emit_lifecycle_events is a constructor opt-in (default False for
    the node; the CLI turns it on): the tests above assert exact
    published-envelope counts and sequence numbers with it off."""

    def test_disabled_by_default_publishes_no_lifecycle_events(self, fake_bridge):
        node = StreamingBridgeNode(
            robot_id=ROBOT_ID, robot_run_id=ROBOT_RUN_ID, producer_bridge=fake_bridge
        )
        try:
            assert fake_bridge.published == []
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
            _handle(node, "/vehicle/odom", _make_odometry(1, 0))
            node.shutdown()

            assert len(fake_bridge.published) == 3
            assert parse_run_event(fake_bridge.published[0]) is RunEventType.RUN_START
            assert fake_bridge.published[1].channel == "/vehicle/odom"
            assert parse_run_event(fake_bridge.published[2]) is RunEventType.RUN_END
        finally:
            node.destroy_node()

    def test_lifecycle_events_use_their_own_sequence_space(self, fake_bridge):
        """Telemetry keeps its 0-start/no-gap sequence regardless of the
        control events around it; RUN_START/RUN_END number 0 and 1."""
        node = StreamingBridgeNode(
            robot_id=ROBOT_ID,
            robot_run_id=ROBOT_RUN_ID,
            producer_bridge=fake_bridge,
            emit_lifecycle_events=True,
        )
        try:
            _handle(node, "/vehicle/odom", _make_odometry(1, 0))
            _handle(node, "/vehicle/imu", _make_imu(1, 0))
            node.shutdown()

            run_start, odom, imu, run_end = fake_bridge.published
            assert (odom.sequence_number, imu.sequence_number) == (0, 1)
            assert (run_start.sequence_number, run_end.sequence_number) == (0, 1)
        finally:
            node.destroy_node()

    def test_lifecycle_event_publish_failure_is_logged_and_never_raises(self):
        class RaisingBridge:
            def publish(self, envelope):
                raise RuntimeError("simulated Kafka failure")

            def close(self, timeout_seconds=10.0):
                pass

        # A broken control-event publish must never prevent the node from
        # starting and serving real telemetry.
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
