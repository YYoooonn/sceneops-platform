"""The facts of one recording, read once with the verifier's own MCAP and CDR
readers. Payload bytes are hashed while streaming and not kept."""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from . import cdr
from .mcap_records import Message, iter_messages, iter_messages_from_bytes
from .rules import checksum

IMAGE_SCHEMA = "sensor_msgs/msg/CompressedImage"
POINT_CLOUD_SCHEMA = "sensor_msgs/msg/PointCloud2"
TF_SCHEMA = "tf2_msgs/msg/TFMessage"
CAMERA_INFO_SCHEMA = "sensor_msgs/msg/CameraInfo"
ODOMETRY_SCHEMA = "nav_msgs/msg/Odometry"
BATTERY_SCHEMA = "sensor_msgs/msg/BatteryState"
STRING_SCHEMA = "std_msgs/msg/String"
IMU_SCHEMA = "sensor_msgs/msg/Imu"


@dataclass
class Entry:
    """One recorded message: where it is, when, and what it carries."""

    topic: str
    schema_name: str
    channel_index: int  # occurrence index among its own topic's messages, file order
    file_index: int
    sequence: int
    log_time: int
    publish_time: int
    message_checksum: str
    message_size: int
    stamp_ns: int | None = None
    frame_id: str | None = None
    image_format: str | None = None
    image_checksum: str | None = None
    image_size_bytes: int | None = None
    transforms: list[cdr.Transform] = field(default_factory=list)
    camera_info: cdr.CameraInfo | None = None
    values: dict[str, Any] = field(default_factory=dict)
    json: dict[str, Any] | None = None


@dataclass
class RecordingFacts:
    checksum: str
    size_bytes: int
    entries: list[Entry]

    def topic(self, topic: str) -> list[Entry]:
        return [e for e in self.entries if e.topic == topic]

    @property
    def topic_counts(self) -> dict[str, int]:
        out: dict[str, int] = {}
        for e in self.entries:
            out[e.topic] = out.get(e.topic, 0) + 1
        return dict(sorted(out.items()))

    @property
    def message_count(self) -> int:
        return len(self.entries)

    def schema_name(self, topic: str) -> str:
        names = {e.schema_name for e in self.entries if e.topic == topic}
        if len(names) != 1:
            raise KeyError(f"topic {topic!r} has schema names {sorted(names)}")
        return names.pop()


def _decode(entry: Entry, data: bytes) -> None:
    name = entry.schema_name
    if name == IMAGE_SCHEMA:
        image = cdr.compressed_image(data)
        entry.stamp_ns, entry.frame_id = image.header.stamp_ns, image.header.frame_id
        entry.image_format = image.format
        entry.image_checksum = checksum(image.data)
        entry.image_size_bytes = len(image.data)
    elif name == POINT_CLOUD_SCHEMA:
        cloud = cdr.point_cloud2(data)
        entry.stamp_ns, entry.frame_id = cloud.header.stamp_ns, cloud.header.frame_id
        entry.image_checksum = checksum(cloud.data)  # the point data alone
        entry.image_size_bytes = len(cloud.data)
    elif name == TF_SCHEMA:
        entry.transforms = cdr.tf_message(data)
    elif name == CAMERA_INFO_SCHEMA:
        info = cdr.camera_info(data)
        entry.stamp_ns, entry.frame_id = info.header.stamp_ns, info.header.frame_id
        entry.camera_info = info
    elif name == ODOMETRY_SCHEMA:
        odom = cdr.odometry(data)
        entry.stamp_ns, entry.frame_id = odom.header.stamp_ns, odom.header.frame_id
        entry.values = {
            "pose.pose.position.x": odom.position[0],
            "pose.pose.position.y": odom.position[1],
            "pose.pose.position.z": odom.position[2],
            "twist.twist.linear.x": odom.linear[0],
            "twist.twist.linear.y": odom.linear[1],
            "twist.twist.linear.z": odom.linear[2],
        }
    elif name == BATTERY_SCHEMA:
        battery = cdr.battery_state(data)
        entry.stamp_ns, entry.frame_id = (
            battery.header.stamp_ns,
            battery.header.frame_id,
        )
        entry.values = {"percentage": battery.percentage, "voltage": battery.voltage}
    elif name == STRING_SCHEMA:
        text = cdr.string_message(data)
        try:
            decoded = json.loads(text)
        except json.JSONDecodeError:
            decoded = None
        entry.json = decoded if isinstance(decoded, dict) else None
    elif name == IMU_SCHEMA:
        header = cdr.header_of(data)
        entry.stamp_ns, entry.frame_id = header.stamp_ns, header.frame_id


def _facts(messages: Any, file_checksum: str, size_bytes: int) -> RecordingFacts:
    entries: list[Entry] = []
    per_topic: dict[str, int] = {}
    message: Message
    for message in messages:
        index = per_topic.get(message.topic, 0)
        per_topic[message.topic] = index + 1
        entry = Entry(
            topic=message.topic,
            schema_name=message.schema_name,
            channel_index=index,
            file_index=message.file_index,
            sequence=message.sequence,
            log_time=message.log_time,
            publish_time=message.publish_time,
            message_checksum=checksum(message.data),
            message_size=len(message.data),
        )
        if message.message_encoding == "cdr":
            _decode(entry, message.data)
        entries.append(entry)
    return RecordingFacts(file_checksum, size_bytes, entries)


def _file_checksum(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as handle:
        for block in iter(lambda: handle.read(1 << 22), b""):
            h.update(block)
    return "sha256:" + h.hexdigest()


def read_recording_facts(path: Path) -> RecordingFacts:
    return _facts(iter_messages(path), _file_checksum(path), path.stat().st_size)


def recording_facts_from_bytes(data: bytes) -> RecordingFacts:
    return _facts(iter_messages_from_bytes(data), checksum(data), len(data))
