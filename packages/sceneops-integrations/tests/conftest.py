from __future__ import annotations

import io
from collections.abc import Callable, Iterable
from pathlib import Path

import pytest

# (topic, schema_name, log_time_ns) per message
MessageSpec = tuple[str, str, int]


def build_mcap(messages: Iterable[MessageSpec], *, library: str = "test") -> bytes:
    """Deterministic MCAP bytes: one schema/channel per (topic, schema)."""
    from mcap.writer import Writer

    buffer = io.BytesIO()
    writer = Writer(buffer)
    writer.start(profile="ros2", library=library)
    channels: dict[tuple[str, str], int] = {}
    for sequence, (topic, schema_name, log_time) in enumerate(messages):
        key = (topic, schema_name)
        if key not in channels:
            schema_id = writer.register_schema(
                name=schema_name, encoding="ros2msg", data=b"# test"
            )
            channels[key] = writer.register_channel(
                topic=topic, message_encoding="cdr", schema_id=schema_id
            )
        writer.add_message(
            channels[key],
            log_time=log_time,
            data=b"\x00\x01",
            publish_time=log_time,
            sequence=sequence,
        )
    writer.finish()
    return buffer.getvalue()


DEFAULT_MESSAGES: list[MessageSpec] = [
    ("/vehicle/odom", "nav_msgs/msg/Odometry", 1_700_000_000_000_001_999),
    ("/vehicle/imu", "sensor_msgs/msg/Imu", 1_700_000_000_500_000_000),
    ("/vehicle/odom", "nav_msgs/msg/Odometry", 1_700_000_001_000_000_000),
    ("/vehicle/odom", "nav_msgs/msg/Odometry", 1_700_000_002_000_000_999),
]


@pytest.fixture()
def write_mcap(tmp_path: Path) -> Callable[..., Path]:
    def _write(
        messages: Iterable[MessageSpec] = DEFAULT_MESSAGES,
        *,
        name: str = "run.mcap",
        library: str = "test",
    ) -> Path:
        path = tmp_path / "captures" / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(build_mcap(messages, library=library))
        return path

    return _write


@pytest.fixture()
def make_capture() -> Callable[..., Path]:
    """A finalized capture directory the way Capture leaves it:
    ``<base>/<run_id>/`` holding ``<run_id>_0.mcap`` and a canonical
    ``capture_receipt.json`` that describes those bytes."""
    from datetime import UTC, datetime

    from sceneops_core.robots.capture_receipt import (
        CAPTURE_RECEIPT_FILENAME,
        CaptureReceipt,
        FinalizationReason,
        ReceiptFinalization,
        ReceiptKafka,
        ReceiptRecording,
    )
    from sceneops_core.robots.manifest import (
        CaptureInfo,
        CaptureSource,
        CaptureSourceKind,
        RecordingFormat,
    )
    from sceneops_integrations.recording import derive_mcap_facts, sha256_checksum

    def _make(
        base: Path,
        *,
        run_id: str,
        messages: Iterable[MessageSpec] = DEFAULT_MESSAGES,
        robot_id: str = "robot-001",
    ) -> Path:
        data = build_mcap(messages)
        facts = derive_mcap_facts(io.BytesIO(data), source_clock="mcap_log_time")
        receipt = CaptureReceipt(
            run_id=run_id,
            robot_id=robot_id,
            robot_platform="nuscenes-can-replay",
            recording=ReceiptRecording(
                file=f"{run_id}_0.mcap",
                format=RecordingFormat.MCAP,
                checksum=sha256_checksum(data),
                size_bytes=len(data),
            ),
            capture=CaptureInfo(
                source=CaptureSource(
                    kind=CaptureSourceKind.KAFKA, topics=["sceneops.robot.telemetry.v1"]
                ),
                source_clock="mcap_log_time",
            ),
            message_count=facts.message_count,
            per_channel_counts={c.topic: c.message_count for c in facts.channels},
            finalization=ReceiptFinalization(
                reason=FinalizationReason.EXPLICIT_RUN_END,
                finalized_at=datetime(2026, 10, 5, 12, 0, tzinfo=UTC),
            ),
            kafka=ReceiptKafka(
                partition=0,
                first_offset=0,
                last_offset=facts.message_count,
                first_sequence=0,
                last_sequence=facts.message_count - 1,
            ),
        )
        directory = base / run_id
        directory.mkdir(parents=True)
        (directory / f"{run_id}_0.mcap").write_bytes(data)
        (directory / CAPTURE_RECEIPT_FILENAME).write_bytes(receipt.to_canonical_bytes())
        return directory

    return _make
