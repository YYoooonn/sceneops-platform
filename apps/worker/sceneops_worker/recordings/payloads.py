"""Shared recording-builder primitives (ADR-007 §30.4, §30.5, I-34).

Payload identity is a property of one recorded message and one extraction,
never of the domain unit that references it:

    payload artifact  payload-<sha256(robot_run_id, topic, channel_index,
                      extraction)[:32]>, owned by the RobotRun

so a Scene and an Episode built from the same RobotRun reference the same
OBSERVATION_PAYLOAD artifact when they extract the same message the same
way. ``channel_index`` is the message's occurrence index among its own
topic's messages (I-34 acquisition evidence); the id depends on no build
configuration.
"""

from __future__ import annotations

import hashlib
import re
from dataclasses import dataclass
from typing import Any, Final

from sceneops_core.artifacts.schemas.payload import PayloadRef, validate_media_type
from sceneops_core.common.canonical_json import canonical_json_bytes
from sceneops_core.robots.recording_payload import PayloadExtraction
from sceneops_integrations.recording import RecordingMessage

OBSERVATION_PAYLOAD_ID_SCHEMA_V1: Final = "sceneops.observation_payload_id/v1"
COMPRESSED_IMAGE_SCHEMA: Final = "sensor_msgs/msg/CompressedImage"
ROS2_CDR_MEDIA_TYPE_PREFIX: Final = "application/x.ros2-cdr."
_IMAGE_MAGIC: Final = {"image/jpeg": b"\xff\xd8\xff", "image/png": b"\x89PNG\r\n\x1a\n"}


class RecordingBuildError(RuntimeError):
    """A recording cannot be canonicalized faithfully under a configuration."""


@dataclass(frozen=True)
class RecordingRevision:
    """The registered recording being built from (from its RobotRunRecord
    and recording ArtifactRecord)."""

    robot_run_id: str
    recording_checksum: str
    recording_clock: str


@dataclass(frozen=True)
class PlannedPayload:
    artifact_id: str
    topic: str
    channel_index: int
    extraction: PayloadExtraction
    checksum: str
    size_bytes: int
    media_type: str

    def ref(self) -> PayloadRef:
        return PayloadRef(
            artifact_id=self.artifact_id,
            checksum=self.checksum,
            size_bytes=self.size_bytes,
            media_type=self.media_type,
        )


def topic_slug(topic: str) -> str:
    """A topic as a path-safe local-id fragment: ``/camera/front/image`` ->
    ``camera.front.image``."""
    slug = re.sub(r"[^A-Za-z0-9._-]", "_", topic.strip("/").replace("/", "."))
    slug = slug.lstrip("._-")
    if not slug:
        raise RecordingBuildError(f"topic {topic!r} has no usable id slug")
    return slug


def observation_payload_artifact_id(
    *, robot_run_id: str, topic: str, channel_index: int, extraction: PayloadExtraction
) -> str:
    document = {
        "payload_id_schema": OBSERVATION_PAYLOAD_ID_SCHEMA_V1,
        "robot_run_id": robot_run_id,
        "topic": topic,
        "channel_index": channel_index,
        "extraction": extraction.value,
    }
    return "payload-" + hashlib.sha256(canonical_json_bytes(document)).hexdigest()[:32]


def ros2_cdr_media_type(schema_name: str) -> str:
    return validate_media_type(
        ROS2_CDR_MEDIA_TYPE_PREFIX + schema_name.replace("/", ".").lower()
    )


def _compressed_image_media_type(image_format: str, topic: str) -> str:
    fmt = image_format.strip().lower()
    if fmt in {"jpeg", "jpg"} or fmt.endswith("jpeg compressed") or "; jpeg" in fmt:
        return "image/jpeg"
    if fmt == "png" or fmt.endswith("png compressed") or "; png" in fmt:
        return "image/png"
    raise RecordingBuildError(
        f"{topic!r}: CompressedImage format {image_format!r} has no supported "
        "media type (jpeg, png)"
    )


def extract_payload(
    extraction: PayloadExtraction, message: RecordingMessage, decoded: Any
) -> tuple[bytes, str]:
    """The canonical payload bytes of one message and their media type.
    Never decodes or re-encodes image or point data (Q2)."""
    if extraction == PayloadExtraction.COMPRESSED_IMAGE:
        if message.schema_name != COMPRESSED_IMAGE_SCHEMA:
            raise RecordingBuildError(
                f"{message.topic!r} is {message.schema_name!r}; compressed_image "
                f"extraction needs {COMPRESSED_IMAGE_SCHEMA!r}"
            )
        data = bytes(decoded.data)
        media_type = _compressed_image_media_type(decoded.format, message.topic)
        if not data.startswith(_IMAGE_MAGIC[media_type]):
            raise RecordingBuildError(
                f"message {message.channel_index} on {message.topic!r} declares "
                f"{decoded.format!r} but its bytes are not {media_type}"
            )
        return data, media_type
    return message.data, ros2_cdr_media_type(message.schema_name)


def plan_payload(
    *,
    robot_run_id: str,
    extraction: PayloadExtraction,
    message: RecordingMessage,
    decoded: Any,
) -> PlannedPayload:
    data, media_type = extract_payload(extraction, message, decoded)
    if not data:
        raise RecordingBuildError(
            f"message {message.channel_index} on {message.topic!r} has an empty payload"
        )
    return PlannedPayload(
        artifact_id=observation_payload_artifact_id(
            robot_run_id=robot_run_id,
            topic=message.topic,
            channel_index=message.channel_index,
            extraction=extraction,
        ),
        topic=message.topic,
        channel_index=message.channel_index,
        extraction=extraction,
        checksum="sha256:" + hashlib.sha256(data).hexdigest(),
        size_bytes=len(data),
        media_type=media_type,
    )


def canonical_order(items: list[Any], *, extra: str | None = None) -> list[Any]:
    """I-34: canonical timestamp, then MCAP sequence when every item carries
    one, then per-channel file order (and an optional tie key). Items need
    ``timestamp_ns``, ``sequence`` and ``channel_index``."""
    sequenced = all(i.sequence is not None for i in items)

    def key(item: Any) -> tuple:
        k: tuple = (item.timestamp_ns,)
        if sequenced:
            k += (item.sequence,)
        k += (item.channel_index,)
        if extra is not None:
            k += (getattr(item, extra),)
        return k

    return sorted(items, key=key)


__all__ = [
    "OBSERVATION_PAYLOAD_ID_SCHEMA_V1",
    "PlannedPayload",
    "RecordingBuildError",
    "RecordingRevision",
    "canonical_order",
    "extract_payload",
    "observation_payload_artifact_id",
    "plan_payload",
    "ros2_cdr_media_type",
    "topic_slug",
]
