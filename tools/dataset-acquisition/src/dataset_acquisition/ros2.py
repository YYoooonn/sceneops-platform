"""Standard ROS 2 messages without a ROS 2 runtime.

Message classes, definitions and CDR serialization come from ``rosbags``'
built-in ROS 2 Jazzy type store -- the distribution the SceneOps ROS 2 image
runs -- so every recording embeds standard ``ros2msg`` definitions and CDR
payloads that any MCAP ROS 2 reader decodes. No message type is hand-written
or SceneOps-specific.
"""

from __future__ import annotations

from functools import cache
from typing import Any

import numpy as np
from rosbags.interfaces import Nodetype
from rosbags.typesys import Stores, get_typestore

from .events import AcquisitionEvent, MessageType

ROS2_SCHEMA_ENCODING = "ros2msg"
CDR_MESSAGE_ENCODING = "cdr"

_TYPESTORE = get_typestore(Stores.ROS2_JAZZY)

# Default values the ROS 2 definitions declare but rosbags' type store does
# not carry; rclpy applies them to unset fields.
_DECLARED_DEFAULTS: dict[str, dict[str, Any]] = {
    "geometry_msgs/msg/Quaternion": {"w": 1.0},
}

_NUMPY_DTYPES = {
    "bool": np.bool_,
    "byte": np.uint8,
    "char": np.uint8,
    "int8": np.int8,
    "uint8": np.uint8,
    "int16": np.int16,
    "uint16": np.uint16,
    "int32": np.int32,
    "uint32": np.uint32,
    "int64": np.int64,
    "uint64": np.uint64,
    "float32": np.float32,
    "float64": np.float64,
}


@cache
def message_type(name: str) -> MessageType:
    definition, _ = _TYPESTORE.generate_msgdef(name, ros_version=2)
    return MessageType(name=name, encoding=ROS2_SCHEMA_ENCODING, definition=definition)


def _default(desc: tuple[Nodetype, Any]) -> Any:
    nodetype, detail = desc
    if nodetype == Nodetype.BASE:
        base = detail[0]
        if base == "string":
            return ""
        if base == "bool":
            return False
        return 0.0 if base.startswith("float") else 0
    if nodetype == Nodetype.NAME:
        return make(detail)
    subtype, length = detail
    if subtype[0] == Nodetype.BASE and subtype[1][0] in _NUMPY_DTYPES:
        dtype = _NUMPY_DTYPES[subtype[1][0]]
        return np.zeros(length if nodetype == Nodetype.ARRAY else 0, dtype=dtype)
    if nodetype == Nodetype.ARRAY:
        return [_default(subtype) for _ in range(length)]
    return []


def make(type_name: str, /, **fields: Any) -> Any:
    """A message of type ``type_name``: every field not given takes the
    value an unset field has in rclpy (zero, empty, or the declared
    default)."""
    _, field_defs = _TYPESTORE.fielddefs[type_name]
    declared = _DECLARED_DEFAULTS.get(type_name, {})
    values = {}
    for field_name, desc in field_defs:
        if field_name in fields:
            values[field_name] = fields.pop(field_name)
        elif field_name in declared:
            values[field_name] = declared[field_name]
        else:
            values[field_name] = _default(desc)
    if fields:
        raise TypeError(f"{type_name} has no fields {sorted(fields)}")
    return _TYPESTORE.types[type_name](**values)


def stamp(timestamp_ns: int) -> Any:
    sec, nanosec = divmod(timestamp_ns, 1_000_000_000)
    return make("builtin_interfaces/msg/Time", sec=sec, nanosec=nanosec)


def header(timestamp_ns: int, frame_id: str = "") -> Any:
    return make("std_msgs/msg/Header", stamp=stamp(timestamp_ns), frame_id=frame_id)


def uint8_array(data: bytes) -> np.ndarray:
    return np.frombuffer(data, dtype=np.uint8)


def event(
    topic: str,
    message: Any,
    *,
    source_time_ns: int,
    sequence: int | None = None,
) -> AcquisitionEvent:
    """Serialize ``message`` once into an event on ``topic``."""
    name = message.__msgtype__
    return AcquisitionEvent(
        topic=topic,
        message_type=message_type(name),
        message_encoding=CDR_MESSAGE_ENCODING,
        payload=bytes(_TYPESTORE.serialize_cdr(message, name)),
        source_time_ns=source_time_ns,
        sequence=sequence,
    )
