"""Explicit v1 supported topic/type set for MCAP capture.

Source of truth for topic/type identity is always the consumed
``TelemetryEnvelope.channel``/``message_type`` themselves -- this
registry only validates an envelope's claims against the known,
supported set. No dynamic ROS2 topic/type discovery exists; an envelope
naming a channel or message_type outside this set fails capture loudly
(see ``capture_consumer.py``).

Deliberately just channel -> canonical ROS2 interface type STRING --
writing an MCAP message needs no imported ROS2 message Python class at
all (``rosbag2_py`` resolves the schema from the type string against the
installed ROS2 interface definitions, and the payload is opaque CDR
bytes). The message CLASSES (needed only for optional deserialize-based
verification, never for writing) live separately in ``validation.py``.
"""

from __future__ import annotations

SUPPORTED_CHANNELS: dict[str, str] = {
    "/vehicle/odom": "nav_msgs/msg/Odometry",
    "/vehicle/imu": "sensor_msgs/msg/Imu",
    "/vehicle/status": "sensor_msgs/msg/BatteryState",
    "/vehicle/control": "std_msgs/msg/String",
    "/mission/status": "std_msgs/msg/String",
}


def is_supported(*, channel: str, message_type: str) -> bool:
    return SUPPORTED_CHANNELS.get(channel) == message_type
