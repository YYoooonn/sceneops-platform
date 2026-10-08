"""MCAP source: everything an event carries comes from the recording itself.

Uses hand-built MCAPs (so chunking and equal timestamps are controlled) and a
batch recording written by the sink from the synthetic nuScenes adapter.
"""

from __future__ import annotations

import tracemalloc
from pathlib import Path

import pytest
from mcap.writer import Writer
from synthetic_nuscenes import UNIT, VERSION
from test_ros2_replay import FakeClock, make

from dataset_acquisition import ros2
from dataset_acquisition.events import AcquisitionError
from dataset_acquisition.mcap_sink import write_mcap
from dataset_acquisition.mcap_source import McapAdapter
from dataset_acquisition.nuscenes import NuScenesAdapter, NuScenesSelection
from dataset_acquisition.ros2_replay import replay

STRING_DEFINITION = "string data\n"


def write_raw(
    path: Path,
    messages: list[tuple[str, int, bytes]],
    *,
    chunk_size: int = 1 << 20,
    chunked: bool = True,
    message_encoding: str = "cdr",
    schema: bool = True,
    declare_unused_channel: bool = False,
) -> Path:
    """An MCAP whose records are written in exactly the given order."""
    with path.open("wb") as stream:
        writer = Writer(stream, chunk_size=chunk_size, use_chunking=chunked)
        writer.start(profile="ros2", library="test")
        schema_id = (
            writer.register_schema("std_msgs/msg/String", "ros2msg", b"string data\n")
            if schema
            else 0
        )
        channels: dict[str, int] = {}
        if declare_unused_channel:
            writer.register_channel("/unused", message_encoding, schema_id)
        for sequence, (topic, log_time, payload) in enumerate(messages):
            if topic not in channels:
                channels[topic] = writer.register_channel(
                    topic, message_encoding, schema_id
                )
            writer.add_message(
                channels[topic],
                log_time=log_time,
                publish_time=log_time,
                data=payload,
                sequence=sequence,
            )
        writer.finish()
    return path


def order(adapter: McapAdapter) -> list[tuple[str, int, bytes]]:
    return [(e.topic, e.source_time_ns, e.payload) for e in adapter.events()]


def test_channels_and_event_fields_are_derived_from_the_mcap(tmp_path: Path) -> None:
    path = write_raw(
        tmp_path / "a.mcap",
        [("/a", 10, b"p1"), ("/b", 20, b"p2")],
        declare_unused_channel=True,
    )
    adapter = McapAdapter(path)

    channels = adapter.channels()
    assert set(channels) == {"/a", "/b"}  # a channel without messages is not declared
    for message_type in channels.values():
        assert message_type.name == "std_msgs/msg/String"
        assert message_type.encoding == "ros2msg"
        assert message_type.definition == STRING_DEFINITION
    events = list(adapter.events())
    assert [(e.topic, e.source_time_ns, e.payload, e.sequence) for e in events] == [
        ("/a", 10, b"p1", 0),
        ("/b", 20, b"p2", 1),
    ]
    assert {e.message_encoding for e in events} == {"cdr"}
    assert all(e.message_type is channels[e.topic] for e in events)


def test_a_batch_recording_gives_back_the_events_that_were_written(
    dataroot: Path, tmp_path: Path
) -> None:
    source = NuScenesAdapter(
        NuScenesSelection(dataroot=dataroot, version=VERSION, source_unit=UNIT)
    )
    written = list(source.events())
    path = tmp_path / "batch.mcap"
    write_mcap(written, path, origin=source.origin())

    adapter = McapAdapter(path)
    assert adapter.channels() == dict(source.channels())
    assert list(adapter.events()) == [
        # The sink writes a missing sequence as 0.
        type(e)(
            e.topic,
            e.message_type,
            e.message_encoding,
            e.payload,
            e.source_time_ns,
            e.sequence or 0,
        )
        for e in written
    ]
    assert dict(adapter.origin()) == dict(source.origin())


def test_equal_timestamps_keep_file_order_within_and_across_chunks(
    tmp_path: Path,
) -> None:
    """/tf_static and the first /tf share one instant; their order, and that
    of every other same-instant message, is the order they were written in."""
    messages = [
        ("/tf_static", 100, b"static"),
        ("/tf", 100, b"tf-first"),
        ("/imu", 100, b"imu"),
        ("/tf", 100, b"tf-second"),
        ("/tf", 200, b"tf-later"),
    ]
    for chunk_size in (1 << 20, 1):  # one chunk, and one chunk per message
        path = write_raw(
            tmp_path / f"ties-{chunk_size}.mcap", messages, chunk_size=chunk_size
        )
        assert order(McapAdapter(path)) == messages


def test_an_unsorted_recording_is_ordered_by_log_time_with_file_order_ties(
    tmp_path: Path,
) -> None:
    path = write_raw(
        tmp_path / "unsorted.mcap",
        [("/x", 300, b"c"), ("/x", 100, b"a1"), ("/y", 200, b"b"), ("/x", 100, b"a2")],
        chunk_size=1,
    )
    assert [p for _, _, p in order(McapAdapter(path))] == [b"a1", b"a2", b"b", b"c"]


def test_replay_publishes_the_recording_bytes_in_order_and_latches_tf_static(
    tmp_path: Path,
) -> None:
    messages = [
        ("/tf_static", 100, b"static"),
        ("/tf", 100, b"tf-first"),
        ("/tf", 150, b"tf-second"),
    ]
    adapter = McapAdapter(write_raw(tmp_path / "r.mcap", messages))
    clock = FakeClock()
    factory, publishers = make(clock)

    summary = replay(adapter, factory, rate=2, clock=clock.clock, sleep=clock.sleep)

    assert {t: [p for _, p in pub.published] for t, pub in publishers.items()} == {
        "/tf_static": [b"static"],
        "/tf": [b"tf-first", b"tf-second"],
    }
    assert publishers["/tf_static"].latched and not publishers["/tf"].latched
    # Pacing follows log_time: 50 ns of source time at rate 2.
    assert clock.sleeps == [pytest.approx(25e-9)]
    assert summary.message_count == 3 and summary.source_span_ns == 50


def test_a_payload_is_never_decoded_or_rewritten(tmp_path: Path) -> None:
    payload = bytes(
        ros2.event(
            "/s", ros2.make("std_msgs/msg/String", data="é"), source_time_ns=0
        ).payload
    )
    path = write_raw(tmp_path / "p.mcap", [("/s", 7, payload)])
    (event,) = McapAdapter(path).events()
    assert event.payload == payload and event.source_time_ns == 7


@pytest.mark.parametrize(
    ("options", "message"),
    [
        ({"chunked": False}, "no chunk index"),
        ({"message_encoding": "json"}, "'cdr'"),
        ({"schema": False}, "no schema"),
    ],
)
def test_a_recording_that_cannot_be_streamed_or_replayed_is_refused(
    tmp_path: Path, options: dict, message: str
) -> None:
    path = write_raw(tmp_path / "bad.mcap", [("/a", 1, b"x")], **options)
    with pytest.raises(AcquisitionError, match=message):
        McapAdapter(path).channels()


def test_an_unfinalized_recording_is_refused(tmp_path: Path) -> None:
    good = write_raw(tmp_path / "good.mcap", [("/a", 1, b"x")])
    truncated = tmp_path / "truncated.mcap"
    truncated.write_bytes(good.read_bytes()[:-60])
    with pytest.raises(AcquisitionError):
        McapAdapter(truncated).channels()


def test_reading_streams_instead_of_materializing_the_recording(
    tmp_path: Path,
) -> None:
    blob = bytes(200_000)
    count = 100  # ~20 MB of payload in ~1 MiB chunks
    path = write_raw(tmp_path / "big.mcap", [("/big", i, blob) for i in range(count)])
    total = count * len(blob)
    adapter = McapAdapter(path)
    tracemalloc.start()
    try:
        seen = sum(1 for _ in adapter.events())
        _, peak = tracemalloc.get_traced_memory()
    finally:
        tracemalloc.stop()
    assert seen == count
    assert peak < total / 3
