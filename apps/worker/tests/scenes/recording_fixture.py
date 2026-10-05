"""Small, real ROS 2 (CDR / ros2msg) MCAP recordings for recording-builder
tests.

Messages are serialized by ``mcap_ros2`` from standard ROS 2 Jazzy message
definitions embedded in the file, exactly as any ROS 2 recorder would, so
the builder decodes them with the schemas the recording carries. The
default recording has one camera (CompressedImage + CameraInfo), one lidar
(PointCloud2), static calibration on /tf_static and ego poses on /tf, with
source header stamps that differ from log_time.
"""

from __future__ import annotations

import struct
from collections.abc import Iterable
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

_SEP = "\n" + "=" * 80 + "\n"

_TIME = "int32 sec\nuint32 nanosec\n"
_HEADER = "builtin_interfaces/Time stamp\nstring frame_id\n"
_VECTOR3 = "float64 x\nfloat64 y\nfloat64 z\n"
_QUATERNION = "float64 x 0\nfloat64 y 0\nfloat64 z 0\nfloat64 w 1\n"
_TRANSFORM = "Vector3 translation\nQuaternion rotation\n"
_ROI = (
    "uint32 x_offset\nuint32 y_offset\nuint32 height\nuint32 width\nbool do_rectify\n"
)
_POINT_FIELD = (
    "uint8 INT8=1\nuint8 UINT8=2\nuint8 INT16=3\nuint8 UINT16=4\nuint8 INT32=5\n"
    "uint8 UINT32=6\nuint8 FLOAT32=7\nuint8 FLOAT64=8\n"
    "string name\nuint32 offset\nuint8 datatype\nuint32 count\n"
)


def _with_deps(main: str, deps: dict[str, str]) -> str:
    return main + "".join(f"{_SEP}MSG: {name}\n{text}" for name, text in deps.items())


MSGDEFS: dict[str, str] = {
    "sensor_msgs/msg/CompressedImage": _with_deps(
        "std_msgs/Header header\nstring format\nuint8[] data\n",
        {"std_msgs/Header": _HEADER, "builtin_interfaces/Time": _TIME},
    ),
    "sensor_msgs/msg/CameraInfo": _with_deps(
        "std_msgs/Header header\nuint32 height\nuint32 width\nstring distortion_model\n"
        "float64[] d\nfloat64[9] k\nfloat64[9] r\nfloat64[12] p\nuint32 binning_x\n"
        "uint32 binning_y\nsensor_msgs/RegionOfInterest roi\n",
        {
            "std_msgs/Header": _HEADER,
            "builtin_interfaces/Time": _TIME,
            "sensor_msgs/RegionOfInterest": _ROI,
        },
    ),
    "sensor_msgs/msg/PointCloud2": _with_deps(
        "std_msgs/Header header\nuint32 height\nuint32 width\n"
        "sensor_msgs/PointField[] fields\nbool is_bigendian\nuint32 point_step\n"
        "uint32 row_step\nuint8[] data\nbool is_dense\n",
        {
            "std_msgs/Header": _HEADER,
            "builtin_interfaces/Time": _TIME,
            "sensor_msgs/PointField": _POINT_FIELD,
        },
    ),
    "tf2_msgs/msg/TFMessage": _with_deps(
        "geometry_msgs/TransformStamped[] transforms\n",
        {
            "geometry_msgs/TransformStamped": "std_msgs/Header header\n"
            "string child_frame_id\nTransform transform\n",
            "std_msgs/Header": _HEADER,
            "builtin_interfaces/Time": _TIME,
            "geometry_msgs/Transform": _TRANSFORM,
            "geometry_msgs/Vector3": _VECTOR3,
            "geometry_msgs/Quaternion": _QUATERNION,
        },
    ),
}

JPEG = b"\xff\xd8\xff\xe0" + b"jpeg-bytes"
CAMERA_TOPIC = "/camera/front/image/compressed"
CAMERA_INFO_TOPIC = "/camera/front/camera_info"
LIDAR_TOPIC = "/lidar/top/points"
K = [1266.4, 0.0, 816.3, 0.0, 1266.4, 491.5, 0.0, 0.0, 1.0]
# Header stamps are on the source clock; log_time lags them by this much.
LOG_LAG_NS = 1_000


def stamp(ns: int) -> dict[str, int]:
    return {"sec": ns // 1_000_000_000, "nanosec": ns % 1_000_000_000}


def header(ns: int, frame_id: str) -> dict[str, Any]:
    return {"stamp": stamp(ns), "frame_id": frame_id}


def transform(
    ns: int,
    parent: str,
    child: str,
    xyz=(1.0, 2.0, 3.0),
    xyzw=(0.0, 0.0, 0.0, 1.0),
) -> dict[str, Any]:
    x, y, z = xyz
    qx, qy, qz, qw = xyzw
    return {
        "header": header(ns, parent),
        "child_frame_id": child,
        "transform": {
            "translation": {"x": x, "y": y, "z": z},
            "rotation": {"x": qx, "y": qy, "z": qz, "w": qw},
        },
    }


def compressed_image(ns: int, *, data: bytes = JPEG, fmt: str = "jpeg") -> dict:
    return {"header": header(ns, "cam_front"), "format": fmt, "data": data}


def camera_info(ns: int, *, k=K, d=(0.0,) * 5, frame_id: str = "cam_front") -> dict:
    p = [k[0], k[1], k[2], 0.0, k[3], k[4], k[5], 0.0, k[6], k[7], k[8], 0.0]
    return {
        "header": header(ns, frame_id),
        "height": 900,
        "width": 1600,
        "distortion_model": "plumb_bob",
        "d": list(d),
        "k": list(k),
        "r": [1.0, 0.0, 0.0, 0.0, 1.0, 0.0, 0.0, 0.0, 1.0],
        "p": p,
        "binning_x": 0,
        "binning_y": 0,
        "roi": {
            "x_offset": 0,
            "y_offset": 0,
            "height": 0,
            "width": 0,
            "do_rectify": False,
        },
    }


def point_cloud(ns: int, points: int = 2) -> dict:
    data = b"".join(
        struct.pack("<fffff", i, i + 1.0, i + 2.0, 0.5, 1.0) for i in range(points)
    )
    return {
        "header": header(ns, "lidar_top"),
        "height": 1,
        "width": points,
        "fields": [
            {"name": n, "offset": 4 * i, "datatype": 7, "count": 1}
            for i, n in enumerate(("x", "y", "z", "intensity", "ring"))
        ],
        "is_bigendian": False,
        "point_step": 20,
        "row_step": 20 * points,
        "data": data,
        "is_dense": False,
    }


def tf_message(*transforms: dict) -> dict:
    return {"transforms": list(transforms)}


@dataclass
class Msg:
    topic: str
    schema: str
    message: dict
    log_time: int
    publish_time: int | None = None
    sequence: int = 0


@dataclass
class Recording:
    messages: list[Msg] = field(default_factory=list)

    def add(self, topic, schema, message, log_time, *, publish_time=None, sequence=0):
        self.messages.append(
            Msg(topic, schema, message, log_time, publish_time, sequence)
        )
        return self

    def write(
        self, path: Path, *, order: Iterable[int] | None = None, chunk_size=1 << 20
    ):
        from mcap_ros2.writer import Writer

        messages = self.messages if order is None else [self.messages[i] for i in order]
        with path.open("wb") as stream:
            writer = Writer(stream, chunk_size=chunk_size)
            schemas: dict[str, Any] = {}
            for m in messages:
                if m.schema not in schemas:
                    schemas[m.schema] = writer.register_msgdef(
                        m.schema, MSGDEFS[m.schema]
                    )
                writer.write_message(
                    m.topic,
                    schemas[m.schema],
                    m.message,
                    log_time=m.log_time,
                    publish_time=m.publish_time
                    if m.publish_time is not None
                    else m.log_time,
                    sequence=m.sequence,
                )
            writer.finish()
        return path


def default_recording(
    *,
    frame_stamps_ns: Iterable[int] = (
        1_000_000_000,
        1_500_000_000,
        2_000_000_000,
        2_500_000_000,
    ),
    start_ns: int = 900_000_000,
) -> Recording:
    """tf_static first, then per frame: ego pose, CameraInfo, image, lidar.
    Header stamps are source times; log_time = stamp + LOG_LAG_NS."""
    rec = Recording()
    rec.add(
        "/tf_static",
        "tf2_msgs/msg/TFMessage",
        tf_message(
            transform(
                start_ns,
                "base_link",
                "cam_front",
                (1.7, 0.0, 1.5),
                (-0.5, 0.5, -0.5, 0.5),
            ),
            transform(start_ns, "base_link", "lidar_top", (0.9, 0.0, 1.8)),
        ),
        start_ns,
        sequence=1,
    )
    for i, ns in enumerate(frame_stamps_ns):
        log = ns + LOG_LAG_NS
        rec.add(
            "/tf",
            "tf2_msgs/msg/TFMessage",
            tf_message(transform(ns, "map", "base_link", (float(i), 0.0, 0.0))),
            log,
            sequence=i + 1,
        )
        rec.add(
            CAMERA_INFO_TOPIC,
            "sensor_msgs/msg/CameraInfo",
            camera_info(ns),
            log,
            sequence=i + 1,
        )
        rec.add(
            CAMERA_TOPIC,
            "sensor_msgs/msg/CompressedImage",
            compressed_image(ns, data=JPEG + bytes([i])),
            log,
            sequence=i + 1,
        )
        rec.add(
            LIDAR_TOPIC,
            "sensor_msgs/msg/PointCloud2",
            point_cloud(ns + 7, points=i + 1),
            log + 7,
            sequence=i + 1,
        )
    return rec


def build_config(
    *,
    clock: str = "sensor.header_stamp",
    duration_ns: int = 1_000_000_000,
    camera_time: tuple[str, str] = ("header_stamp", "sensor.header_stamp"),
    lidar_time: tuple[str, str] = ("header_stamp", "sensor.header_stamp"),
    pose_time: tuple[str, str] = ("header_stamp", "sensor.header_stamp"),
    with_poses: bool = True,
) -> dict:
    return {
        "channels": [
            {
                "topic": CAMERA_TOPIC,
                "modality": "camera",
                "sensor_id": "cam-front",
                "time": {"source": camera_time[0], "clock": camera_time[1]},
                "payload": "compressed_image",
                "camera_info_topic": CAMERA_INFO_TOPIC,
            },
            {
                "topic": LIDAR_TOPIC,
                "modality": "lidar",
                "time": {"source": lidar_time[0], "clock": lidar_time[1]},
                "payload": "ros2_message",
            },
        ],
        "frames": {"ego_frame_id": "base_link", "world_frame_id": "map"},
        "poses": (
            [
                {
                    "topic": "/tf",
                    "parent_frame_id": "map",
                    "child_frame_id": "base_link",
                    "time": {"source": pose_time[0], "clock": pose_time[1]},
                }
            ]
            if with_poses
            else []
        ),
        "segmentation": {
            "policy": "fixed_duration",
            "clock": clock,
            "duration_ns": duration_ns,
        },
    }
