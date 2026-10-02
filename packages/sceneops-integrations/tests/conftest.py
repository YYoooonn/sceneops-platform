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
