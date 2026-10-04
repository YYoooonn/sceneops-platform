"""Static streaming channel registry.

The set of ROS 2 topics the bridge subscribes to and capture accepts. One
declarative table drives both sides, so the bridge and capture can never
disagree about which channel carries which message type or where its
source timestamp lives.

A channel names a ROS 2 topic, its interface type, and how the bridge reads
the *source* timestamp out of the message for the envelope
(``TelemetryEnvelope.source_timestamp_ns``). The rule only locates a
timestamp the message already carries. No rule ever substitutes receive or
wall-clock time, and a message without a readable source timestamp fails
loudly at the bridge. A zero stamp is the one exception, per channel: static
data such as ``/tf_static`` carries no observation time, and its zero is
passed through verbatim (``allow_zero_stamp``).

The registry holds transport facts only. It names no Scene, Episode,
modality, sensor or dataset format. Sensor channels are deployment
configuration: a channel-set JSON file adds them to the defaults::

    {"channels": [
      {"topic": "/camera/front/image/compressed",
       "message_type": "sensor_msgs/msg/CompressedImage",
       "timestamp": "header"}
    ]}

There is no dynamic topic discovery. A topic outside the registry is never
subscribed to and is rejected by capture if an envelope names it.
"""

from __future__ import annotations

import json
from collections.abc import Iterable, Iterator, Mapping, Sequence
from enum import StrEnum
from pathlib import Path

from pydantic import Field, field_validator

from sceneops_core.common.schemas import SceneOpsBaseModel
from sceneops_core.constants.streaming import SESSION_CONTROL_CHANNEL


class TimestampRule(StrEnum):
    """Where the bridge reads a channel's source timestamp.

    HEADER            ``msg.header.stamp``
    TRANSFORM_HEADER  ``msg.transforms[0].header.stamp`` (``tf2_msgs/TFMessage``)
    JSON_FIELD        integer ``source_timestamp_ns`` in a JSON ``std_msgs/String``
    """

    HEADER = "header"
    TRANSFORM_HEADER = "transform_header"
    JSON_FIELD = "json_field"


class ChannelSpec(SceneOpsBaseModel):
    topic: str = Field(min_length=2)
    message_type: str
    timestamp: TimestampRule
    # A latched channel (static data, e.g. /tf_static) is subscribed with
    # transient-local durability, so a capture that starts after the
    # publisher still receives what the publisher already latched.
    latched: bool = False
    # Whether a message may legitimately carry a zero source timestamp (an
    # unstamped Header). Static data such as /tf_static has no observation
    # time; the zero is the source's own value, forwarded verbatim in the
    # payload and in the envelope, never replaced. On any other channel a
    # zero stamp is a missing observation time and fails loudly.
    allow_zero_stamp: bool = False
    # ROS 2 subscription history depth. None (the default) keeps every
    # received sample until the bridge has handled it: a source may emit
    # bursts (a sensor frame's messages share one instant) far larger than
    # any fixed depth, and DDS drops the oldest sample of a full keep-last
    # history without any signal. Set a depth only to bound memory
    # deliberately, accepting that overflow loses messages silently.
    queue_depth: int | None = Field(default=None, ge=1)

    @field_validator("topic")
    @classmethod
    def _topic_is_absolute(cls, value: str) -> str:
        if not value.startswith("/"):
            raise ValueError(f"topic must be an absolute ROS 2 name, got {value!r}")
        if value == SESSION_CONTROL_CHANNEL:
            raise ValueError(f"{value!r} is reserved for run lifecycle control events")
        return value

    @field_validator("message_type")
    @classmethod
    def _message_type_is_interface_name(cls, value: str) -> str:
        parts = value.split("/")
        if len(parts) != 3 or parts[1] != "msg" or not all(parts):
            raise ValueError(
                f"message_type must be '<package>/msg/<Name>', got {value!r}"
            )
        return value


DEFAULT_CHANNELS: tuple[ChannelSpec, ...] = (
    ChannelSpec(
        topic="/vehicle/odom",
        message_type="nav_msgs/msg/Odometry",
        timestamp=TimestampRule.HEADER,
    ),
    ChannelSpec(
        topic="/vehicle/imu",
        message_type="sensor_msgs/msg/Imu",
        timestamp=TimestampRule.HEADER,
    ),
    ChannelSpec(
        topic="/vehicle/status",
        message_type="sensor_msgs/msg/BatteryState",
        timestamp=TimestampRule.HEADER,
    ),
    ChannelSpec(
        topic="/vehicle/control",
        message_type="std_msgs/msg/String",
        timestamp=TimestampRule.JSON_FIELD,
    ),
    ChannelSpec(
        topic="/mission/status",
        message_type="std_msgs/msg/String",
        timestamp=TimestampRule.JSON_FIELD,
    ),
    ChannelSpec(
        topic="/tf",
        message_type="tf2_msgs/msg/TFMessage",
        timestamp=TimestampRule.TRANSFORM_HEADER,
    ),
    ChannelSpec(
        topic="/tf_static",
        message_type="tf2_msgs/msg/TFMessage",
        timestamp=TimestampRule.TRANSFORM_HEADER,
        latched=True,
        allow_zero_stamp=True,
    ),
)


class ChannelRegistry:
    """An immutable topic -> :class:`ChannelSpec` mapping."""

    def __init__(self, specs: Iterable[ChannelSpec]) -> None:
        by_topic: dict[str, ChannelSpec] = {}
        for spec in specs:
            existing = by_topic.get(spec.topic)
            if existing is not None and existing != spec:
                raise ValueError(
                    f"channel {spec.topic!r} is defined twice with different "
                    f"settings: {existing} vs {spec}"
                )
            by_topic[spec.topic] = spec
        self._by_topic: Mapping[str, ChannelSpec] = by_topic

    def __iter__(self) -> Iterator[ChannelSpec]:
        return iter(self._by_topic.values())

    def __len__(self) -> int:
        return len(self._by_topic)

    def __contains__(self, topic: object) -> bool:
        return topic in self._by_topic

    def get(self, topic: str) -> ChannelSpec | None:
        return self._by_topic.get(topic)

    def topics(self) -> list[str]:
        return list(self._by_topic)

    def message_types(self) -> dict[str, str]:
        return {topic: spec.message_type for topic, spec in self._by_topic.items()}

    def is_supported(self, *, channel: str, message_type: str) -> bool:
        spec = self._by_topic.get(channel)
        return spec is not None and spec.message_type == message_type


def load_channel_file(path: Path) -> list[ChannelSpec]:
    document = json.loads(Path(path).read_text())
    if not isinstance(document, dict) or not isinstance(document.get("channels"), list):
        raise ValueError(f"{path}: expected an object with a 'channels' list")
    return [ChannelSpec.model_validate(entry) for entry in document["channels"]]


def build_channel_registry(
    channel_files: Sequence[Path] = (),
    *,
    include_defaults: bool = True,
) -> ChannelRegistry:
    """The default channels plus every channel declared in ``channel_files``."""
    specs: list[ChannelSpec] = list(DEFAULT_CHANNELS) if include_defaults else []
    for path in channel_files:
        specs.extend(load_channel_file(path))
    return ChannelRegistry(specs)


DEFAULT_REGISTRY = ChannelRegistry(DEFAULT_CHANNELS)
