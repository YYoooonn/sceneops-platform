"""Unit tests for mcap_writer.py: envelope -> L1 MCAP message mapping.

Runs only inside the capture image (needs the ROS 2 interface
definitions for schema text, rclpy for real payloads, and the mcap /
mcap-ros2-support packages for read-back verification).
"""

from __future__ import annotations

from pathlib import Path


import pytest
from mcap.reader import make_reader
from mcap_ros2.decoder import DecoderFactory
from rclpy.serialization import serialize_message
from sceneops_streaming import (
    EnvelopeEncoding,
    TelemetryEnvelope,
    build_channel_registry,
)
from sensor_msgs.msg import CompressedImage, PointCloud2
from std_msgs.msg import String

from sceneops_recording.capture.writer import McapCaptureWriter, UnsupportedChannelError  # noqa: E402

SURROUND = (
    Path(__file__).resolve().parents[2] / "channels" / "surround-camera-lidar.json"
)


def _envelope(**overrides) -> TelemetryEnvelope:
    fields = dict(
        robot_id="robot-1",
        robot_run_id="run-1",
        channel="/vehicle/odom",
        message_type="nav_msgs/msg/Odometry",
        source_timestamp_ns=1_000_000_000,
        ingest_timestamp_ns=2_000_000_000,
        sequence_number=0,
        encoding=EnvelopeEncoding.ROS2_CDR,
        payload=b"\x00\x01\x00\x00" + b"x" * 16,
    )
    fields.update(overrides)
    return TelemetryEnvelope(**fields)


def _read_all(mcap_path: str):
    with open(mcap_path, "rb") as f:
        reader = make_reader(f)
        return list(reader.iter_messages())


def test_write_envelope_registers_correct_topic_and_schema(tmp_path) -> None:
    writer = McapCaptureWriter(bag_uri=str(tmp_path / "bag"))
    writer.write_envelope(_envelope())
    writer.close()

    [(schema, channel, _message)] = _read_all(writer.mcap_file_path())
    assert channel.topic == "/vehicle/odom"
    assert schema.name == "nav_msgs/msg/Odometry"
    assert schema.encoding == "ros2msg"
    assert channel.message_encoding == "cdr"
    assert "MSG: geometry_msgs/PoseWithCovariance" in schema.data.decode()


def test_log_time_is_receive_time_not_source_time(tmp_path) -> None:
    writer = McapCaptureWriter(bag_uri=str(tmp_path / "bag"))
    writer.write_envelope(
        _envelope(source_timestamp_ns=1_111_000_000, ingest_timestamp_ns=2_222_000_000),
        receive_time_ns=3_333_000_000,
    )
    writer.close()

    [(_s, _c, message)] = _read_all(writer.mcap_file_path())
    assert message.log_time == 3_333_000_000  # capture receive time
    assert message.publish_time == 2_222_000_000  # bridge ingest time
    assert 1_111_000_000 not in (message.log_time, message.publish_time)


def test_zero_stamped_static_transform_is_recorded_unchanged(tmp_path) -> None:
    """The zero source stamp stays zero in the payload; the recording's
    three timing facts stay distinct (source 0 in the payload, publish_time
    the transport ingest time, log_time the receive time)."""
    from geometry_msgs.msg import TransformStamped
    from tf2_msgs.msg import TFMessage

    transform = TransformStamped()
    transform.header.frame_id = "base_link"
    transform.child_frame_id = "cam_front"
    transform.transform.rotation.w = 1.0
    payload = serialize_message(TFMessage(transforms=[transform]))
    writer = McapCaptureWriter(bag_uri=str(tmp_path / "bag"))
    writer.write_envelope(
        _envelope(
            channel="/tf_static",
            message_type="tf2_msgs/msg/TFMessage",
            source_timestamp_ns=0,
            ingest_timestamp_ns=2_222_000_000,
            payload=payload,
        ),
        receive_time_ns=3_333_000_000,
    )
    writer.close()

    with open(writer.mcap_file_path(), "rb") as f:
        reader = make_reader(f, decoder_factories=[DecoderFactory()])
        [decoded] = list(reader.iter_decoded_messages())
    assert decoded.message.data == payload
    stamp = decoded.decoded_message.transforms[0].header.stamp
    assert (stamp.sec, stamp.nanosec) == (0, 0)
    assert (decoded.message.publish_time, decoded.message.log_time) == (
        2_222_000_000,
        3_333_000_000,
    )


def test_receive_time_defaults_to_the_wall_clock(tmp_path) -> None:
    import time

    writer = McapCaptureWriter(bag_uri=str(tmp_path / "bag"))
    before = time.time_ns()
    writer.write_envelope(_envelope(source_timestamp_ns=5))
    after = time.time_ns()
    writer.close()

    [(_s, _c, message)] = _read_all(writer.mcap_file_path())
    assert before <= message.log_time <= after


def test_log_time_never_decreases_in_write_order(tmp_path) -> None:
    writer = McapCaptureWriter(bag_uri=str(tmp_path / "bag"))
    for sequence, receive in enumerate([100, 300, 200, 400]):
        writer.write_envelope(
            _envelope(sequence_number=sequence), receive_time_ns=receive
        )
    writer.close()

    log_times = [m.log_time for _s, _c, m in _read_all(writer.mcap_file_path())]
    assert log_times == [100, 300, 300, 400]


def test_envelope_sequence_is_preserved_as_mcap_sequence(tmp_path) -> None:
    """MCAP reserves sequence 0 for "none", so the 0-based transport
    counter is stored plus one."""
    writer = McapCaptureWriter(bag_uri=str(tmp_path / "bag"))
    for sequence in (0, 1, 5, 6):
        writer.write_envelope(_envelope(sequence_number=sequence))
    writer.close()

    assert [m.sequence for _s, _c, m in _read_all(writer.mcap_file_path())] == [
        1,
        2,
        6,
        7,
    ]


def test_raw_cdr_payload_bytes_are_preserved_exactly(tmp_path) -> None:
    payload = bytes(range(256)) * 4  # exercise every byte value, including 0x00/0xFF
    writer = McapCaptureWriter(bag_uri=str(tmp_path / "bag"))
    writer.write_envelope(_envelope(payload=payload))
    writer.close()

    [(_s, _c, message)] = _read_all(writer.mcap_file_path())
    assert message.data == payload


def test_source_duplicates_are_recorded_as_separate_occurrences(tmp_path) -> None:
    payload = serialize_message(String(data="same"))
    writer = McapCaptureWriter(bag_uri=str(tmp_path / "bag"))
    for sequence in (0, 1):
        writer.write_envelope(
            _envelope(
                channel="/mission/status",
                message_type="std_msgs/msg/String",
                payload=payload,
                sequence_number=sequence,
            )
        )
    writer.close()

    messages = _read_all(writer.mcap_file_path())
    assert [m.data for _s, _c, m in messages] == [payload, payload]
    assert [m.sequence for _s, _c, m in messages] == [1, 2]


def test_one_channel_definition_per_topic_with_late_channel_arrival(tmp_path) -> None:
    """Channels arrive asynchronously: each topic gets exactly one
    channel the first time it is seen, whenever that is."""
    registry = build_channel_registry([SURROUND])
    image = serialize_message(CompressedImage(format="jpeg", data=b"\xff\xd8\xff"))
    cloud = serialize_message(PointCloud2())
    writer = McapCaptureWriter(bag_uri=str(tmp_path / "bag"), registry=registry)
    sequence = iter(range(100))
    for channel, message_type, payload in [
        ("/lidar/top/points", "sensor_msgs/msg/PointCloud2", cloud),
        ("/camera/front/image/compressed", "sensor_msgs/msg/CompressedImage", image),
        ("/lidar/top/points", "sensor_msgs/msg/PointCloud2", cloud),
        ("/camera/front/image/compressed", "sensor_msgs/msg/CompressedImage", image),
    ]:
        writer.write_envelope(
            _envelope(
                channel=channel,
                message_type=message_type,
                payload=payload,
                sequence_number=next(sequence),
            )
        )
    writer.close()

    with open(writer.mcap_file_path(), "rb") as f:
        summary = make_reader(f).get_summary()
    assert sorted(c.topic for c in summary.channels.values()) == [
        "/camera/front/image/compressed",
        "/lidar/top/points",
    ]
    assert len(_read_all(writer.mcap_file_path())) == 4


def test_unsupported_channel_raises_and_does_not_write(tmp_path) -> None:
    writer = McapCaptureWriter(bag_uri=str(tmp_path / "bag"))
    with pytest.raises(UnsupportedChannelError):
        writer.write_envelope(
            _envelope(channel="/not/registered", message_type="std_msgs/msg/String")
        )
    assert writer.stats.message_count == 0


def test_sensor_channel_is_unsupported_without_its_channel_file(tmp_path) -> None:
    writer = McapCaptureWriter(bag_uri=str(tmp_path / "bag"))
    with pytest.raises(UnsupportedChannelError):
        writer.write_envelope(
            _envelope(
                channel="/lidar/top/points", message_type="sensor_msgs/msg/PointCloud2"
            )
        )


def test_known_channel_with_mismatched_type_raises(tmp_path) -> None:
    writer = McapCaptureWriter(bag_uri=str(tmp_path / "bag"))
    with pytest.raises(UnsupportedChannelError):
        writer.write_envelope(
            _envelope(channel="/vehicle/odom", message_type="std_msgs/msg/String")
        )
    assert writer.stats.message_count == 0


def test_message_count_and_per_channel_counts_tracked(tmp_path) -> None:
    writer = McapCaptureWriter(bag_uri=str(tmp_path / "bag"))
    writer.write_envelope(_envelope(channel="/vehicle/odom", sequence_number=0))
    writer.write_envelope(_envelope(channel="/vehicle/odom", sequence_number=1))
    writer.write_envelope(
        _envelope(
            channel="/vehicle/imu",
            message_type="sensor_msgs/msg/Imu",
            sequence_number=2,
        )
    )
    writer.close()

    assert writer.stats.message_count == 3
    assert writer.stats.per_channel_counts == {"/vehicle/odom": 2, "/vehicle/imu": 1}


def test_close_is_idempotent_and_leaves_a_finished_mcap(tmp_path) -> None:
    writer = McapCaptureWriter(bag_uri=str(tmp_path / "bag"))
    writer.write_envelope(_envelope())
    writer.close()
    writer.close()

    with open(writer.mcap_file_path(), "rb") as f:
        assert make_reader(f).get_summary().statistics.message_count == 1


def test_mcap_file_path_uses_bag_name_convention(tmp_path) -> None:
    bag_dir = tmp_path / "my-run"
    writer = McapCaptureWriter(bag_uri=str(bag_dir))
    assert writer.mcap_file_path() == str(bag_dir / "my-run_0.mcap")
    writer.close()


def test_embedded_schema_decodes_a_payload_written_through_the_writer(tmp_path) -> None:
    writer = McapCaptureWriter(bag_uri=str(tmp_path / "bag"))
    writer.write_envelope(
        _envelope(
            channel="/mission/status",
            message_type="std_msgs/msg/String",
            payload=serialize_message(String(data='{"k": 1}')),
        )
    )
    writer.close()

    with open(writer.mcap_file_path(), "rb") as f:
        reader = make_reader(f, decoder_factories=[DecoderFactory()])
        [decoded] = list(reader.iter_decoded_messages())
    assert decoded.decoded_message.data == '{"k": 1}'
