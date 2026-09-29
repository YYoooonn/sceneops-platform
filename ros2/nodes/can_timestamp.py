"""Timestamp conversion helpers for nuScenes CAN bus source records.

Every CAN source record (pose/ms_imu/vehicle_monitor, matching what
NuScenesCanBus.get_messages() returns) carries one `utime` field, an
integer count of MICROSECONDS since the Unix epoch (e.g.
`1532402927665106`). One helper, used everywhere a CAN-derived timestamp
needs converting -- never a unit conversion scattered inline across
publishers.
"""

from __future__ import annotations

from builtin_interfaces.msg import Time

# nuScenes CAN records' `utime` field unit -- integer microseconds since
# the Unix epoch.
_CAN_UTIME_TO_NS = 1_000


def can_timestamp_to_ns(utime_us: int) -> int:
    """Convert a nuScenes CAN record's ``utime`` field (integer
    microseconds since epoch) to nanoseconds."""

    return utime_us * _CAN_UTIME_TO_NS


def ns_to_ros_time(timestamp_ns: int) -> Time:
    """Convert a nanosecond integer timestamp into a ROS2
    ``builtin_interfaces/Time`` (sec + nanosec), for assignment to a
    message's ``header.stamp``."""

    sec, nanosec = divmod(timestamp_ns, 1_000_000_000)
    return Time(sec=int(sec), nanosec=int(nanosec))
