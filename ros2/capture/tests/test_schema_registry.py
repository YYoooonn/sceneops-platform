"""Unit tests for schema_registry.py.

Runs only inside the ros2 container (needs sceneops_core installed
there, and matches the rest of ros2/capture's flat-script import
convention). No ROS graph, no Kafka.
"""

from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from schema_registry import SUPPORTED_CHANNELS, is_supported  # noqa: E402


def test_supported_channel_and_type_is_supported() -> None:
    assert is_supported(channel="/vehicle/odom", message_type="nav_msgs/msg/Odometry")


def test_unknown_channel_is_not_supported() -> None:
    assert not is_supported(channel="/unknown/topic", message_type="std_msgs/msg/String")


def test_known_channel_with_wrong_type_is_not_supported() -> None:
    channel = "/vehicle/odom"
    wrong_type = "std_msgs/msg/String"
    assert SUPPORTED_CHANNELS[channel] != wrong_type
    assert not is_supported(channel=channel, message_type=wrong_type)
