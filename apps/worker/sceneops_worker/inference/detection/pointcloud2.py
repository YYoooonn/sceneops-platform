"""Decoding of canonical lidar payloads (ADR-007 §30.4, §33.5).

A canonical ``ros2_message`` lidar payload is the recorded
``sensor_msgs/msg/PointCloud2`` message exactly as serialized: CDR with its
encapsulation header, media type
``application/x.ros2-cdr.sensor_msgs.msg.pointcloud2``. Consumers dispatch
on that media type. The message layout is the fixed ROS 2 definition, so a
small reader suffices and no ROS 2 installation or schema lookup is needed.

Only what 3-D lifting needs is decoded: the geometry (``x``, ``y``, ``z``,
the standard PointCloud2 field names) in the cloud's own frame. The source
point layout (``fields``, ``point_step``, ``row_step``, endianness) is
honoured, never assumed.
"""

from __future__ import annotations

import struct
from collections.abc import Callable
from dataclasses import dataclass

import numpy as np

POINTCLOUD2_CDR_MEDIA_TYPE = "application/x.ros2-cdr.sensor_msgs.msg.pointcloud2"

_FLOAT32 = 7
_FLOAT64 = 8
_NUMPY_BY_DATATYPE = {_FLOAT32: "f4", _FLOAT64: "f8"}


class PointCloudDecodeError(ValueError):
    """The payload is not a decodable PointCloud2 CDR message."""


class UnsupportedLidarMediaTypeError(ValueError):
    """No decoder is registered for the lidar payload's media type."""


@dataclass
class _Reader:
    data: bytes
    little_endian: bool
    pos: int

    def _align(self, size: int) -> None:
        # Alignment is relative to the first byte after the encapsulation header.
        self.pos += (-(self.pos - 4)) % size

    def _take(self, fmt: str, size: int):
        self._align(size)
        end = self.pos + size
        if end > len(self.data):
            raise PointCloudDecodeError("PointCloud2 message is truncated")
        (value,) = struct.unpack_from(
            ("<" if self.little_endian else ">") + fmt, self.data, self.pos
        )
        self.pos = end
        return value

    def u8(self) -> int:
        return self._take("B", 1)

    def u32(self) -> int:
        return self._take("I", 4)

    def i32(self) -> int:
        return self._take("i", 4)

    def string(self) -> str:
        length = self.u32()
        end = self.pos + length
        if length == 0 or end > len(self.data):
            raise PointCloudDecodeError("PointCloud2 message has a malformed string")
        raw = self.data[self.pos : end - 1]
        self.pos = end
        return raw.decode("utf-8", errors="replace")

    def bytes_(self, length: int) -> bytes:
        end = self.pos + length
        if end > len(self.data):
            raise PointCloudDecodeError("PointCloud2 message is truncated")
        out = self.data[self.pos : end]
        self.pos = end
        return out


def decode_pointcloud2_cdr_xyz(data: bytes) -> np.ndarray:
    """``(N, 3)`` float64 xyz of every finite point of a CDR PointCloud2."""
    if len(data) < 4:
        raise PointCloudDecodeError(
            "PointCloud2 message is shorter than its CDR header"
        )
    representation = data[1]
    if data[0] != 0 or representation not in (0, 1):
        raise PointCloudDecodeError(
            f"unsupported CDR encapsulation {data[0]:#04x}{data[1]:02x}"
        )
    r = _Reader(data=data, little_endian=representation == 1, pos=4)

    r.i32()  # header.stamp.sec
    r.u32()  # header.stamp.nanosec
    r.string()  # header.frame_id
    height = r.u32()
    width = r.u32()
    fields: dict[str, tuple[int, int]] = {}
    for _ in range(r.u32()):
        name = r.string()
        offset = r.u32()
        datatype = r.u8()
        r.u32()  # count
        fields[name] = (offset, datatype)
    big_endian = bool(r.u8())
    point_step = r.u32()
    row_step = r.u32()
    payload = r.bytes_(r.u32())

    for axis in ("x", "y", "z"):
        if axis not in fields:
            raise PointCloudDecodeError(f"PointCloud2 has no {axis!r} field")
        if fields[axis][1] not in _NUMPY_BY_DATATYPE:
            raise PointCloudDecodeError(
                f"PointCloud2 field {axis!r} has unsupported datatype {fields[axis][1]}"
            )
    if width == 0 or height == 0:
        return np.empty((0, 3), dtype=np.float64)
    if point_step == 0 or row_step < width * point_step:
        raise PointCloudDecodeError(
            "PointCloud2 point_step / row_step are inconsistent"
        )
    if len(payload) < (height - 1) * row_step + width * point_step:
        raise PointCloudDecodeError(
            "PointCloud2 data is shorter than its declared points"
        )

    prefix = ">" if big_endian else "<"
    rows = []
    for row in range(height):
        base = row * row_step
        block = np.frombuffer(
            payload, dtype=np.uint8, count=width * point_step, offset=base
        )
        block = block.reshape(width, point_step)
        columns = []
        for axis in ("x", "y", "z"):
            offset, datatype = fields[axis]
            size = 4 if datatype == _FLOAT32 else 8
            raw = np.ascontiguousarray(block[:, offset : offset + size])
            columns.append(
                raw.view(prefix + _NUMPY_BY_DATATYPE[datatype]).reshape(width)
            )
        rows.append(np.stack(columns, axis=1).astype(np.float64))
    points = np.concatenate(rows, axis=0)
    return points[np.isfinite(points).all(axis=1)]


# Lidar payload formats the lifter can decode, keyed by the payload's declared
# media type. A lidar observation in any other format is not lifted (the caller
# records a failed lift) rather than guessed at.
LIDAR_XYZ_DECODERS: dict[str, Callable[[bytes], np.ndarray]] = {
    POINTCLOUD2_CDR_MEDIA_TYPE: decode_pointcloud2_cdr_xyz,
}


def decode_lidar_xyz(media_type: str, data: bytes) -> np.ndarray:
    decoder = LIDAR_XYZ_DECODERS.get(media_type)
    if decoder is None:
        raise UnsupportedLidarMediaTypeError(
            f"no lidar decoder for payload media_type {media_type!r}"
        )
    return decoder(data)


__all__ = [
    "LIDAR_XYZ_DECODERS",
    "POINTCLOUD2_CDR_MEDIA_TYPE",
    "PointCloudDecodeError",
    "UnsupportedLidarMediaTypeError",
    "decode_lidar_xyz",
    "decode_pointcloud2_cdr_xyz",
]
