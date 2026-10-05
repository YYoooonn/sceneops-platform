"""Replay sink: acquisition events -> timed ROS 2 publication
(ADR-007 §29.13, streaming mode).

The sink plays the robot's own stack. It publishes each event's payload --
the CDR bytes the adapter serialized once -- on its topic, at the pace the
source timeline sets, scaled by ``rate``. The platform's ROS 2 bridge
subscribes to those topics exactly as it would to a live robot. The batch
MCAP sink and this one therefore emit identical payload bytes.

* Payloads are published *raw* (``publisher.publish(bytes)``). Nothing is
  deserialized or reserialized, and no timestamp is touched: source
  observation times stay inside the payload, so replay pacing and
  transport latency can never leak into them (R4, R11).
* Pacing uses the event's ``source_time_ns`` only as a *schedule*: event
  ``e`` is published ``(e.source_time - first.source_time) / rate`` after
  the start. ``rate <= 0`` publishes without pacing.
* Every publisher is created, and every subscriber matched, before the
  first message goes out. If a topic still has no subscriber after
  ``wait_subscribers_seconds`` the replay fails: it never publishes into
  the void and calls that a replay.
* Delivery is reliable with unbounded sender history, so a slow subscriber
  delays acknowledgement instead of losing samples. After the last
  message the sink waits for every sample to be acknowledged and fails if
  any is not.
* ``/tf_static`` is latched (transient-local), the ROS 2 convention for
  static transforms, so a subscriber that joins late still receives it.

The module imports ``rclpy`` only inside :func:`ros2_publishers`, so the
scheduling logic is testable without a ROS 2 runtime. It runs in the replay
image (ROS 2 + this tool, no SceneOps package).
"""

from __future__ import annotations

import time
from collections.abc import Callable, Iterator
from contextlib import contextmanager
from dataclasses import dataclass, field
from typing import Protocol

from .events import AcquisitionError, AcquisitionEvent, DatasetAdapter, MessageType

LATCHED_TOPICS = frozenset({"/tf_static"})
LATCHED_DEPTH = 100


class TopicPublisher(Protocol):
    def publish(self, payload: bytes) -> None: ...

    def subscription_count(self) -> int: ...

    def wait_for_all_acked(self, timeout_seconds: float) -> bool: ...


PublisherFactory = Callable[[str, MessageType, bool], TopicPublisher]


@dataclass
class ReplaySummary:
    message_count: int = 0
    topic_counts: dict[str, int] = field(default_factory=dict)
    rate: float = 1.0
    source_span_ns: int = 0
    elapsed_seconds: float = 0.0
    # Largest amount a message was published later than its schedule.
    max_lag_seconds: float = 0.0
    largest_payload_bytes: int = 0

    def to_dict(self) -> dict[str, object]:
        return {
            "message_count": self.message_count,
            "topic_counts": dict(sorted(self.topic_counts.items())),
            "rate": self.rate,
            "source_span_ns": self.source_span_ns,
            "elapsed_seconds": round(self.elapsed_seconds, 3),
            "max_lag_seconds": round(self.max_lag_seconds, 3),
            "largest_payload_bytes": self.largest_payload_bytes,
        }


def _wait_for_subscribers(
    publishers: dict[str, TopicPublisher],
    timeout_seconds: float,
    clock: Callable[[], float],
    sleep: Callable[[float], None],
) -> None:
    deadline = clock() + timeout_seconds
    while True:
        unmatched = sorted(
            topic for topic, p in publishers.items() if p.subscription_count() < 1
        )
        if not unmatched:
            return
        if clock() >= deadline:
            raise AcquisitionError(
                f"no subscriber after {timeout_seconds}s on: {', '.join(unmatched)}"
            )
        sleep(0.1)


def replay(
    adapter: DatasetAdapter,
    publisher_factory: PublisherFactory,
    *,
    rate: float = 1.0,
    wait_subscribers_seconds: float = 30.0,
    ack_timeout_seconds: float = 120.0,
    clock: Callable[[], float] = time.monotonic,
    sleep: Callable[[float], None] = time.sleep,
) -> ReplaySummary:
    """Publish ``adapter``'s events in order, paced by their source time."""
    channels = adapter.channels()
    if not channels:
        raise AcquisitionError("the selected source produced no channels")
    publishers = {
        topic: publisher_factory(topic, message_type, topic in LATCHED_TOPICS)
        for topic, message_type in channels.items()
    }
    _wait_for_subscribers(publishers, wait_subscribers_seconds, clock, sleep)

    summary = ReplaySummary(rate=rate)
    events: Iterator[AcquisitionEvent] = adapter.events()
    start = clock()
    first_ns: int | None = None
    last_ns = 0
    for event in events:
        publisher = publishers.get(event.topic)
        if publisher is None:
            raise AcquisitionError(f"event on undeclared channel {event.topic!r}")
        if first_ns is None:
            first_ns = event.source_time_ns
        if event.source_time_ns < last_ns:
            raise AcquisitionError(
                f"events out of acquisition order on {event.topic!r}: "
                f"{event.source_time_ns} < {last_ns}"
            )
        last_ns = event.source_time_ns
        if rate > 0:
            due = start + (event.source_time_ns - first_ns) / 1e9 / rate
            now = clock()
            if due > now:
                sleep(due - now)
            else:
                summary.max_lag_seconds = max(summary.max_lag_seconds, now - due)
        publisher.publish(event.payload)
        summary.message_count += 1
        summary.topic_counts[event.topic] = summary.topic_counts.get(event.topic, 0) + 1
        summary.largest_payload_bytes = max(
            summary.largest_payload_bytes, len(event.payload)
        )

    if summary.message_count == 0:
        raise AcquisitionError("the selected source produced no events")
    unacked = sorted(
        topic
        for topic, p in publishers.items()
        if not p.wait_for_all_acked(ack_timeout_seconds)
    )
    if unacked:
        raise AcquisitionError(
            f"samples not acknowledged within {ack_timeout_seconds}s on: "
            f"{', '.join(unacked)}"
        )
    summary.source_span_ns = last_ns - (first_ns or 0)
    summary.elapsed_seconds = clock() - start
    return summary


@contextmanager
def ros2_publishers(node_name: str = "dataset_replay") -> Iterator[PublisherFactory]:
    """A :data:`PublisherFactory` backed by rclpy, with its node and context
    torn down on exit. Raises :class:`AcquisitionError` when no ROS 2
    runtime is available (run the replay image)."""
    try:
        import rclpy
        from rclpy.duration import Duration
        from rclpy.qos import (
            QoSDurabilityPolicy,
            QoSHistoryPolicy,
            QoSProfile,
            QoSReliabilityPolicy,
        )
        from rosidl_runtime_py.utilities import get_message
    except ImportError as exc:
        raise AcquisitionError(
            "replay needs a ROS 2 runtime (rclpy); use the dataset-replay image"
        ) from exc

    class _RclpyPublisher:
        def __init__(self, publisher: object) -> None:
            self._publisher = publisher

        def publish(self, payload: bytes) -> None:
            self._publisher.publish(payload)

        def subscription_count(self) -> int:
            return self._publisher.get_subscription_count()

        def wait_for_all_acked(self, timeout_seconds: float) -> bool:
            return bool(
                self._publisher.wait_for_all_acked(Duration(seconds=timeout_seconds))
            )

    rclpy.init()
    node = rclpy.create_node(node_name)

    def factory(topic: str, message_type: MessageType, latched: bool) -> TopicPublisher:
        if message_type.encoding != "ros2msg":
            raise AcquisitionError(
                f"{topic!r}: only ros2msg message types can be replayed, got "
                f"{message_type.encoding!r}"
            )
        interface = get_message(message_type.name)
        if latched:
            qos = QoSProfile(
                reliability=QoSReliabilityPolicy.RELIABLE,
                durability=QoSDurabilityPolicy.TRANSIENT_LOCAL,
                history=QoSHistoryPolicy.KEEP_LAST,
                depth=LATCHED_DEPTH,
            )
        else:
            qos = QoSProfile(
                reliability=QoSReliabilityPolicy.RELIABLE,
                durability=QoSDurabilityPolicy.VOLATILE,
                history=QoSHistoryPolicy.KEEP_ALL,
            )
        return _RclpyPublisher(node.create_publisher(interface, topic, qos))

    try:
        yield factory
    finally:
        node.destroy_node()
        rclpy.shutdown()
