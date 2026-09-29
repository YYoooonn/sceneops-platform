"""Unit tests for can_timestamp.py.

Runs only inside the ros2 container (needs builtin_interfaces, apt-
installed there). No CAN data, no Kafka, no ROS graph needed -- pure
conversion logic.
"""

from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from can_timestamp import can_timestamp_to_ns, ns_to_ros_time  # noqa: E402


def test_can_timestamp_to_ns_exact_conversion() -> None:
    # Real value from data/raw/nuscenes/can_bus/scene-0061_pose.json's
    # first record.
    assert can_timestamp_to_ns(1_532_402_927_665_106) == 1_532_402_927_665_106_000


def test_can_timestamp_to_ns_zero() -> None:
    assert can_timestamp_to_ns(0) == 0


def test_can_timestamp_to_ns_is_pure_microsecond_to_nanosecond_scaling() -> None:
    for utime_us in (1, 1_000, 999_999, 1_532_402_928_127_800):
        assert can_timestamp_to_ns(utime_us) == utime_us * 1_000


def test_ns_to_ros_time_splits_sec_and_nanosec() -> None:
    stamp = ns_to_ros_time(1_532_402_927_665_106_000)
    assert stamp.sec == 1_532_402_927
    assert stamp.nanosec == 665_106_000


def test_ns_to_ros_time_zero() -> None:
    stamp = ns_to_ros_time(0)
    assert stamp.sec == 0
    assert stamp.nanosec == 0


def test_ns_to_ros_time_sub_second_only() -> None:
    stamp = ns_to_ros_time(999_999_999)
    assert stamp.sec == 0
    assert stamp.nanosec == 999_999_999


def test_can_timestamp_to_ns_then_ns_to_ros_time_round_trips() -> None:
    utime_us = 1_532_402_928_127_800
    ns = can_timestamp_to_ns(utime_us)
    stamp = ns_to_ros_time(ns)
    assert stamp.sec * 1_000_000_000 + stamp.nanosec == ns
