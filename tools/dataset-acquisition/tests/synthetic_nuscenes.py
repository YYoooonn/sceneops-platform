"""A tiny nuScenes-format dataroot written to tmp_path, loadable by the
official devkit, plus an MCAP reader that decodes with ``mcap_ros2`` --
the platform's reader, independent of the rosbags serializer under test.

Layout of the synthetic source (timestamps in µs):

    scene-0001  samples s1 (t=1_000_000), s2 (t=1_500_000)
        CAM_FRONT   key 1_000_000, sweep 1_250_000, key 1_500_000
        CAM_BACK    key 1_000_000 (same time as CAM_FRONT), key 1_500_000
        LIDAR_TOP   key 1_000_050, sweep 1_200_000, key 1_500_050
        RADAR_FRONT key 1_000_020                      (never converted)
    scene-0002  sample s3 (t=9_000_000): one CAM_FRONT key frame (excluded)
    CAN (scene-0001 only): pose, ms_imu, vehicle_monitor from 990_000 µs
"""

from __future__ import annotations

import hashlib
import json
import struct
from dataclasses import dataclass
from pathlib import Path
from typing import Any

VERSION = "v1.0-test"
UNIT = "scene-0001"

INTRINSIC = [[1266.4, 0.0, 816.3], [0.0, 1266.4, 491.5], [0.0, 0.0, 1.0]]
CALIBRATION = {
    "CAM_FRONT": ([1.70, 0.02, 1.51], [0.4998, -0.5030, 0.4997, -0.4973]),
    "CAM_BACK": ([0.03, 0.01, 1.59], [0.5037, -0.4941, -0.4993, 0.5027]),
    "LIDAR_TOP": ([0.94, 0.00, 1.84], [0.7071, 0.0, 0.0, -0.7071]),
    "RADAR_FRONT": ([3.41, 0.00, 0.50], [1.0, 0.0, 0.0, 0.0]),
}
MODALITY = {
    "CAM_FRONT": "camera",
    "CAM_BACK": "camera",
    "LIDAR_TOP": "lidar",
    "RADAR_FRONT": "radar",
}


@dataclass(frozen=True)
class SampleData:
    token: str
    sample: str
    channel: str
    timestamp: int
    is_key_frame: bool

    @property
    def filename(self) -> str:
        ext = {"camera": "jpg", "lidar": "pcd.bin", "radar": "pcd"}[
            MODALITY[self.channel]
        ]
        folder = "samples" if self.is_key_frame else "sweeps"
        return f"{folder}/{self.channel}/{self.token}.{ext}"


SAMPLE_DATA = [
    SampleData("sd-cf-1", "s1", "CAM_FRONT", 1_000_000, True),
    SampleData("sd-cb-1", "s1", "CAM_BACK", 1_000_000, True),
    SampleData("sd-rf-1", "s1", "RADAR_FRONT", 1_000_020, True),
    SampleData("sd-lt-1", "s1", "LIDAR_TOP", 1_000_050, True),
    SampleData("sd-lt-s", "s1", "LIDAR_TOP", 1_200_000, False),
    SampleData("sd-cf-s", "s1", "CAM_FRONT", 1_250_000, False),
    SampleData("sd-cf-2", "s2", "CAM_FRONT", 1_500_000, True),
    SampleData("sd-cb-2", "s2", "CAM_BACK", 1_500_000, True),
    SampleData("sd-lt-2", "s2", "LIDAR_TOP", 1_500_050, True),
    SampleData("sd-cf-3", "s3", "CAM_FRONT", 9_000_000, True),
]

CAN_POSE = [
    {
        "utime": 990_000 + 100_000 * i,
        "pos": [10.0 + i, 20.0, 0.0],
        "orientation": [0.9, 0.0, 0.0, 0.436],
        "vel": [5.0 + i, 0.0, 0.0],
        "rotation_rate": [0.0, 0.0, 0.01],
        "accel": [0.1, 0.0, 9.8],
    }
    for i in range(6)
]
CAN_IMU = [
    {
        "utime": 995_000 + 50_000 * i,
        "q": [0.99, 0.01, 0.02, 0.03],
        "rotation_rate": [0.001 * i, 0.0, 0.0],
        "linear_accel": [0.0, 0.1, 9.81],
    }
    for i in range(11)
]
CAN_MONITOR = [
    {
        "utime": 1_100_000 + 300_000 * i,
        "battery_level": 91 - i,
        "steering": 3.0 + i,
        "throttle": 10 * i,
        "brake": 0,
        "vehicle_speed": 31.4,
    }
    for i in range(2)
]


def image_bytes(token: str) -> bytes:
    return (
        b"\xff\xd8\xff\xe0" + hashlib.sha256(token.encode()).digest() * 4 + b"\xff\xd9"
    )


def lidar_bytes(token: str) -> bytes:
    n = 3 + len(token)
    return b"".join(
        struct.pack("<5f", i * 0.5, -i * 0.25, 1.0 + i, float(i % 7), float(i % 32))
        for i in range(n)
    )


def write_dataroot(
    root: Path, *, extra_calibration: bool = False, can: bool = True
) -> Path:
    tables = root / VERSION
    tables.mkdir(parents=True)
    sensors = [
        {"token": f"sensor-{c}", "channel": c, "modality": MODALITY[c]}
        for c in CALIBRATION
    ]
    calibrated = [
        {
            "token": f"cs-{c}",
            "sensor_token": f"sensor-{c}",
            "translation": t,
            "rotation": r,
            "camera_intrinsic": INTRINSIC if MODALITY[c] == "camera" else [],
        }
        for c, (t, r) in CALIBRATION.items()
    ]
    if extra_calibration:
        calibrated.append(
            {**calibrated[0], "token": "cs-CAM_FRONT-2", "translation": [9.0, 9.0, 9.0]}
        )

    def cs_token(sd: SampleData) -> str:
        return (
            "cs-CAM_FRONT-2"
            if extra_calibration and sd.token == "sd-cf-2"
            else f"cs-{sd.channel}"
        )

    sample_data = [
        {
            "token": sd.token,
            "sample_token": sd.sample,
            "ego_pose_token": sd.token,
            "calibrated_sensor_token": cs_token(sd),
            "timestamp": sd.timestamp,
            "fileformat": sd.filename.split(".", 1)[1],
            "is_key_frame": sd.is_key_frame,
            "height": 900 if MODALITY[sd.channel] == "camera" else 0,
            "width": 1600 if MODALITY[sd.channel] == "camera" else 0,
            "filename": sd.filename,
            "prev": "",
            "next": "",
        }
        for sd in SAMPLE_DATA
    ]
    ego_poses = [
        {
            "token": sd.token,
            "timestamp": sd.timestamp,
            "translation": [100.0 + sd.timestamp / 1e6, 200.0, 0.0],
            "rotation": [0.96, 0.0, 0.0, -0.28],
        }
        for sd in SAMPLE_DATA
    ]
    samples = [
        {
            "token": "s1",
            "timestamp": 1_000_000,
            "prev": "",
            "next": "s2",
            "scene_token": "scene-a",
        },
        {
            "token": "s2",
            "timestamp": 1_500_000,
            "prev": "s1",
            "next": "",
            "scene_token": "scene-a",
        },
        {
            "token": "s3",
            "timestamp": 9_000_000,
            "prev": "",
            "next": "",
            "scene_token": "scene-b",
        },
    ]
    scenes = [
        {
            "token": "scene-a",
            "name": UNIT,
            "log_token": "log-1",
            "nbr_samples": 2,
            "first_sample_token": "s1",
            "last_sample_token": "s2",
            "description": "",
        },
        {
            "token": "scene-b",
            "name": "scene-0002",
            "log_token": "log-1",
            "nbr_samples": 1,
            "first_sample_token": "s3",
            "last_sample_token": "s3",
            "description": "",
        },
    ]
    content = {
        "category": [],
        "attribute": [],
        "visibility": [],
        "instance": [],
        "sample_annotation": [],
        "sensor": sensors,
        "calibrated_sensor": calibrated,
        "ego_pose": ego_poses,
        "log": [
            {
                "token": "log-1",
                "logfile": "n000-test",
                "vehicle": "n000",
                "date_captured": "2018-07-24",
                "location": "test",
            }
        ],
        "scene": scenes,
        "sample": samples,
        "sample_data": sample_data,
        "map": [
            {
                "token": "map-1",
                "log_tokens": ["log-1"],
                "category": "semantic_prior",
                "filename": "maps/map.png",
            }
        ],
    }
    for name, rows in content.items():
        (tables / f"{name}.json").write_text(json.dumps(rows))
    (root / "maps").mkdir()
    (root / "maps" / "map.png").write_bytes(b"")
    for sd in SAMPLE_DATA:
        path = root / sd.filename
        path.parent.mkdir(parents=True, exist_ok=True)
        kind = MODALITY[sd.channel]
        path.write_bytes(
            image_bytes(sd.token)
            if kind == "camera"
            else lidar_bytes(sd.token)
            if kind == "lidar"
            else b"radar"
        )
    (root / "can_bus").mkdir()
    if can:
        for name, rows in (
            ("pose", CAN_POSE),
            ("ms_imu", CAN_IMU),
            ("vehicle_monitor", CAN_MONITOR),
        ):
            (root / "can_bus" / f"{UNIT}_{name}.json").write_text(json.dumps(rows))
    return root


@dataclass(frozen=True)
class ReadMessage:
    topic: str
    schema_name: str
    schema_encoding: str
    message_encoding: str
    log_time: int
    publish_time: int
    sequence: int
    data: bytes
    decoded: Any


def read_mcap(path: Path) -> tuple[list[ReadMessage], dict[str, dict[str, str]], str]:
    """Messages in file order, metadata records and the header profile."""
    from mcap.reader import make_reader
    from mcap_ros2.decoder import DecoderFactory

    with path.open("rb") as stream:
        reader = make_reader(stream, decoder_factories=[DecoderFactory()])
        profile = reader.get_header().profile
        messages = [
            ReadMessage(
                topic=channel.topic,
                schema_name=schema.name,
                schema_encoding=schema.encoding,
                message_encoding=channel.message_encoding,
                log_time=message.log_time,
                publish_time=message.publish_time,
                sequence=message.sequence,
                data=message.data,
                decoded=decoded,
            )
            for schema, channel, message, decoded in reader.iter_decoded_messages(
                log_time_order=False
            )
        ]
        metadata = {m.name: dict(m.metadata) for m in reader.iter_metadata()}
    return messages, metadata, profile


def stamp_ns(stamp: Any) -> int:
    return stamp.sec * 1_000_000_000 + stamp.nanosec
