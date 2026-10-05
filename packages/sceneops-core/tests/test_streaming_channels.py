"""Static streaming channel registry: defaults, channel files, conflicts."""

from __future__ import annotations

import json
from pathlib import Path

import pytest
from pydantic import ValidationError

from sceneops_core.constants.streaming import SESSION_CONTROL_CHANNEL
from sceneops_core.streaming import (
    DEFAULT_CHANNELS,
    ChannelSpec,
    TimestampRule,
    build_channel_registry,
    load_channel_file,
)

REPO_ROOT = Path(__file__).resolve().parents[3]


def _write(path: Path, channels: list[dict]) -> Path:
    path.write_text(json.dumps({"channels": channels}))
    return path


def test_defaults_cover_telemetry_and_transform_channels() -> None:
    registry = build_channel_registry()
    assert set(registry.topics()) == {
        "/vehicle/odom",
        "/vehicle/imu",
        "/vehicle/status",
        "/vehicle/control",
        "/mission/status",
        "/tf",
        "/tf_static",
    }
    assert registry.get("/tf_static").latched is True
    assert registry.get("/tf").latched is False
    assert registry.get("/tf").timestamp is TimestampRule.TRANSFORM_HEADER
    assert registry.get("/vehicle/control").timestamp is TimestampRule.JSON_FIELD


def test_only_static_data_may_carry_a_zero_source_stamp_by_default() -> None:
    registry = build_channel_registry()
    assert {s.topic for s in registry if s.allow_zero_stamp} == {"/tf_static"}


def test_channel_file_can_allow_zero_stamps(tmp_path: Path) -> None:
    path = _write(
        tmp_path / "extra.json",
        [
            {
                "topic": "/calibration",
                "message_type": "tf2_msgs/msg/TFMessage",
                "timestamp": "transform_header",
                "latched": True,
                "allow_zero_stamp": True,
            }
        ],
    )
    assert build_channel_registry([path]).get("/calibration").allow_zero_stamp is True


def test_is_supported_requires_matching_type() -> None:
    registry = build_channel_registry()
    assert registry.is_supported(
        channel="/vehicle/odom", message_type="nav_msgs/msg/Odometry"
    )
    assert not registry.is_supported(
        channel="/vehicle/odom", message_type="sensor_msgs/msg/Imu"
    )
    assert not registry.is_supported(
        channel="/unknown", message_type="nav_msgs/msg/Odometry"
    )


def test_channel_file_adds_channels(tmp_path: Path) -> None:
    path = _write(
        tmp_path / "extra.json",
        [
            {
                "topic": "/camera/front/image/compressed",
                "message_type": "sensor_msgs/msg/CompressedImage",
                "timestamp": "header",
            }
        ],
    )
    registry = build_channel_registry([path])
    assert len(registry) == len(DEFAULT_CHANNELS) + 1
    spec = registry.get("/camera/front/image/compressed")
    assert spec.queue_depth is None and spec.latched is False


def test_conflicting_redefinition_is_rejected(tmp_path: Path) -> None:
    path = _write(
        tmp_path / "bad.json",
        [
            {
                "topic": "/vehicle/odom",
                "message_type": "sensor_msgs/msg/Imu",
                "timestamp": "header",
            }
        ],
    )
    with pytest.raises(ValueError, match="defined twice"):
        build_channel_registry([path])


def test_identical_redefinition_is_idempotent(tmp_path: Path) -> None:
    path = _write(
        tmp_path / "same.json",
        [
            {
                "topic": "/vehicle/odom",
                "message_type": "nav_msgs/msg/Odometry",
                "timestamp": "header",
            }
        ],
    )
    assert len(build_channel_registry([path])) == len(DEFAULT_CHANNELS)


@pytest.mark.parametrize(
    "entry",
    [
        {
            "topic": "camera",
            "message_type": "sensor_msgs/msg/Image",
            "timestamp": "header",
        },
        {
            "topic": SESSION_CONTROL_CHANNEL,
            "message_type": "std_msgs/msg/String",
            "timestamp": "json_field",
        },
        {"topic": "/x", "message_type": "Image", "timestamp": "header"},
        {"topic": "/x", "message_type": "sensor_msgs/srv/Image", "timestamp": "header"},
        {
            "topic": "/x",
            "message_type": "sensor_msgs/msg/Image",
            "timestamp": "callback",
        },
        {
            "topic": "/x",
            "message_type": "sensor_msgs/msg/Image",
            "timestamp": "header",
            "queue_depth": 0,
        },
    ],
)
def test_invalid_channel_specs_fail(entry: dict) -> None:
    with pytest.raises(ValidationError):
        ChannelSpec.model_validate(entry)


def test_channel_file_shape_is_validated(tmp_path: Path) -> None:
    path = tmp_path / "bad.json"
    path.write_text("[]")
    with pytest.raises(ValueError, match="'channels' list"):
        load_channel_file(path)


def test_shipped_surround_channel_set_loads() -> None:
    registry = build_channel_registry(
        [REPO_ROOT / "ros2/channels/surround-camera-lidar.json"]
    )
    assert "/lidar/top/points" in registry
    assert (
        registry.get("/lidar/top/points").message_type == "sensor_msgs/msg/PointCloud2"
    )
    assert sum(1 for s in registry if s.topic.endswith("/image/compressed")) == 6
    assert sum(1 for s in registry if s.topic.endswith("/camera_info")) == 6
