"""A minimal ROS 2 CDR decoder for the message types of the reference corpus.

Written from the OMG CDR (XCDR1) rules ROS 2 uses: a 4-byte encapsulation
header, then little-endian primitives aligned to their own size relative to
the end of that header; strings are a uint32 length that counts the
terminating NUL; sequences are a uint32 count followed by their elements.
The layouts below are the ROS 2 interface definitions of each type. It
shares no code with the SceneOps recording reader or ``mcap_ros2``.
"""

from __future__ import annotations

import struct
from dataclasses import dataclass
from typing import Any

_LITTLE_ENDIAN_ENCAPSULATIONS = {b"\x00\x01", b"\x00\x03"}


class CdrError(ValueError):
    """The bytes are not a CDR message of the expected type."""


class _Reader:
    def __init__(self, data: bytes) -> None:
        if len(data) < 4 or data[:2] not in _LITTLE_ENDIAN_ENCAPSULATIONS:
            raise CdrError("not a little-endian CDR encapsulation")
        self.data = data
        self.pos = 4

    def _align(self, size: int) -> None:
        offset = (self.pos - 4) % size
        if offset:
            self.pos += size - offset

    def _unpack(self, fmt: str, size: int) -> Any:
        self._align(size)
        if self.pos + size > len(self.data):
            raise CdrError("message truncated")
        (value,) = struct.unpack_from("<" + fmt, self.data, self.pos)
        self.pos += size
        return value

    def u8(self) -> int:
        return self._unpack("B", 1)

    def boolean(self) -> bool:
        return bool(self._unpack("B", 1))

    def i32(self) -> int:
        return self._unpack("i", 4)

    def u32(self) -> int:
        return self._unpack("I", 4)

    def f32(self) -> float:
        return self._unpack("f", 4)

    def f64(self) -> float:
        return self._unpack("d", 8)

    def string(self) -> str:
        length = self.u32()
        if length == 0 or self.pos + length > len(self.data):
            raise CdrError("bad string length")
        raw = self.data[self.pos : self.pos + length]
        self.pos += length
        if raw[-1:] != b"\x00":
            raise CdrError("string is not NUL-terminated")
        return raw[:-1].decode("utf-8")

    def octets(self) -> bytes:
        count = self.u32()
        if self.pos + count > len(self.data):
            raise CdrError("octet sequence truncated")
        raw = self.data[self.pos : self.pos + count]
        self.pos += count
        return raw

    def f64_array(self, n: int) -> list[float]:
        return [self.f64() for _ in range(n)]

    def f64_sequence(self) -> list[float]:
        return self.f64_array(self.u32())


@dataclass(frozen=True)
class Header:
    stamp_ns: int
    frame_id: str


def _header(r: _Reader) -> Header:
    sec = r.i32()
    nanosec = r.u32()
    return Header(sec * 1_000_000_000 + nanosec, r.string())


def _vector3(r: _Reader) -> list[float]:
    return r.f64_array(3)


def _quaternion_wxyz(r: _Reader) -> list[float]:
    x, y, z, w = r.f64_array(4)
    return [w, x, y, z]


@dataclass(frozen=True)
class CompressedImage:
    header: Header
    format: str
    data: bytes


def compressed_image(data: bytes) -> CompressedImage:
    r = _Reader(data)
    return CompressedImage(_header(r), r.string(), r.octets())


@dataclass(frozen=True)
class PointCloud2:
    header: Header
    height: int
    width: int
    point_step: int
    data: bytes


def point_cloud2(data: bytes) -> PointCloud2:
    r = _Reader(data)
    header = _header(r)
    height, width = r.u32(), r.u32()
    for _ in range(r.u32()):  # fields: name, offset, datatype, count
        r.string()
        r.u32()
        r.u8()
        r.u32()
    r.boolean()  # is_bigendian
    point_step = r.u32()
    r.u32()  # row_step
    return PointCloud2(header, height, width, point_step, r.octets())


@dataclass(frozen=True)
class Transform:
    stamp_ns: int
    parent_frame_id: str
    child_frame_id: str
    translation_m: list[float]
    rotation_wxyz: list[float]


def tf_message(data: bytes) -> list[Transform]:
    r = _Reader(data)
    out = []
    for _ in range(r.u32()):
        header = _header(r)
        child = r.string()
        translation = _vector3(r)
        rotation = _quaternion_wxyz(r)
        out.append(
            Transform(header.stamp_ns, header.frame_id, child, translation, rotation)
        )
    return out


@dataclass(frozen=True)
class CameraInfo:
    header: Header
    height: int
    width: int
    distortion_model: str
    d: list[float]
    k: list[float]
    r: list[float]
    p: list[float]


def camera_info(data: bytes) -> CameraInfo:
    r = _Reader(data)
    header = _header(r)
    height, width = r.u32(), r.u32()
    model = r.string()
    d = r.f64_sequence()
    return CameraInfo(
        header, height, width, model, d, r.f64_array(9), r.f64_array(9), r.f64_array(12)
    )


@dataclass(frozen=True)
class Odometry:
    header: Header
    child_frame_id: str
    position: list[float]
    orientation_wxyz: list[float]
    linear: list[float]
    angular: list[float]


def odometry(data: bytes) -> Odometry:
    r = _Reader(data)
    header = _header(r)
    child = r.string()
    position = _vector3(r)
    orientation = _quaternion_wxyz(r)
    r.f64_array(36)
    linear = _vector3(r)
    angular = _vector3(r)
    r.f64_array(36)
    return Odometry(header, child, position, orientation, linear, angular)


@dataclass(frozen=True)
class BatteryState:
    header: Header
    voltage: float
    temperature: float
    current: float
    charge: float
    capacity: float
    design_capacity: float
    percentage: float


def battery_state(data: bytes) -> BatteryState:
    r = _Reader(data)
    header = _header(r)
    values = [r.f32() for _ in range(7)]
    return BatteryState(header, *values)


def string_message(data: bytes) -> str:
    return _Reader(data).string()


def header_of(data: bytes) -> Header:
    """The leading std_msgs/Header of a stamped message."""
    return _header(_Reader(data))
