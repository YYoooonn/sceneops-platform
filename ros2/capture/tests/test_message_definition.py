"""message_definition.py: ros2msg schema text from the installed interfaces.

Each registry channel type's definition must be accepted by an
independent MCAP ROS 2 decoder and decode a payload produced by real
rclpy serialization to the same values.
"""

from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import pytest
from builtin_interfaces.msg import Time
from geometry_msgs.msg import TransformStamped
from mcap.writer import Writer
from mcap.reader import make_reader
from mcap_ros2.decoder import DecoderFactory
from nav_msgs.msg import Odometry
from rclpy.serialization import serialize_message
from sensor_msgs.msg import (
    BatteryState,
    CameraInfo,
    CompressedImage,
    Imu,
    PointCloud2,
    PointField,
)
from std_msgs.msg import String
from tf2_msgs.msg import TFMessage

from message_definition import SEPARATOR, message_definition  # noqa: E402


def _decode(type_name: str, payload: bytes):
    import io

    stream = io.BytesIO()
    writer = Writer(stream)
    writer.start(profile="ros2", library="test")
    schema_id = writer.register_schema(
        name=type_name, encoding="ros2msg", data=message_definition(type_name).encode()
    )
    channel_id = writer.register_channel(
        topic="/t", message_encoding="cdr", schema_id=schema_id
    )
    writer.add_message(channel_id=channel_id, log_time=1, publish_time=1, data=payload)
    writer.finish()
    stream.seek(0)
    reader = make_reader(stream, decoder_factories=[DecoderFactory()])
    [decoded] = list(reader.iter_decoded_messages())
    return decoded.decoded_message


def test_primitive_only_type_has_no_dependency_sections() -> None:
    assert SEPARATOR not in message_definition("std_msgs/msg/String")


def test_dependencies_follow_in_separate_sections_once_each() -> None:
    text = message_definition("tf2_msgs/msg/TFMessage")
    sections = [line for line in text.splitlines() if line.startswith("MSG: ")]
    assert sections == [
        "MSG: geometry_msgs/TransformStamped",
        "MSG: std_msgs/Header",
        "MSG: builtin_interfaces/Time",
        "MSG: geometry_msgs/Transform",
        "MSG: geometry_msgs/Vector3",
        "MSG: geometry_msgs/Quaternion",
    ]


def test_non_message_interface_is_rejected() -> None:
    with pytest.raises(ValueError):
        message_definition("std_srvs/srv/Empty")


def test_string_round_trip() -> None:
    decoded = _decode("std_msgs/msg/String", serialize_message(String(data="hello")))
    assert decoded.data == "hello"


def test_tf_message_round_trip() -> None:
    transform = TransformStamped()
    transform.header.stamp = Time(sec=9, nanosec=8)
    transform.header.frame_id = "map"
    transform.child_frame_id = "base_link"
    transform.transform.translation.x = 1.5
    transform.transform.rotation.w = 1.0
    decoded = _decode(
        "tf2_msgs/msg/TFMessage", serialize_message(TFMessage(transforms=[transform]))
    )
    [t] = decoded.transforms
    assert (t.header.stamp.sec, t.header.stamp.nanosec) == (9, 8)
    assert (t.header.frame_id, t.child_frame_id) == ("map", "base_link")
    assert t.transform.translation.x == 1.5


def test_sensor_types_round_trip() -> None:
    image = CompressedImage(format="jpeg", data=bytes(range(200)))
    image.header.stamp = Time(sec=1, nanosec=2)
    image.header.frame_id = "cam_front"
    decoded = _decode("sensor_msgs/msg/CompressedImage", serialize_message(image))
    assert decoded.format == "jpeg" and bytes(decoded.data) == bytes(range(200))
    assert decoded.header.frame_id == "cam_front"

    cloud = PointCloud2(width=2, height=1, point_step=8, row_step=16, data=bytes(16))
    cloud.fields = [PointField(name="x", offset=0, datatype=7, count=1)]
    decoded = _decode("sensor_msgs/msg/PointCloud2", serialize_message(cloud))
    assert decoded.width == 2 and decoded.fields[0].name == "x"

    info = CameraInfo(width=1600, height=900, distortion_model="plumb_bob")
    info.k = [float(i) for i in range(9)]
    decoded = _decode("sensor_msgs/msg/CameraInfo", serialize_message(info))
    assert decoded.width == 1600 and list(decoded.k) == [float(i) for i in range(9)]


def test_vehicle_types_round_trip() -> None:
    odom = Odometry()
    odom.pose.pose.position.x = 4.25
    assert (
        _decode("nav_msgs/msg/Odometry", serialize_message(odom)).pose.pose.position.x
        == 4.25
    )
    battery = BatteryState(percentage=0.5)
    assert (
        _decode("sensor_msgs/msg/BatteryState", serialize_message(battery)).percentage
        == 0.5
    )
    imu = Imu()
    imu.linear_acceleration.z = 9.81
    assert (
        _decode("sensor_msgs/msg/Imu", serialize_message(imu)).linear_acceleration.z
        == 9.81
    )
