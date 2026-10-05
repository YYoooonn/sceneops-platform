"""PointCloud2 CDR decoding of canonical lidar payloads (ADR-007 §30.4),
verified against messages serialized by an independent implementation
(``mcap_ros2``)."""

from __future__ import annotations

import struct

import numpy as np
import pytest

from sceneops_integrations.recording import iter_recording_messages
from sceneops_worker.inference.detection.pointcloud2 import (
    POINTCLOUD2_CDR_MEDIA_TYPE,
    PointCloudDecodeError,
    UnsupportedLidarMediaTypeError,
    decode_lidar_xyz,
    decode_pointcloud2_cdr_xyz,
)
from tests.scenes.recording_fixture import (
    LIDAR_TOPIC,
    Recording,
    default_recording,
    header,
    point_cloud,
)


def _lidar_messages(tmp_path, recording: Recording) -> list[bytes]:
    path = recording.write(tmp_path / "rec.mcap")
    return [m.data for m in iter_recording_messages(path, topics=[LIDAR_TOPIC])]


def test_decodes_every_point_of_a_recorded_message(tmp_path) -> None:
    messages = _lidar_messages(tmp_path, default_recording())
    assert len(messages) == 4
    for index, data in enumerate(messages):
        xyz = decode_pointcloud2_cdr_xyz(data)
        assert xyz.shape == (index + 1, 3)
        expected = [[i, i + 1.0, i + 2.0] for i in range(index + 1)]
        np.testing.assert_allclose(xyz, expected)


def test_dispatch_on_the_declared_media_type(tmp_path) -> None:
    (data, *_rest) = _lidar_messages(tmp_path, default_recording())
    np.testing.assert_allclose(
        decode_lidar_xyz(POINTCLOUD2_CDR_MEDIA_TYPE, data),
        decode_pointcloud2_cdr_xyz(data),
    )
    with pytest.raises(UnsupportedLidarMediaTypeError):
        decode_lidar_xyz("application/x.nuscenes.lidar-pcd-bin", data)


def test_honours_a_non_default_point_layout(tmp_path) -> None:
    # float64 xyz at non-zero offsets with padding around each point.
    points = [(1.5, -2.5, 3.5), (4.0, 5.0, 6.0)]
    data = b"".join(struct.pack("<4x3d8x", *p) for p in points)
    cloud = {
        "header": header(1, "lidar"),
        "height": 1,
        "width": 2,
        "fields": [
            {"name": n, "offset": 4 + 8 * i, "datatype": 8, "count": 1}
            for i, n in enumerate(("x", "y", "z"))
        ],
        "is_bigendian": False,
        "point_step": 36,
        "row_step": 72,
        "data": data,
        "is_dense": True,
    }
    rec = Recording().add(LIDAR_TOPIC, "sensor_msgs/msg/PointCloud2", cloud, 10)
    (message,) = _lidar_messages(tmp_path, rec)
    np.testing.assert_allclose(decode_pointcloud2_cdr_xyz(message), points)


def test_non_finite_points_are_dropped(tmp_path) -> None:
    cloud = point_cloud(1, points=3)
    data = bytearray(cloud["data"])
    struct.pack_into("<f", data, 20, float("nan"))  # second point's x
    cloud["data"] = bytes(data)
    rec = Recording().add(LIDAR_TOPIC, "sensor_msgs/msg/PointCloud2", cloud, 10)
    (message,) = _lidar_messages(tmp_path, rec)
    assert decode_pointcloud2_cdr_xyz(message).shape == (2, 3)


def test_multi_row_clouds_honour_row_step(tmp_path) -> None:
    point = struct.pack("<fff", 1.0, 2.0, 3.0)
    row = point * 2 + b"\x00" * 4  # 4 bytes of row padding
    cloud = {
        "header": header(1, "lidar"),
        "height": 2,
        "width": 2,
        "fields": [
            {"name": n, "offset": 4 * i, "datatype": 7, "count": 1}
            for i, n in enumerate(("x", "y", "z"))
        ],
        "is_bigendian": False,
        "point_step": 12,
        "row_step": 28,
        "data": row * 2,
        "is_dense": True,
    }
    rec = Recording().add(LIDAR_TOPIC, "sensor_msgs/msg/PointCloud2", cloud, 10)
    (message,) = _lidar_messages(tmp_path, rec)
    assert decode_pointcloud2_cdr_xyz(message).shape == (4, 3)


def test_malformed_messages_fail_loudly(tmp_path) -> None:
    (data, *_rest) = _lidar_messages(tmp_path, default_recording())
    with pytest.raises(PointCloudDecodeError):
        decode_pointcloud2_cdr_xyz(b"\x00")
    with pytest.raises(PointCloudDecodeError):
        decode_pointcloud2_cdr_xyz(b"\x00\x09\x00\x00" + data[4:])
    with pytest.raises(PointCloudDecodeError):
        decode_pointcloud2_cdr_xyz(data[: len(data) // 2])
