"""Unit tests for CanReplayNode's timestamp fidelity.

Runs only inside the ros2 container (needs the real ROS2 message
packages to construct Odometry/Imu/BatteryState/String objects). Tests
the pure `_build_*` functions directly -- no Node, no publisher, no
rclpy.init() needed at all, since these functions only transform a CAN
source record dict into a fully-built ROS2 message and never touch a
live ROS graph (see `can_replay_node.py`'s own `_build_odometry` etc.
docstring for why these stay pure functions rather than methods that
publish directly).
"""

from __future__ import annotations

import json
import sys
import time
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from can_replay_node import (  # noqa: E402
    _build_imu,
    _build_mission_status,
    _build_odometry,
    _build_status_and_control,
)


class TestOdometryTimestampFidelity:
    def test_header_stamp_is_real_can_utime_not_replay_clock(self) -> None:
        # A deliberately implausible-as-wall-clock utime (year ~2018,
        # microseconds -- the real value from
        # data/raw/nuscenes/can_bus/scene-0061_pose.json's first record)
        # proves this is the SOURCE record's own value, never
        # `self.get_clock().now()` at call time.
        pose = {
            "utime": 1_532_402_927_665_106,
            "pos": [1.0, 2.0, 3.0],
            "orientation": [1.0, 0.0, 0.0, 0.0],
            "vel": [0.1, 0.2, 0.3],
        }

        msg = _build_odometry(pose)

        assert msg.header.stamp.sec == 1_532_402_927
        assert msg.header.stamp.nanosec == 665_106_000

    def test_position_orientation_velocity_mapped(self) -> None:
        pose = {
            "utime": 1,
            "pos": [1.0, 2.0, 3.0],
            "orientation": [0.5, 0.1, 0.2, 0.3],  # nuScenes wxyz
            "vel": [4.0, 5.0, 6.0],
        }

        msg = _build_odometry(pose)

        assert (msg.pose.pose.position.x, msg.pose.pose.position.y, msg.pose.pose.position.z) == (
            1.0,
            2.0,
            3.0,
        )
        # nuScenes (w,x,y,z) -> ROS geometry_msgs/Quaternion (x,y,z,w)
        assert (
            msg.pose.pose.orientation.x,
            msg.pose.pose.orientation.y,
            msg.pose.pose.orientation.z,
            msg.pose.pose.orientation.w,
        ) == (0.1, 0.2, 0.3, 0.5)
        assert (msg.twist.twist.linear.x, msg.twist.twist.linear.y, msg.twist.twist.linear.z) == (
            4.0,
            5.0,
            6.0,
        )


class TestImuTimestampFidelity:
    def test_header_stamp_is_real_can_utime(self) -> None:
        ms_imu = {
            "utime": 1_532_402_927_649_034,
            "q": [1.0, 0.0, 0.0, 0.0],
            "rotation_rate": [0.01, 0.02, 0.03],
            "linear_accel": [0.0, 0.0, 9.81],
        }

        msg = _build_imu(ms_imu)

        assert msg.header.stamp.sec == 1_532_402_927
        assert msg.header.stamp.nanosec == 649_034_000

    def test_one_record_maps_to_one_message_no_combining(self) -> None:
        # Two distinct ms_imu records must never blend into one message
        # or share a timestamp -- one call, one record, one stamp.
        first = _build_imu(
            {
                "utime": 1000,
                "q": [1.0, 0.0, 0.0, 0.0],
                "rotation_rate": [0.0, 0.0, 0.0],
                "linear_accel": [0.0, 0.0, 0.0],
            }
        )
        second = _build_imu(
            {
                "utime": 2000,
                "q": [1.0, 0.0, 0.0, 0.0],
                "rotation_rate": [0.0, 0.0, 0.0],
                "linear_accel": [0.0, 0.0, 0.0],
            }
        )
        assert (first.header.stamp.sec, first.header.stamp.nanosec) == (0, 1_000_000)
        assert (second.header.stamp.sec, second.header.stamp.nanosec) == (0, 2_000_000)


class TestVehicleStatusAndControlTimestampFidelity:
    def test_battery_state_header_stamp_is_real_can_utime(self) -> None:
        vehicle_monitor = {
            "utime": 1_532_402_928_127_800,
            "battery_level": 91,
            "steering": 3.0,
            "throttle": 0,
            "brake": 0,
        }

        status, _control = _build_status_and_control(vehicle_monitor)

        assert status.header.stamp.sec == 1_532_402_928
        assert status.header.stamp.nanosec == 127_800_000
        assert status.percentage == pytest.approx(0.91)

    def test_control_json_carries_same_source_timestamp_as_status(self) -> None:
        vehicle_monitor = {
            "utime": 1_532_402_928_127_800,
            "battery_level": 91,
            "steering": 3.0,
            "throttle": 0.0,
            "brake": 0.0,
        }

        status, control = _build_status_and_control(vehicle_monitor)

        payload = json.loads(control.data)
        expected_ns = 1_532_402_928_127_800 * 1_000
        assert payload["source_timestamp_ns"] == expected_ns
        # Same CAN record owns both emitted messages -- one utime, no
        # ambiguity between /vehicle/status and /vehicle/control.
        assert (
            status.header.stamp.sec * 1_000_000_000 + status.header.stamp.nanosec == expected_ns
        )

    def test_control_json_preserves_observed_feedback_fields(self) -> None:
        vehicle_monitor = {
            "utime": 1,
            "battery_level": 50,
            "steering": 1.5,
            "throttle": 0.4,
            "brake": 0.1,
        }

        _status, control = _build_status_and_control(vehicle_monitor)

        payload = json.loads(control.data)
        assert payload["steering"] == 1.5
        assert payload["throttle"] == 0.4
        assert payload["brake"] == 0.1


class TestMissionStatusSyntheticTimestamp:
    def test_source_timestamp_ns_field_is_populated(self) -> None:
        msg = _build_mission_status(
            scene_name="scene-0061",
            robot_id="robot-test",
            operation_state="running",
            event_timestamp_ns=1_700_000_000_000_000_000,
        )

        payload = json.loads(msg.data)
        assert payload["operation_state"] == "running"
        assert payload["mission_id"] == "mission-scene-0061"
        assert payload["robot_id"] == "robot-test"
        assert payload["source_timestamp_ns"] == 1_700_000_000_000_000_000

    def test_event_timestamp_is_whatever_caller_supplies_not_derived_from_can_data(self) -> None:
        # No CAN utime is ever available for a synthetic replay-boundary
        # event -- _build_mission_status takes the timestamp as an
        # explicit parameter (the real node passes its own ROS clock at
        # call time) rather than deriving one internally, so this proves
        # the function performs no CAN-like computation on it at all.
        before = time.time_ns()
        msg = _build_mission_status(
            scene_name="scene-0061",
            robot_id="robot-test",
            operation_state="completed",
            event_timestamp_ns=before,
        )
        after = time.time_ns()

        payload = json.loads(msg.data)
        assert payload["source_timestamp_ns"] == before
        assert before <= payload["source_timestamp_ns"] <= after
