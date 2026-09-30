#!/usr/bin/env python3
"""StreamingBridgeNode: bridges real ROS2 telemetry topics to the Kafka
streaming transport.

Responsibility: exactly one hop --

    ROS2 message -> TelemetryEnvelope -> TelemetryProducer

The bridge is transport infrastructure. It never imports/knows about
PostgreSQL, DatasetVersion, Scene, RobotRun DB records, Episode,
ArtifactStore, MinIO, MCAP, Celery, or Airflow -- verified by construction
(no such import appears anywhere in this file or its dependencies,
sceneops_core.streaming / sceneops_streaming).

Topic/type map and per-topic source-timestamp rule are audited against
the actual ros2/nodes/can_replay_node.py implementation -- see
docs/architecture/streaming-transport.md's ROS2 topic mapping section for
the full per-topic contract.

Usage (inside the ros2 Docker sandbox):
    python3 /workspace/nodes/streaming_bridge_node.py \\
        --robot-id robot-nuscenes-streaming --robot-run-id run-<unique>
"""

from __future__ import annotations

import argparse
import asyncio
import json
import logging
import signal
import threading
from dataclasses import dataclass
from enum import Enum
from typing import Callable

import rclpy
from nav_msgs.msg import Odometry
from rclpy.node import Node
from rclpy.serialization import serialize_message
from sensor_msgs.msg import BatteryState, Imu
from std_msgs.msg import String

from sceneops_core.streaming import EnvelopeEncoding, TelemetryEnvelope
from sceneops_streaming import KafkaTelemetryProducer, StreamingSettings

logger = logging.getLogger("sceneops.ros2_streaming_bridge")


class SourceTimestampRule(str, Enum):
    """Per-topic source-timestamp extraction rule:

    HEADER     -- msg.header.stamp.{sec,nanosec} is populated by the
                  publisher with a real source observation time; use it
                  verbatim as source_timestamp_ns. Never replaced by
                  bridge receive time.
    JSON_FIELD -- msg.data is a JSON string (std_msgs/String has no
                  Header) carrying an explicit "source_timestamp_ns"
                  integer field, threaded through by can_replay_node.py.
                  Used for both /vehicle/control (real CAN observation
                  time) and /mission/status (synthetic replay-event time)
                  -- one shared wire convention, two different semantic
                  meanings, both documented per-topic below.
    CALLBACK   -- for a channel that lacks any authoritative source time;
                  falls back to this bridge's own ROS2 node-clock time at
                  the moment the callback runs. Not used by any of the
                  current five channels -- every channel has either a
                  real header timestamp or an explicit JSON field (real
                  or synthetic-but-documented).
    """

    HEADER = "header"
    JSON_FIELD = "json_field"
    CALLBACK = "callback"


@dataclass(frozen=True)
class TopicSpec:
    message_type: type
    message_type_name: str
    timestamp_rule: SourceTimestampRule


# Single source of truth for topic subscriptions -- every subscription
# this node creates comes from this one map; nothing is scattered across
# ad hoc callbacks. Because the node only ever subscribes to the topics
# listed here, an unsupported message type is prevented by construction
# rather than handled as a runtime branch: a topic this bridge doesn't
# know about is simply never subscribed to.
#
# Timestamp fidelity, audited against ros2/nodes/can_replay_node.py, not
# assumed from docs/workflows/robot-run-and-mcap.md alone:
#
#   /vehicle/odom     nav_msgs/msg/Odometry        HEADER -- header.stamp
#                     is the real nuScenes CAN 'pose' record's own utime
#                     (can_timestamp_to_ns, ros2/nodes/can_timestamp.py).
#   /vehicle/imu      sensor_msgs/msg/Imu          HEADER -- real CAN
#                     'ms_imu' record utime; one record maps to exactly
#                     one Imu message, no multi-source combining.
#   /vehicle/status   sensor_msgs/msg/BatteryState HEADER -- real CAN
#                     'vehicle_monitor' record utime.
#   /vehicle/control  std_msgs/msg/String (JSON)   JSON_FIELD -- no ROS
#                     Header exists on this type, so the real CAN
#                     'vehicle_monitor' record's utime (the SAME record
#                     /vehicle/status derives its header from -- one
#                     utime owns both) is threaded through explicitly as
#                     a "source_timestamp_ns" JSON field instead. Still
#                     observed vehicle feedback, NOT an autonomy-policy
#                     command -- never renamed.
#   /mission/status   std_msgs/msg/String (JSON)   JSON_FIELD -- no
#                     original CAN time exists (synthetic replay-boundary
#                     signal, not a nuScenes sensor channel); the JSON's
#                     "source_timestamp_ns" field carries the replay
#                     session's OWN publish-time clock instead, using the
#                     same wire field name/extraction mechanism as
#                     /vehicle/control for one consistent convention, with
#                     a different -- explicitly documented -- semantic
#                     meaning.
TOPIC_SPECS: dict[str, TopicSpec] = {
    "/vehicle/odom": TopicSpec(
        Odometry, "nav_msgs/msg/Odometry", SourceTimestampRule.HEADER
    ),
    "/vehicle/imu": TopicSpec(Imu, "sensor_msgs/msg/Imu", SourceTimestampRule.HEADER),
    "/vehicle/status": TopicSpec(
        BatteryState, "sensor_msgs/msg/BatteryState", SourceTimestampRule.HEADER
    ),
    "/vehicle/control": TopicSpec(
        String, "std_msgs/msg/String", SourceTimestampRule.JSON_FIELD
    ),
    "/mission/status": TopicSpec(
        String, "std_msgs/msg/String", SourceTimestampRule.JSON_FIELD
    ),
}


class _AsyncProducerBridge:
    """Bounded, understandable sync-callback -> async-producer handoff.
    Runs ONE ``KafkaTelemetryProducer`` on a dedicated background asyncio
    event loop for the node's whole lifetime, so a ROS2 callback (always
    synchronous here -- the node uses the default single-threaded
    executor via ``rclpy.spin_once``) can publish without spinning up a
    fresh event loop per message.

    ``publish()`` blocks the calling (ROS2 callback) thread for at most
    ``publish_timeout_seconds`` -- there is no queue of this bridge's own;
    the only buffering in this data path is librdkafka's own internal
    client queue (inside ``KafkaTelemetryProducer``/``confluent_kafka``),
    which is bounded by its own default configuration. A stalled or
    unreachable broker surfaces as a timeout/exception in the ROS2
    callback (caught and logged by ``StreamingBridgeNode``) rather than
    growing unboundedly in this bridge's memory or silently dropping the
    message.

    This is a deliberately simple design for the expected message volume
    (one nuScenes CAN-replay scene, ~a few thousand messages over tens of
    seconds), which never approaches librdkafka's default buffer
    capacity. Whether this remains sufficient at materially higher
    throughput is left to future benchmarking work, not decided here.
    """

    def __init__(
        self, *, settings: StreamingSettings, publish_timeout_seconds: float = 5.0
    ) -> None:
        self._publish_timeout_seconds = publish_timeout_seconds
        self._producer = KafkaTelemetryProducer(settings=settings)
        self._loop = asyncio.new_event_loop()
        self._thread = threading.Thread(
            target=self._loop.run_forever, daemon=True, name="kafka-producer-loop"
        )
        self._thread.start()

    def publish(self, envelope: TelemetryEnvelope) -> None:
        future = asyncio.run_coroutine_threadsafe(
            self._producer.publish(envelope), self._loop
        )
        future.result(timeout=self._publish_timeout_seconds)

    def close(self, timeout_seconds: float = 10.0) -> None:
        """Flush pending production, close the producer, stop the loop.
        Deterministic and bounded -- never blocks forever."""
        future = asyncio.run_coroutine_threadsafe(self._producer.close(), self._loop)
        try:
            future.result(timeout=timeout_seconds)
        finally:
            self._loop.call_soon_threadsafe(self._loop.stop)
            self._thread.join(timeout=5.0)


class StreamingBridgeNode(Node):
    def __init__(
        self,
        *,
        robot_id: str,
        robot_run_id: str,
        publish_timeout_seconds: float = 5.0,
        producer_bridge: object | None = None,
    ) -> None:
        """``producer_bridge`` defaults to a real ``_AsyncProducerBridge``
        (real Kafka client). Tests inject a fake exposing the same
        ``publish(envelope)``/``close()`` shape instead, so envelope-
        construction logic is testable without Kafka and without
        constructing a real producer -- see
        ros2/nodes/tests/test_streaming_bridge_node.py."""
        super().__init__("streaming_bridge_node")
        self._robot_id = robot_id
        self._robot_run_id = robot_run_id

        # One monotonically increasing sequence per (robot_id,
        # robot_run_id) bridge stream, across ALL channels -- represents
        # bridge-observed arrival order, never source-timestamp order.
        # No lock: the default rclpy executor (spin_once, used by main()
        # below) runs every subscription callback sequentially on one
        # thread, so callbacks never execute concurrently with each other.
        self._sequence = 0

        self.published_count = 0
        self.failed_count = 0

        if producer_bridge is None:
            settings = StreamingSettings()
            producer_bridge = _AsyncProducerBridge(
                settings=settings, publish_timeout_seconds=publish_timeout_seconds
            )
            bootstrap_servers, telemetry_topic = settings.bootstrap_servers, settings.telemetry_topic
        else:
            bootstrap_servers, telemetry_topic = "<injected>", "<injected>"
        self._bridge = producer_bridge

        self._subscriptions = [
            self.create_subscription(spec.message_type, topic, self._make_callback(topic, spec), 10)
            for topic, spec in TOPIC_SPECS.items()
        ]

        self.get_logger().info(
            f"streaming bridge ready: robot_id={robot_id} "
            f"robot_run_id={robot_run_id} topics={list(TOPIC_SPECS)} "
            f"bootstrap_servers={bootstrap_servers} "
            f"topic={telemetry_topic}"
        )

    def _next_sequence_number(self) -> int:
        seq = self._sequence
        self._sequence += 1
        return seq

    def _make_callback(self, topic: str, spec: TopicSpec) -> Callable[[object], None]:
        def _callback(msg: object) -> None:
            self._handle_message(topic, spec, msg)

        return _callback

    def _source_timestamp_ns(self, spec: TopicSpec, msg: object) -> int:
        if spec.timestamp_rule is SourceTimestampRule.HEADER:
            stamp = msg.header.stamp
            return stamp.sec * 1_000_000_000 + stamp.nanosec
        if spec.timestamp_rule is SourceTimestampRule.JSON_FIELD:
            # msg.data is already a plain Python str (rclpy deserializes
            # std_msgs/String before the callback runs) -- no extra CDR
            # decode step needed here. A missing/malformed field surfaces
            # naturally as KeyError/JSONDecodeError, caught by
            # _handle_message's failure handling below -- never silently
            # coerced to 0 or callback time.
            payload = json.loads(msg.data)
            return int(payload["source_timestamp_ns"])
        # CALLBACK -- unused by the current five channels (see
        # SourceTimestampRule's own docstring).
        return self.get_clock().now().nanoseconds

    def _handle_message(self, topic: str, spec: TopicSpec, msg: object) -> None:
        try:
            payload = serialize_message(msg)
            # ingest_timestamp_ns is deliberately left to
            # TelemetryEnvelope's own default_factory -- constructing the
            # envelope here IS accepting the message at the transport
            # boundary; the bridge captures no earlier, more-precise
            # acceptance instant, so there is exactly one
            # default-assignment point, not two.
            envelope = TelemetryEnvelope(
                robot_id=self._robot_id,
                robot_run_id=self._robot_run_id,
                channel=topic,
                message_type=spec.message_type_name,
                source_timestamp_ns=self._source_timestamp_ns(spec, msg),
                sequence_number=self._next_sequence_number(),
                encoding=EnvelopeEncoding.ROS2_CDR,
                payload=payload,
            )
            self._bridge.publish(envelope)
            self.published_count += 1
        except Exception as exc:
            # Fail loudly, never silently drop. No DLQ, no retry storage
            # -- those are reliability-boundary concerns, not this
            # bridge's. rclpy's node logger is NOT Python's stdlib
            # `logging` -- it has its own kwarg set (throttle_duration_sec,
            # once, ...) and rejects exc_info=True outright, so the
            # exception is formatted into the message text instead.
            self.failed_count += 1
            self.get_logger().error(
                f"failed to bridge message on {topic}: "
                f"{type(exc).__name__}: {exc} "
                f"(published={self.published_count} failed={self.failed_count})"
            )

    def shutdown(self, timeout_seconds: float = 10.0) -> None:
        """Flush pending Kafka production and close the producer. Does
        NOT stop ROS2 spinning -- the caller (main()) stops spin_once
        first, then calls this: stop accepting new callbacks -> flush ->
        close producer -> destroy node."""
        self._bridge.close(timeout_seconds=timeout_seconds)


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Bridge ROS2 telemetry topics to Kafka"
    )
    parser.add_argument(
        "--robot-id", required=True, help="Logical robot identity (transport metadata only)"
    )
    parser.add_argument(
        "--robot-run-id",
        required=True,
        help="RobotRun/session identity -- Kafka partitioning key (transport metadata only)",
    )
    parser.add_argument(
        "--publish-timeout-seconds",
        type=float,
        default=5.0,
        help="Bound on how long a single publish() may block the ROS2 callback thread",
    )
    args = parser.parse_args()

    logging.basicConfig(
        level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s"
    )

    rclpy.init()
    node = StreamingBridgeNode(
        robot_id=args.robot_id,
        robot_run_id=args.robot_run_id,
        publish_timeout_seconds=args.publish_timeout_seconds,
    )

    shutdown_requested = threading.Event()

    def _handle_signal(signum: int, _frame: object) -> None:
        node.get_logger().info(f"received signal {signum} -- beginning graceful shutdown")
        shutdown_requested.set()

    signal.signal(signal.SIGTERM, _handle_signal)
    signal.signal(signal.SIGINT, _handle_signal)

    try:
        # spin_once in a loop (not a single blocking spin()) so the signal
        # handler's flag is checked at a bounded interval -- stops
        # accepting new callbacks as soon as shutdown is requested, never
        # mid-callback.
        while not shutdown_requested.is_set():
            rclpy.spin_once(node, timeout_sec=0.5)
    finally:
        node.get_logger().info(
            f"shutting down -- published={node.published_count} "
            f"failed={node.failed_count}"
        )
        node.shutdown()
        node.destroy_node()
        rclpy.shutdown()

    if node.failed_count > 0:
        raise SystemExit(1)


if __name__ == "__main__":
    main()
