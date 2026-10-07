#!/usr/bin/env python3
"""StreamingBridgeNode: bridges ROS2 topics to the Kafka streaming transport.

Responsibility: exactly one hop --

    ROS2 message (serialized CDR) -> TelemetryEnvelope -> TelemetryProducer

The bridge is transport infrastructure. It never imports/knows about
PostgreSQL, DatasetVersion, Scene, RobotRun DB records, Episode,
ArtifactStore, MinIO, MCAP, Celery, or Airflow -- verified by construction
(no such import appears anywhere in this file or its dependencies,
sceneops_core.streaming / sceneops_streaming).

Which topics it subscribes to, with which type, QoS and source-timestamp
rule, comes from one declarative channel registry
(``sceneops_core.streaming.channels``): the built-in defaults plus any
channel-set files given with ``--channels-file``. Capture validates against
the same registry. See docs/architecture/streaming-transport.md.

Payload fidelity. Subscriptions are *raw*: the bridge receives the
serialized CDR bytes DDS delivered and forwards those bytes, so what capture
writes is what the publisher produced. The bytes are deserialized only to
read the source timestamp the envelope carries. One transport artifact is
removed: DDS pads a small serialized sample to a 4-byte multiple, so a raw
take can end in 1-3 zero bytes the publisher never wrote. ``exact_cdr``
trims them, and only when re-serializing the decoded message proves they
are padding (the tail beyond the canonical length is 1-3 zero bytes). Bytes
that do not match that proof are forwarded unchanged.

Time. ``source_timestamp_ns`` is a timestamp the message itself carries
(header stamp, first transform's header stamp, or a JSON field), read
verbatim. The bridge never substitutes its own clock for it; a message with
no readable source timestamp fails loudly and is counted. ``ingest_timestamp_ns``
(bridge acceptance time) is the transport's own timestamp, a separate fact
that becomes the recording's ``publish_time`` downstream. A source timestamp
of zero (an unstamped header) is forwarded as 0 on a channel that allows it
(``/tf_static``) -- never replaced by the ingest time. ``sequence_number`` is
the bridge's transport arrival counter, never a source-time order.

Usage (inside the ros2 Docker sandbox):
    python3 /workspace/nodes/streaming_bridge_node.py \\
        --robot-id robot-nuscenes-streaming --robot-run-id run-<unique> \\
        [--channels-file /workspace/channels/surround-camera-lidar.json] \\
        [--exit-after-idle-seconds 10]
"""

from __future__ import annotations

import argparse
import asyncio
import json
import logging
import signal
import threading
import time
from collections.abc import Callable
from pathlib import Path

import rclpy
from rclpy.node import Node
from rclpy.qos import (
    QoSDurabilityPolicy,
    QoSHistoryPolicy,
    QoSProfile,
    QoSReliabilityPolicy,
)
from rclpy.serialization import deserialize_message, serialize_message
from rosidl_runtime_py.utilities import get_message

from sceneops_core.streaming import (
    DEFAULT_REGISTRY,
    ChannelRegistry,
    ChannelSpec,
    EnvelopeEncoding,
    RunEventType,
    TelemetryEnvelope,
    TimestampRule,
    build_channel_registry,
    build_control_envelope,
)
from sceneops_streaming import KafkaTelemetryProducer, StreamingSettings

logger = logging.getLogger("sceneops.ros2_streaming_bridge")

SUMMARY_PREFIX = "bridge_summary "


def subscription_qos(spec: ChannelSpec) -> QoSProfile:
    """Reliable delivery; transient-local for a latched channel so static
    data published before the bridge started is still received. History is
    keep-all unless the channel sets a depth (see ``ChannelSpec``)."""
    durability = (
        QoSDurabilityPolicy.TRANSIENT_LOCAL
        if spec.latched
        else QoSDurabilityPolicy.VOLATILE
    )
    if spec.queue_depth is None:
        return QoSProfile(
            reliability=QoSReliabilityPolicy.RELIABLE,
            durability=durability,
            history=QoSHistoryPolicy.KEEP_ALL,
        )
    return QoSProfile(
        reliability=QoSReliabilityPolicy.RELIABLE,
        durability=durability,
        history=QoSHistoryPolicy.KEEP_LAST,
        depth=spec.queue_depth,
    )


def _stamp_ns(stamp: object) -> int:
    return stamp.sec * 1_000_000_000 + stamp.nanosec


def exact_cdr(raw: bytes, message: object) -> bytes:
    """``raw`` without DDS alignment padding.

    The padding is identified by the message itself: ``message`` is the
    decoded ``raw``, and the length of its canonical serialization is the
    publisher's byte length (CDR layout is deterministic in length). ``raw``
    is trimmed to that length only if what follows is 1-3 zero bytes.
    Anything else (already exact, a non-zero tail) is returned as is.

    Only the *length* of the re-serialization is used, never its bytes: the
    alignment padding inside a CDR message is uninitialized memory in
    ``serialize_message`` output, so a byte comparison would be
    nondeterministic. The forwarded bytes are always the received ones."""
    extra = len(raw) - len(serialize_message(message))
    if 0 < extra < 4 and not any(raw[-extra:]):
        return raw[:-extra]
    return raw


def read_source_timestamp_ns(spec: ChannelSpec, message: object) -> int:
    """The source timestamp the message carries, per the channel's rule.
    A missing or malformed value raises (KeyError / IndexError /
    JSONDecodeError / ...) -- never replaced by receive time."""
    if spec.timestamp is TimestampRule.HEADER:
        return _stamp_ns(message.header.stamp)
    if spec.timestamp is TimestampRule.TRANSFORM_HEADER:
        return _stamp_ns(message.transforms[0].header.stamp)
    if spec.timestamp is TimestampRule.JSON_FIELD:
        return int(json.loads(message.data)["source_timestamp_ns"])
    raise ValueError(f"unhandled timestamp rule {spec.timestamp!r}")


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
        registry: ChannelRegistry = DEFAULT_REGISTRY,
        publish_timeout_seconds: float = 5.0,
        producer_bridge: object | None = None,
        emit_lifecycle_events: bool = False,
    ) -> None:
        """``producer_bridge`` defaults to a real ``_AsyncProducerBridge``
        (real Kafka client). Tests inject a fake exposing the same
        ``publish(envelope)``/``close()`` shape instead, so envelope-
        construction logic is testable without Kafka and without
        constructing a real producer -- see
        ros2/nodes/tests/test_streaming_bridge_node.py.

        ``emit_lifecycle_events`` -- publish a ``RUN_START`` control event
        (``sceneops_core.streaming.control``) right after construction and a
        best-effort ``RUN_END`` during ``shutdown()``. Capture finalizes a run
        only on its ``RUN_END``, so ``main()`` always enables it. It defaults
        to ``False`` so tests that inject a fake bridge and assert exact
        published-envelope counts/indices/sequence numbers are unaffected."""
        super().__init__("streaming_bridge_node")
        self._robot_id = robot_id
        self._robot_run_id = robot_run_id
        self._emit_lifecycle_events = emit_lifecycle_events

        self._registry = registry

        # One monotonically increasing sequence per (robot_id,
        # robot_run_id) bridge TELEMETRY stream, across every registry
        # channel -- the transport sequence: bridge-observed arrival
        # order, never source-timestamp order. Capture carries it into the
        # recording as the MCAP sequence. No lock: the default rclpy
        # executor (spin_once, used by main() below) runs every
        # subscription callback sequentially on one thread, so callbacks
        # never execute concurrently with each other.
        self._sequence = 0

        # A SEPARATE, independent counter for lifecycle control events
        # (Phase 7.2.1) -- RUN_START/RUN_END never share telemetry's own
        # 0-start sequence space. Sharing it would collide: RUN_START
        # published before any telemetry would land at the exact same
        # sequence_number (0) the first REAL telemetry message also
        # needs, and RunScopedCapture's _SequenceTracker (one tracker,
        # one run) requires a clean 0..N-1 run with no two different
        # payloads at the same position. Keeping the two spaces
        # independent lets each be validated on its own terms: telemetry
        # keeps its existing, unchanged 0-start/no-gap contract
        # regardless of whether lifecycle events are enabled at all, and
        # control events get their OWN gap/duplicate/conflict validation
        # (capture_consumer.py's own second _SequenceTracker, Phase
        # 7.2.1) without perturbing it.
        self._lifecycle_sequence = 0

        self.published_count = 0
        self.failed_count = 0
        self.published_by_channel: dict[str, int] = {}
        self.failed_by_channel: dict[str, int] = {}
        # Monotonic time of the latest bridged message; None until the
        # first one (idle exit never fires before any traffic).
        self.last_message_monotonic: float | None = None

        if producer_bridge is None:
            settings = StreamingSettings()
            producer_bridge = _AsyncProducerBridge(
                settings=settings, publish_timeout_seconds=publish_timeout_seconds
            )
            bootstrap_servers, telemetry_topic = settings.bootstrap_servers, settings.telemetry_topic
        else:
            bootstrap_servers, telemetry_topic = "<injected>", "<injected>"
        self._bridge = producer_bridge

        if self._emit_lifecycle_events:
            self._publish_lifecycle_event(RunEventType.RUN_START)

        self._message_classes = {
            spec.topic: get_message(spec.message_type) for spec in registry
        }
        self._subscriptions = [
            self.create_subscription(
                self._message_classes[spec.topic],
                spec.topic,
                self._make_callback(spec),
                subscription_qos(spec),
                raw=True,
            )
            for spec in registry
        ]

        self.get_logger().info(
            f"streaming bridge ready: robot_id={robot_id} "
            f"robot_run_id={robot_run_id} topics={registry.topics()} "
            f"bootstrap_servers={bootstrap_servers} "
            f"topic={telemetry_topic}"
        )

    def _next_sequence_number(self) -> int:
        seq = self._sequence
        self._sequence += 1
        return seq

    def _next_lifecycle_sequence_number(self) -> int:
        seq = self._lifecycle_sequence
        self._lifecycle_sequence += 1
        return seq

    def _publish_lifecycle_event(self, event_type: RunEventType) -> None:
        """Best-effort -- a control event failing to publish must never
        crash bridge startup/shutdown or abort telemetry publishing. A RUN_END
        that fails to publish leaves the run unfinalizable by capture, which
        fails the capture rather than finalizing a recording of unknown
        completeness. Uses the SEPARATE, independent lifecycle sequence
        counter (``_next_lifecycle_sequence_number``) -- never the telemetry
        one -- so RUN_START always gets a clean 0 and RUN_END a clean 1,
        regardless of how many (or how few) telemetry messages were
        published in between; capture validates this stream's own 0..N-1
        completeness independently of telemetry's (capture_consumer.py's
        second
        ``_SequenceTracker``)."""
        try:
            envelope = build_control_envelope(
                event_type=event_type,
                robot_id=self._robot_id,
                robot_run_id=self._robot_run_id,
                sequence_number=self._next_lifecycle_sequence_number(),
            )
            self._bridge.publish(envelope)
        except Exception as exc:
            self.get_logger().error(
                f"failed to publish {event_type.value} lifecycle event: "
                f"{type(exc).__name__}: {exc}"
            )

    def _make_callback(self, spec: ChannelSpec) -> Callable[[bytes], None]:
        def _callback(raw: bytes) -> None:
            self._handle_message(spec, raw)

        return _callback

    def _handle_message(self, spec: ChannelSpec, raw: bytes) -> None:
        """``raw`` is the serialized CDR as received; it is forwarded
        without DDS alignment padding (``exact_cdr``) and otherwise
        unchanged."""
        # The transport sequence counts every message the bridge received,
        # including one it then fails to forward: a dropped message leaves
        # a sequence gap, which capture rejects, so a bridge failure can
        # never produce a recording that silently lacks a message.
        sequence_number = self._next_sequence_number()
        try:
            message = deserialize_message(raw, self._message_classes[spec.topic])
            source_timestamp_ns = read_source_timestamp_ns(spec, message)
            if source_timestamp_ns == 0 and not spec.allow_zero_stamp:
                raise ValueError(
                    "zero source timestamp (unstamped message) on a channel "
                    "that carries observations"
                )
            # ingest_timestamp_ns is deliberately left to
            # TelemetryEnvelope's own default_factory -- constructing the
            # envelope here IS accepting the message at the transport
            # boundary; the bridge captures no earlier, more-precise
            # acceptance instant, so there is exactly one
            # default-assignment point, not two. It is the transport's own
            # timestamp: distinct from the source timestamp (verbatim, may
            # be 0 on an allowed channel) and from capture's receive time.
            envelope = TelemetryEnvelope(
                robot_id=self._robot_id,
                robot_run_id=self._robot_run_id,
                channel=spec.topic,
                message_type=spec.message_type,
                source_timestamp_ns=source_timestamp_ns,
                sequence_number=sequence_number,
                encoding=EnvelopeEncoding.ROS2_CDR,
                payload=exact_cdr(bytes(raw), message),
            )
            self._bridge.publish(envelope)
            self.published_count += 1
            self.published_by_channel[spec.topic] = (
                self.published_by_channel.get(spec.topic, 0) + 1
            )
            self.last_message_monotonic = time.monotonic()
        except Exception as exc:
            # Fail loudly, never silently drop. No DLQ, no retry storage
            # -- those are reliability-boundary concerns, not this
            # bridge's. rclpy's node logger is NOT Python's stdlib
            # `logging` -- it has its own kwarg set (throttle_duration_sec,
            # once, ...) and rejects exc_info=True outright, so the
            # exception is formatted into the message text instead.
            self.failed_count += 1
            self.failed_by_channel[spec.topic] = (
                self.failed_by_channel.get(spec.topic, 0) + 1
            )
            self.last_message_monotonic = time.monotonic()
            self.get_logger().error(
                f"failed to bridge message on {spec.topic}: "
                f"{type(exc).__name__}: {exc} "
                f"(published={self.published_count} failed={self.failed_count})"
            )

    def summary(self) -> dict[str, object]:
        return {
            "published": self.published_count,
            "failed": self.failed_count,
            "published_by_channel": dict(sorted(self.published_by_channel.items())),
            "failed_by_channel": dict(sorted(self.failed_by_channel.items())),
        }

    def shutdown(self, timeout_seconds: float = 10.0) -> None:
        """Flush pending Kafka production and close the producer. Does
        NOT stop ROS2 spinning -- the caller (main()) stops spin_once
        first, then calls this: stop accepting new callbacks -> publish
        RUN_END (best-effort, if enabled) -> flush -> close producer ->
        destroy node. RUN_END is published BEFORE flush/close so it is
        handed to the SAME producer instance as every telemetry record,
        ordered after all of them in publish order -- never a separate,
        possibly-racing producer/connection.

        This is the GRACEFUL path only -- a hard kill (SIGKILL, crash)
        never reaches this method, so that run never gets a RUN_END and
        capture refuses to finalize it."""
        if self._emit_lifecycle_events:
            self._publish_lifecycle_event(RunEventType.RUN_END)
        self._bridge.close(timeout_seconds=timeout_seconds)


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Bridge ROS2 topics to the Kafka streaming transport"
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
        "--channels-file",
        type=Path,
        action="append",
        default=[],
        help=(
            "Channel-set JSON file adding channels to the built-in defaults "
            "(repeatable); see sceneops_core.streaming.channels"
        ),
    )
    parser.add_argument(
        "--publish-timeout-seconds",
        type=float,
        default=5.0,
        help="Bound on how long a single publish() may block the ROS2 callback thread",
    )
    parser.add_argument(
        "--exit-after-idle-seconds",
        type=float,
        default=None,
        help=(
            "Shut down gracefully (publishing RUN_END) once at least one "
            "message was bridged and none arrived for this many seconds. "
            "For finite sources such as a dataset replay; a live robot's "
            "bridge is stopped by signal instead. Default: never."
        ),
    )
    args = parser.parse_args()

    logging.basicConfig(
        level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s"
    )

    registry = build_channel_registry(args.channels_file)
    rclpy.init()
    node = StreamingBridgeNode(
        robot_id=args.robot_id,
        robot_run_id=args.robot_run_id,
        registry=registry,
        publish_timeout_seconds=args.publish_timeout_seconds,
        emit_lifecycle_events=True,
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
            idle_limit = args.exit_after_idle_seconds
            last = node.last_message_monotonic
            if (
                idle_limit is not None
                and last is not None
                and time.monotonic() - last >= idle_limit
            ):
                node.get_logger().info(
                    f"no message for {idle_limit}s after traffic -- beginning "
                    "graceful shutdown"
                )
                break
    finally:
        node.get_logger().info(
            f"shutting down -- published={node.published_count} "
            f"failed={node.failed_count}"
        )
        node.shutdown()
        # One machine-readable line for orchestration (per-channel counts).
        print(SUMMARY_PREFIX + json.dumps(node.summary(), sort_keys=True), flush=True)
        node.destroy_node()
        rclpy.shutdown()

    if node.failed_count > 0:
        raise SystemExit(1)


if __name__ == "__main__":
    main()
