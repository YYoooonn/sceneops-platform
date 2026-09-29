"""Unit tests for mcap_writer.py: envelope -> MCAP message mapping.

Runs only inside the ros2 container (needs rosbag2_py + the mcap
storage plugin, apt-installed there, plus the mcap Python package for
read-back verification).
"""

from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import pytest
from mcap.reader import make_reader
from sceneops_core.streaming import EnvelopeEncoding, TelemetryEnvelope

from mcap_writer import McapCaptureWriter, UnsupportedChannelError  # noqa: E402


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

    messages = _read_all(writer.mcap_file_path())
    assert len(messages) == 1
    schema, channel, _message = messages[0]
    assert channel.topic == "/vehicle/odom"
    assert schema.name == "nav_msgs/msg/Odometry"
    assert channel.message_encoding == "cdr"


def test_log_time_is_source_timestamp_and_publish_time_is_ingest_timestamp(
    tmp_path,
) -> None:
    writer = McapCaptureWriter(bag_uri=str(tmp_path / "bag"))
    writer.write_envelope(
        _envelope(source_timestamp_ns=1_111_000_000, ingest_timestamp_ns=2_222_000_000)
    )
    writer.close()

    _schema, _channel, message = _read_all(writer.mcap_file_path())[0]
    assert message.log_time == 1_111_000_000
    assert message.publish_time == 2_222_000_000


def test_raw_cdr_payload_bytes_are_preserved_exactly(tmp_path) -> None:
    payload = bytes(range(256)) * 4  # exercise every byte value, including 0x00/0xFF
    writer = McapCaptureWriter(bag_uri=str(tmp_path / "bag"))
    writer.write_envelope(_envelope(payload=payload))
    writer.close()

    _schema, _channel, message = _read_all(writer.mcap_file_path())[0]
    assert message.data == payload


def test_unsupported_channel_raises_and_does_not_write(tmp_path) -> None:
    writer = McapCaptureWriter(bag_uri=str(tmp_path / "bag"))
    with pytest.raises(UnsupportedChannelError):
        writer.write_envelope(
            _envelope(channel="/not/registered", message_type="std_msgs/msg/String")
        )
    assert writer.stats.message_count == 0


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


def test_mcap_file_path_uses_rosbag2_bag_name_convention(tmp_path) -> None:
    bag_dir = tmp_path / "my-run"
    writer = McapCaptureWriter(bag_uri=str(bag_dir))
    assert writer.mcap_file_path() == str(bag_dir / "my-run_0.mcap")
