"""Replay sink: scheduling, readiness, lossless-publication guarantees.

The rclpy transport is faked, so these run without a ROS 2 runtime; the
real publisher path is exercised by the containerized streaming vertical.
"""

from __future__ import annotations

from pathlib import Path

import pytest
from synthetic_nuscenes import UNIT, VERSION

from dataset_acquisition.events import AcquisitionError, AcquisitionEvent, MessageType
from dataset_acquisition.nuscenes import NuScenesAdapter, NuScenesSelection
from dataset_acquisition.ros2_replay import LATCHED_TOPICS, replay

STRING = MessageType("std_msgs/msg/String", "ros2msg", "string data\n")


class FakeClock:
    def __init__(self) -> None:
        self.now = 0.0
        self.sleeps: list[float] = []

    def clock(self) -> float:
        return self.now

    def sleep(self, seconds: float) -> None:
        self.sleeps.append(seconds)
        self.now += seconds


class FakePublisher:
    def __init__(
        self, topic: str, latched: bool, clock: FakeClock, *, subscribers=1, acked=True
    ):
        self.topic, self.latched, self._clock = topic, latched, clock
        self.subscribers, self.acked = subscribers, acked
        self.published: list[tuple[float, bytes]] = []

    def publish(self, payload: bytes) -> None:
        self.published.append((self._clock.now, payload))

    def subscription_count(self) -> int:
        return self.subscribers

    def wait_for_all_acked(self, timeout_seconds: float) -> bool:
        return self.acked


class FakeAdapter:
    def __init__(self, events: list[AcquisitionEvent]) -> None:
        self._events = events

    def origin(self):
        return {}

    def channels(self):
        return {e.topic: STRING for e in self._events}

    def events(self):
        return iter(self._events)


def event(topic: str, payload: bytes, t_ns: int) -> AcquisitionEvent:
    return AcquisitionEvent(topic, STRING, "cdr", payload, source_time_ns=t_ns)


def make(clock: FakeClock, **options):
    publishers: dict[str, FakePublisher] = {}

    def factory(topic, message_type, latched):
        publishers[topic] = FakePublisher(topic, latched, clock, **options)
        return publishers[topic]

    return factory, publishers


def test_events_are_published_in_order_on_the_source_schedule() -> None:
    clock = FakeClock()
    factory, publishers = make(clock)
    events = [
        event("/a", b"1", 10_000_000_000),
        event("/b", b"2", 10_500_000_000),
        event("/a", b"3", 12_000_000_000),
    ]

    summary = replay(
        FakeAdapter(events), factory, rate=2.0, clock=clock.clock, sleep=clock.sleep
    )

    assert [t for t, _ in publishers["/a"].published] == [0.0, 1.0]
    assert [t for t, _ in publishers["/b"].published] == [0.25]
    assert [p for _, p in publishers["/a"].published] == [b"1", b"3"]
    assert summary.message_count == 3
    assert summary.topic_counts == {"/a": 2, "/b": 1}
    assert summary.source_span_ns == 2_000_000_000


def test_zero_rate_publishes_without_pacing() -> None:
    clock = FakeClock()
    factory, publishers = make(clock)
    events = [event("/a", b"1", 0), event("/a", b"2", 60_000_000_000)]

    replay(FakeAdapter(events), factory, rate=0, clock=clock.clock, sleep=clock.sleep)

    assert clock.sleeps == []
    assert len(publishers["/a"].published) == 2


def test_payload_bytes_are_published_unchanged_and_duplicates_kept() -> None:
    clock = FakeClock()
    factory, publishers = make(clock)
    payload = bytes(range(256))

    replay(
        FakeAdapter([event("/a", payload, 5), event("/a", payload, 5)]),
        factory,
        rate=0,
        clock=clock.clock,
        sleep=clock.sleep,
    )

    assert [p for _, p in publishers["/a"].published] == [payload, payload]


def test_only_tf_static_is_latched() -> None:
    clock = FakeClock()
    factory, publishers = make(clock)
    events = [event("/tf_static", b"s", 1), event("/tf", b"t", 1), event("/x", b"x", 2)]

    replay(FakeAdapter(events), factory, rate=0, clock=clock.clock, sleep=clock.sleep)

    assert LATCHED_TOPICS == {"/tf_static"}
    assert {t: p.latched for t, p in publishers.items()} == {
        "/tf_static": True,
        "/tf": False,
        "/x": False,
    }


def test_no_subscriber_fails_before_publishing_anything() -> None:
    clock = FakeClock()
    factory, publishers = make(clock, subscribers=0)

    with pytest.raises(AcquisitionError, match="no subscriber after 5s on: /a, /b"):
        replay(
            FakeAdapter([event("/a", b"1", 1), event("/b", b"2", 2)]),
            factory,
            wait_subscribers_seconds=5,
            clock=clock.clock,
            sleep=clock.sleep,
        )

    assert all(p.published == [] for p in publishers.values())


def test_unacknowledged_samples_fail_the_replay() -> None:
    clock = FakeClock()
    factory, _ = make(clock, acked=False)

    with pytest.raises(AcquisitionError, match="not acknowledged"):
        replay(
            FakeAdapter([event("/a", b"1", 1)]),
            factory,
            rate=0,
            clock=clock.clock,
            sleep=clock.sleep,
        )


def test_out_of_order_events_fail() -> None:
    clock = FakeClock()
    factory, _ = make(clock)

    with pytest.raises(AcquisitionError, match="out of acquisition order"):
        replay(
            FakeAdapter([event("/a", b"1", 10), event("/a", b"2", 5)]),
            factory,
            rate=0,
            clock=clock.clock,
            sleep=clock.sleep,
        )


def test_empty_source_fails() -> None:
    clock = FakeClock()
    factory, _ = make(clock)

    with pytest.raises(AcquisitionError):
        replay(FakeAdapter([]), factory, clock=clock.clock, sleep=clock.sleep)


def test_replay_publishes_exactly_the_batch_sink_payloads(dataroot: Path) -> None:
    """The nuScenes adapter feeds both sinks one event stream: replay
    publishes the same payload bytes, per topic and in order, that the
    batch sink writes."""
    adapter = NuScenesAdapter(
        NuScenesSelection(dataroot=dataroot, version=VERSION, source_unit=UNIT)
    )
    clock = FakeClock()
    factory, publishers = make(clock)

    summary = replay(adapter, factory, rate=0, clock=clock.clock, sleep=clock.sleep)

    expected: dict[str, list[bytes]] = {}
    for e in adapter.events():
        expected.setdefault(e.topic, []).append(e.payload)
    assert {
        t: [p for _, p in pub.published] for t, pub in publishers.items()
    } == expected
    assert set(adapter.channels()) == set(expected)
    assert summary.message_count == sum(len(v) for v in expected.values())
    assert adapter.channels()["/tf_static"].name == "tf2_msgs/msg/TFMessage"
