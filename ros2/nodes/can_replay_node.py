#!/usr/bin/env python3
"""CanReplayNode: replays nuScenes CAN bus data as ROS2 topics.

docs/workflows/robot-run-and-mcap.md §2 target topics:

    /vehicle/odom      nav_msgs/msg/Odometry        <- CAN 'pose' messages
    /vehicle/imu       sensor_msgs/msg/Imu           <- CAN 'ms_imu' messages
    /vehicle/status    sensor_msgs/msg/BatteryState  <- CAN 'vehicle_monitor' (battery_level)
    /vehicle/control   std_msgs/msg/String (JSON)    <- CAN 'vehicle_monitor' (steering/throttle/brake)
    /mission/status    std_msgs/msg/String (JSON)    <- synthetic, published at replay start/end

/vehicle/control and /mission/status have no standard ROS2 message that fits
(steering+throttle+brake feedback, or SceneOps mission metadata) and defining
one means a proper custom .msg colcon package — out of scope for this replay
node. Using std_msgs/String with a JSON payload instead: RosbagAdapter's JSON
decode path already treats unrecognized schemas as pass-through flat fields,
so publishing flat {"steering": ..., "throttle": ..., "brake": ...} here is
exactly what it expects on the other end.

Timestamp fidelity: every message's source observation time is the real
nuScenes CAN record's own `utime` field (integer microseconds since
epoch — see can_timestamp.py), converted to ns and set as header.stamp
for header-bearing messages (Odometry/Imu/BatteryState), or threaded
through as an explicit `source_timestamp_ns` JSON field for
std_msgs/String (/vehicle/control, /mission/status has no original CAN
time — its field carries this replay session's own publish-time clock
instead, documented as synthetic). No message's timestamp is ever the
replay session's own publish clock UNLESS that is genuinely what it
represents (/mission/status only). See
docs/architecture/streaming-transport.md's ROS2 topic mapping section for
the full per-topic contract.

Usage (inside the ros2 Docker sandbox — see makefiles/ros2.mk):
    python3 /workspace/nodes/can_replay_node.py --scene scene-0061 --rate 5.0
"""

from __future__ import annotations

import argparse
import json
import time
from typing import Any

import rclpy
from nav_msgs.msg import Odometry
from nuscenes.can_bus.can_bus_api import NuScenesCanBus
from rclpy.node import Node
from sensor_msgs.msg import BatteryState, Imu
from std_msgs.msg import String

from can_timestamp import can_timestamp_to_ns, ns_to_ros_time

_REPLAYED_MESSAGE_TYPES = ("pose", "ms_imu", "vehicle_monitor")


def _quat_wxyz_to_ros(q: list[float]) -> tuple[float, float, float, float]:
    """nuScenes quaternions are (w, x, y, z) — verified against the same
    scene's ego_pose['rotation'] records, which nuScenes documents as (w, x,
    y, z). geometry_msgs/Quaternion is (x, y, z, w)."""
    w, x, y, z = q
    return x, y, z, w


def _build_timeline(
    can_bus: NuScenesCanBus, scene_name: str
) -> list[tuple[int, str, dict[str, Any]]]:
    """Merge pose/ms_imu/vehicle_monitor messages into one utime-sorted stream
    so playback interleaves them in the order they actually occurred."""
    timeline: list[tuple[int, str, dict[str, Any]]] = []
    for msg_type in _REPLAYED_MESSAGE_TYPES:
        for msg in can_bus.get_messages(scene_name, msg_type):
            timeline.append((msg["utime"], msg_type, msg))
    timeline.sort(key=lambda item: item[0])
    return timeline


# Pure message builders -- deliberately module-level functions with no
# Node/publisher dependency, not methods. They only transform a CAN
# source record dict into a fully-built ROS2 message; CanReplayNode's own
# _publish_* methods are thin wrappers that call one of these and then
# publish the result. This split keeps the timestamp-mapping logic
# testable directly (real CAN record in, real ROS2 message out) without
# constructing a live Node/publisher: repeatedly creating and destroying
# Publishers across many ROS2 node instances can crash rclpy's DDS layer
# at the C level, which these pure functions avoid entirely by never
# touching a live publisher.


def _build_odometry(pose: dict[str, Any]) -> Odometry:
    msg = Odometry()
    # Real nuScenes CAN 'pose' observation time -- never the replay
    # session's own publish clock.
    msg.header.stamp = ns_to_ros_time(can_timestamp_to_ns(pose["utime"]))
    msg.header.frame_id = "odom"
    msg.pose.pose.position.x, msg.pose.pose.position.y, msg.pose.pose.position.z = pose["pos"]
    (
        msg.pose.pose.orientation.x,
        msg.pose.pose.orientation.y,
        msg.pose.pose.orientation.z,
        msg.pose.pose.orientation.w,
    ) = _quat_wxyz_to_ros(pose["orientation"])
    msg.twist.twist.linear.x, msg.twist.twist.linear.y, msg.twist.twist.linear.z = pose["vel"]
    return msg


def _build_imu(ms_imu: dict[str, Any]) -> Imu:
    msg = Imu()
    # Real nuScenes CAN 'ms_imu' observation time -- one ms_imu record
    # maps to exactly one Imu message, no combining of multiple source
    # records, so there is no multi-source timestamp ambiguity to
    # resolve here.
    msg.header.stamp = ns_to_ros_time(can_timestamp_to_ns(ms_imu["utime"]))
    msg.header.frame_id = "imu"
    (
        msg.orientation.x,
        msg.orientation.y,
        msg.orientation.z,
        msg.orientation.w,
    ) = _quat_wxyz_to_ros(ms_imu["q"])
    (
        msg.angular_velocity.x,
        msg.angular_velocity.y,
        msg.angular_velocity.z,
    ) = ms_imu["rotation_rate"]
    (
        msg.linear_acceleration.x,
        msg.linear_acceleration.y,
        msg.linear_acceleration.z,
    ) = ms_imu["linear_accel"]
    return msg


def _build_status_and_control(vehicle_monitor: dict[str, Any]) -> tuple[BatteryState, String]:
    # Both status and control are derived from the SAME 'vehicle_monitor'
    # CAN record -- one utime owns both emitted messages, no ambiguity
    # between the two.
    source_timestamp_ns = can_timestamp_to_ns(vehicle_monitor["utime"])

    status = BatteryState()
    status.header.stamp = ns_to_ros_time(source_timestamp_ns)
    status.percentage = float(vehicle_monitor["battery_level"]) / 100.0
    status.present = True

    control = String()
    control.data = json.dumps(
        {
            "steering": vehicle_monitor["steering"],
            "throttle": vehicle_monitor["throttle"],
            "brake": vehicle_monitor["brake"],
            # std_msgs/String carries no Header -- the real CAN
            # observation time is threaded through explicitly as a JSON
            # field instead, same convention/field name as
            # /mission/status's synthetic counterpart. Still observed
            # vehicle feedback, never renamed to action/command/
            # policy_output.
            "source_timestamp_ns": source_timestamp_ns,
        }
    )
    return status, control


def _build_mission_status(
    *, scene_name: str, robot_id: str, operation_state: str, event_timestamp_ns: int
) -> String:
    msg = String()
    msg.data = json.dumps(
        {
            "mission_id": f"mission-{scene_name}",
            "robot_id": robot_id,
            "operation_state": operation_state,
            "source_timestamp_ns": event_timestamp_ns,
        }
    )
    return msg


class CanReplayNode(Node):
    def __init__(
        self, *, dataroot: str, scene_name: str, rate: float, robot_id: str
    ) -> None:
        super().__init__("can_replay_node")
        self._scene_name = scene_name
        self._rate = rate
        self._robot_id = robot_id

        self._odom_pub = self.create_publisher(Odometry, "/vehicle/odom", 10)
        self._imu_pub = self.create_publisher(Imu, "/vehicle/imu", 10)
        self._status_pub = self.create_publisher(BatteryState, "/vehicle/status", 10)
        self._control_pub = self.create_publisher(String, "/vehicle/control", 10)
        self._mission_pub = self.create_publisher(String, "/mission/status", 10)

        can_bus = NuScenesCanBus(dataroot=dataroot)
        self._timeline = _build_timeline(can_bus, scene_name)
        self.get_logger().info(
            f"Loaded {len(self._timeline)} CAN messages for {scene_name}"
        )

    def _wait_for_subscribers(self, timeout_s: float = 15.0) -> None:
        """DDS discovery between this freshly-started node and an
        already-running `ros2 bag record` subscriber is not instantaneous --
        a fixed startup delay in the calling shell/Make script (`sleep N`
        before launching this node) does not reliably bound it, since the
        discovery handshake only begins once THIS node exists and can take
        a variable amount of time afterward, independent of how long the
        caller waited first. Publishing before discovery completes silently
        drops messages -- no error, no redelivery -- which is exactly how a
        recording can end up missing `/mission/status`'s single "running"
        message and break MISSION_BOUNDARY episode segmentation downstream
        (found via a real, reproducible flake recording nuScenes scene-0103/
        scene-0553: `ros2 bag info` showed `/mission/status` count=1 instead
        of the expected 2). Block briefly on actual subscriber-count
        confirmation instead of guessing a delay."""
        publishers = (
            self._odom_pub,
            self._imu_pub,
            self._status_pub,
            self._control_pub,
            self._mission_pub,
        )
        deadline = time.monotonic() + timeout_s
        for pub in publishers:
            while pub.get_subscription_count() == 0:
                if time.monotonic() > deadline:
                    self.get_logger().warn(
                        f"Timed out after {timeout_s}s waiting for a "
                        f"subscriber on {pub.topic_name} -- proceeding "
                        "anyway; early messages on this topic may be lost."
                    )
                    break
                time.sleep(0.1)

    def _publish_mission_status(self, operation_state: str) -> String:
        msg = _build_mission_status(
            scene_name=self._scene_name,
            robot_id=self._robot_id,
            operation_state=operation_state,
            # /mission/status is a SYNTHETIC replay-boundary event, not a
            # nuScenes CAN observation -- there is no original CAN utime
            # for it. This is this replay session's own publish-time
            # clock (documented in docs/architecture/streaming-transport.md's
            # per-topic table) so it is never mistaken for a real source
            # observation downstream.
            event_timestamp_ns=self.get_clock().now().nanoseconds,
        )
        self._mission_pub.publish(msg)
        return msg

    def _publish_odom(self, pose: dict[str, Any]) -> Odometry:
        msg = _build_odometry(pose)
        self._odom_pub.publish(msg)
        return msg

    def _publish_imu(self, ms_imu: dict[str, Any]) -> Imu:
        msg = _build_imu(ms_imu)
        self._imu_pub.publish(msg)
        return msg

    def _publish_status_and_control(
        self, vehicle_monitor: dict[str, Any]
    ) -> tuple[BatteryState, String]:
        status, control = _build_status_and_control(vehicle_monitor)
        self._status_pub.publish(status)
        self._control_pub.publish(control)
        return status, control

    def replay(self) -> None:
        self._wait_for_subscribers()
        self._publish_mission_status("running")

        prev_utime: int | None = None
        for utime, msg_type, payload in self._timeline:
            if prev_utime is not None:
                delay_s = (utime - prev_utime) / 1_000_000.0 / self._rate
                if delay_s > 0:
                    time.sleep(delay_s)
            prev_utime = utime

            if msg_type == "pose":
                self._publish_odom(payload)
            elif msg_type == "ms_imu":
                self._publish_imu(payload)
            elif msg_type == "vehicle_monitor":
                self._publish_status_and_control(payload)

        self._publish_mission_status("completed")
        self.get_logger().info(f"Replay of {self._scene_name} complete")


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Replay nuScenes CAN bus data as ROS2 topics"
    )
    parser.add_argument(
        "--scene", required=True, help="nuScenes scene name, e.g. scene-0061"
    )
    parser.add_argument("--dataroot", default="/data/raw/nuscenes")
    parser.add_argument(
        "--rate", type=float, default=1.0, help="Playback speed multiplier"
    )
    parser.add_argument("--robot-id", default="nuscenes-can-replay")
    args = parser.parse_args()

    rclpy.init()
    node = CanReplayNode(
        dataroot=args.dataroot,
        scene_name=args.scene,
        rate=args.rate,
        robot_id=args.robot_id,
    )
    try:
        node.replay()
    finally:
        node.destroy_node()
        rclpy.shutdown()


if __name__ == "__main__":
    main()
