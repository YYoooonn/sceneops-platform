"""Unit tests for validation.py: pre-finalize MCAP read-back validation.

Runs only inside the ros2 container (needs the ros2 image to produce a real
MCAP fixture, plus the mcap Python package to validate it).
"""

from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import pytest
from sceneops_core.streaming import EnvelopeEncoding, TelemetryEnvelope

from mcap_writer import McapCaptureWriter  # noqa: E402
from validation import McapValidationError, validate_mcap_file  # noqa: E402


def _write_fixture_bag(tmp_path, *, message_count: int) -> str:
    writer = McapCaptureWriter(bag_uri=str(tmp_path / "bag"))
    for i in range(message_count):
        writer.write_envelope(
            TelemetryEnvelope(
                robot_id="robot-1",
                robot_run_id="run-1",
                channel="/vehicle/odom",
                message_type="nav_msgs/msg/Odometry",
                source_timestamp_ns=1_000_000_000 + i,
                ingest_timestamp_ns=2_000_000_000 + i,
                sequence_number=i,
                encoding=EnvelopeEncoding.ROS2_CDR,
                payload=bytes([i]) * 8,
            ),
            receive_time_ns=1_000_000_000 + i,
        )
    writer.close()
    return writer.mcap_file_path()


def test_validate_succeeds_for_matching_message_count(tmp_path) -> None:
    mcap_path = _write_fixture_bag(tmp_path, message_count=3)

    result = validate_mcap_file(mcap_path, expected_message_count=3)

    assert result.message_count == 3
    assert result.first_log_time == 1_000_000_000
    assert result.last_log_time == 1_000_000_002


def test_validate_raises_on_message_count_mismatch(tmp_path) -> None:
    mcap_path = _write_fixture_bag(tmp_path, message_count=3)

    with pytest.raises(McapValidationError):
        validate_mcap_file(mcap_path, expected_message_count=4)


def test_validate_raises_on_zero_messages(tmp_path) -> None:
    mcap_path = _write_fixture_bag(tmp_path, message_count=0)

    with pytest.raises(McapValidationError):
        validate_mcap_file(mcap_path, expected_message_count=0)


def test_validate_raises_on_corrupt_file(tmp_path) -> None:
    corrupt_path = tmp_path / "corrupt.mcap"
    corrupt_path.write_bytes(b"not a real mcap file" * 10)

    with pytest.raises(McapValidationError):
        validate_mcap_file(str(corrupt_path), expected_message_count=1)
