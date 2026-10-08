"""Real DDS round trip through the bridge's subscriptions (no Kafka).

A publisher node publishes through real rclpy/DDS with the QoS a static
broadcaster uses (reliable, transient-local); the bridge node's own
subscription callback receives it and hands an envelope to a fake producer.
This covers what direct ``_handle_message`` calls cannot: the latched QoS
match, and the bytes DDS actually delivers.

Runs only inside the streaming-bridge image.
"""

from __future__ import annotations

import time

import pytest
import rclpy
from builtin_interfaces.msg import Time
from geometry_msgs.msg import TransformStamped
from rclpy.qos import (
    QoSDurabilityPolicy,
    QoSHistoryPolicy,
    QoSProfile,
    QoSReliabilityPolicy,
)
from rclpy.serialization import deserialize_message, serialize_message
from tf2_msgs.msg import TFMessage


from sceneops_streaming_bridge.node import StreamingBridgeNode  # noqa: E402

LATCHED = QoSProfile(
    reliability=QoSReliabilityPolicy.RELIABLE,
    durability=QoSDurabilityPolicy.TRANSIENT_LOCAL,
    history=QoSHistoryPolicy.KEEP_LAST,
    depth=100,
)


class FakeProducerBridge:
    def __init__(self) -> None:
        self.published = []

    def publish(self, envelope) -> None:
        self.published.append(envelope)

    def close(self, timeout_seconds: float = 10.0) -> None:
        pass


@pytest.fixture(scope="module", autouse=True)
def ros_context():
    rclpy.init()
    yield
    rclpy.shutdown()


def _static_tf(stamp: Time) -> TFMessage:
    transform = TransformStamped()
    transform.header.stamp = stamp
    transform.header.frame_id = "base_link"
    transform.child_frame_id = "cam_front"
    transform.transform.rotation.w = 1.0
    return TFMessage(transforms=[transform])


def _bridge_receives(message: TFMessage, *, publish_before_bridge: bool):
    producer = FakeProducerBridge()
    publisher_node = rclpy.create_node("static_broadcaster_under_test")
    publisher = publisher_node.create_publisher(TFMessage, "/tf_static", LATCHED)
    bridge = None
    try:
        if publish_before_bridge:
            publisher.publish(message)
        bridge = StreamingBridgeNode(
            robot_id="robot-dds", robot_run_id="run-dds", producer_bridge=producer
        )
        if not publish_before_bridge:
            deadline = time.monotonic() + 10
            while (
                publisher.get_subscription_count() < 1 and time.monotonic() < deadline
            ):
                rclpy.spin_once(bridge, timeout_sec=0.1)
            publisher.publish(message)
        deadline = time.monotonic() + 10
        while not producer.published and time.monotonic() < deadline:
            rclpy.spin_once(bridge, timeout_sec=0.1)
        return producer.published, bridge.failed_count
    finally:
        if bridge is not None:
            bridge.destroy_node()
        publisher_node.destroy_node()


@pytest.mark.parametrize("publish_before_bridge", [False, True])
def test_zero_stamped_latched_static_transform_survives_dds(publish_before_bridge):
    """Zero stamp, including a transform latched before the bridge started:
    forwarded verbatim, source timestamp 0, bytes equal to the publisher's
    (DDS padding removed), not counted as a failure."""
    message = _static_tf(Time(sec=0, nanosec=0))

    published, failed = _bridge_receives(
        message, publish_before_bridge=publish_before_bridge
    )

    assert failed == 0
    [envelope] = published
    assert envelope.channel == "/tf_static"
    assert envelope.source_timestamp_ns == 0
    assert envelope.ingest_timestamp_ns > 0
    # The recording's bytes are the publisher's layout: same length as its
    # serialization (so no DDS tail padding), same decoded content. Inner
    # padding bytes are indeterminate in serialize_message, so they are not
    # compared byte for byte.
    assert len(envelope.payload) == len(serialize_message(message))
    decoded = deserialize_message(envelope.payload, TFMessage)
    assert decoded == message
    stamp = decoded.transforms[0].header.stamp
    assert (stamp.sec, stamp.nanosec) == (0, 0)


def test_stamped_static_transform_keeps_its_source_stamp_over_dds():
    message = _static_tf(Time(sec=1_532_402_927, nanosec=604_844_000))

    published, failed = _bridge_receives(message, publish_before_bridge=False)

    assert failed == 0
    assert published[0].source_timestamp_ns == 1_532_402_927_604_844_000
    assert len(published[0].payload) == len(serialize_message(message))
    assert deserialize_message(published[0].payload, TFMessage) == message
