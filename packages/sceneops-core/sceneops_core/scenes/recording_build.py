"""Build configuration of the recording Scene builder (ADR-007 §29.10, §30).

``RecordingSceneBuildConfig`` is everything that decides what a registered
recording means as canonical Scenes, and nothing else:

    channels       which recording topics are inside the Scene boundary, each
                   with its canonical modality, optional sensor id, canonical
                   observation-time policy and payload extraction
    frames         which source frames play the canonical world / ego roles
    calibration    which topics carry static transforms
    poses          which source transforms are poses
    segmentation   how the recording is cut into Scenes, on one declared clock

It names topics, frames and clocks verbatim and never a source format: the
same configuration applies to a recording from a real robot, a replayed
dataset or a batch-converted dataset (I-32). Its normalized form
(:meth:`RecordingSceneBuildConfig.normalized`) is the producer's
``ProducerInfo.build_config`` and therefore part of the producer
fingerprint; it holds no execution context.

Clocks (§27.3, §29.5 R4/R5)::

    log_time       MCAP Message.log_time     clock "mcap_log_time" (recording clock)
    publish_time   MCAP Message.publish_time clock "mcap_publish_time"
    header_stamp   the source timestamp the message carries (std_msgs/Header,
                   or each TransformStamped of a TFMessage), in the clock the
                   configuration declares for it

Segmentation clock (Q4): a Scene window is a half-open interval in exactly
one clock, ``segmentation.clock``. Every included channel and pose source
must be placeable on it: either its canonical time is in that clock, or the
segmentation clock is a recording clock every message carries
(``mcap_log_time`` / ``mcap_publish_time``). An observation whose canonical
time is in another clock keeps that time and is never compared with the
window.
"""

from __future__ import annotations

from enum import StrEnum
from typing import Annotated, Any, Final, Literal

from pydantic import (
    AfterValidator,
    BaseModel,
    ConfigDict,
    Field,
    StrictInt,
    StrictStr,
    model_validator,
)

from sceneops_core.common.identifiers import (
    validate_source_clock,
    validate_verbatim_key,
)
from sceneops_core.robots.clock import MCAP_LOG_TIME_CLOCK

from .schemas.enums import SceneModality

MCAP_PUBLISH_TIME_CLOCK: Final = "mcap_publish_time"
RECORDING_CLOCKS: Final = frozenset({MCAP_LOG_TIME_CLOCK, MCAP_PUBLISH_TIME_CLOCK})

DEFAULT_STATIC_TRANSFORM_TOPIC: Final = "/tf_static"

VerbatimKey = Annotated[
    StrictStr, AfterValidator(lambda v: validate_verbatim_key(v, field="source key"))
]
SourceClock = Annotated[StrictStr, AfterValidator(validate_source_clock)]


class TimeSource(StrEnum):
    """Which preserved timing fact is an observation's canonical time."""

    HEADER_STAMP = "header_stamp"
    LOG_TIME = "log_time"
    PUBLISH_TIME = "publish_time"


class PayloadExtraction(StrEnum):
    """How a message becomes a canonical payload (Q2, §30.4).

    ``compressed_image``  the ``data`` bytes of a ``sensor_msgs/msg/CompressedImage``,
                          unchanged; ``image/jpeg`` or ``image/png`` from its
                          ``format``
    ``ros2_message``      the recorded message bytes exactly as serialized
                          (CDR, encapsulation header included); the media type
                          names the ROS 2 message type
    """

    COMPRESSED_IMAGE = "compressed_image"
    ROS2_MESSAGE = "ros2_message"


class _ConfigModel(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)


class ObservationTimePolicy(_ConfigModel):
    source: TimeSource
    clock: SourceClock

    @model_validator(mode="after")
    def _check_clock(self) -> ObservationTimePolicy:
        fixed = {
            TimeSource.LOG_TIME: MCAP_LOG_TIME_CLOCK,
            TimeSource.PUBLISH_TIME: MCAP_PUBLISH_TIME_CLOCK,
        }.get(self.source)
        if fixed is not None and self.clock != fixed:
            raise ValueError(
                f"{self.source.value} is in clock {fixed!r}, not {self.clock!r}"
            )
        if fixed is None and self.clock in RECORDING_CLOCKS:
            raise ValueError(
                f"{self.clock!r} is a recording clock; a header stamp needs the "
                "identifier of the clock the source stamps it in"
            )
        return self


class SceneChannelConfig(_ConfigModel):
    topic: VerbatimKey
    modality: SceneModality
    sensor_id: VerbatimKey | None = None
    time: ObservationTimePolicy
    payload: PayloadExtraction
    # sensor_msgs/msg/CameraInfo topic carrying this camera's intrinsics.
    camera_info_topic: VerbatimKey | None = None

    @model_validator(mode="after")
    def _check_camera(self) -> SceneChannelConfig:
        if self.modality == SceneModality.CAMERA and self.camera_info_topic is None:
            raise ValueError(f"camera channel {self.topic!r} needs a camera_info_topic")
        if self.modality != SceneModality.CAMERA and self.camera_info_topic is not None:
            raise ValueError(f"camera_info_topic on non-camera channel {self.topic!r}")
        if (
            self.payload == PayloadExtraction.COMPRESSED_IMAGE
            and self.modality != SceneModality.CAMERA
        ):
            raise ValueError(
                f"compressed_image extraction on non-camera {self.topic!r}"
            )
        return self


class CoordinateFrameRoles(_ConfigModel):
    """Source frame names that play the canonical ego / world roles.
    Every channel's own frame plays the sensor role."""

    ego_frame_id: VerbatimKey
    world_frame_id: VerbatimKey | None = None

    @model_validator(mode="after")
    def _check_distinct(self) -> CoordinateFrameRoles:
        if self.world_frame_id == self.ego_frame_id:
            raise ValueError("ego and world frames must differ")
        return self


class CalibrationSources(_ConfigModel):
    """Topics whose ``tf2_msgs/msg/TFMessage`` transforms are static
    calibration. v1 holds calibration constant for the whole recording."""

    static_transform_topics: list[VerbatimKey] = Field(
        default_factory=lambda: [DEFAULT_STATIC_TRANSFORM_TOPIC], min_length=1
    )


class PoseSourceConfig(_ConfigModel):
    """Every transform ``parent_frame_id -> child_frame_id`` recorded on
    ``topic`` (a ``tf2_msgs/msg/TFMessage`` channel) is a source pose."""

    topic: VerbatimKey
    parent_frame_id: VerbatimKey
    child_frame_id: VerbatimKey
    time: ObservationTimePolicy


class FixedDurationSegmentation(_ConfigModel):
    """Half-open windows of ``duration_ns`` on ``clock``, starting at the
    earliest included observation's timestamp on that clock. A window with
    no observation is not a Scene. ``unit_key`` is ``segment-<index>``."""

    policy: Literal["fixed_duration"] = "fixed_duration"
    clock: SourceClock
    duration_ns: StrictInt = Field(ge=1)


class RecordingSceneBuildConfig(_ConfigModel):
    channels: list[SceneChannelConfig] = Field(min_length=1)
    frames: CoordinateFrameRoles
    calibration: CalibrationSources = Field(default_factory=CalibrationSources)
    poses: list[PoseSourceConfig] = Field(default_factory=list)
    segmentation: FixedDurationSegmentation

    @model_validator(mode="after")
    def _check_consistency(self) -> RecordingSceneBuildConfig:
        topics = [c.topic for c in self.channels]
        if len(topics) != len(set(topics)):
            raise ValueError("a topic is configured as more than one channel")
        pose_keys = [(p.topic, p.parent_frame_id, p.child_frame_id) for p in self.poses]
        if len(pose_keys) != len(set(pose_keys)):
            raise ValueError("a pose source is configured more than once")
        overlap = sorted(
            set(topics)
            & (
                {p.topic for p in self.poses}
                | set(self.calibration.static_transform_topics)
            )
        )
        if overlap:
            raise ValueError(
                f"topics {overlap} are both observation channels and transforms"
            )

        clock = self.segmentation.clock
        if clock not in RECORDING_CLOCKS:
            unplaceable = [c.topic for c in self.channels if c.time.clock != clock] + [
                p.topic for p in self.poses if p.time.clock != clock
            ]
            if unplaceable:
                raise ValueError(
                    f"segmentation clock {clock!r} is not a recording clock, so every "
                    f"channel and pose source must take its time from it; "
                    f"{sorted(set(unplaceable))} do not"
                )
        return self

    def normalized(self) -> dict[str, Any]:
        """``ProducerInfo.build_config``: defaults explicit, unordered
        collections sorted (§27.7)."""
        data = self.model_dump(mode="json")
        data["channels"] = sorted(data["channels"], key=lambda c: c["topic"])
        data["poses"] = sorted(
            data["poses"],
            key=lambda p: (p["topic"], p["parent_frame_id"], p["child_frame_id"]),
        )
        data["calibration"]["static_transform_topics"] = sorted(
            set(data["calibration"]["static_transform_topics"])
        )
        return data


__all__ = [
    "DEFAULT_STATIC_TRANSFORM_TOPIC",
    "MCAP_PUBLISH_TIME_CLOCK",
    "RECORDING_CLOCKS",
    "CalibrationSources",
    "CoordinateFrameRoles",
    "FixedDurationSegmentation",
    "ObservationTimePolicy",
    "PayloadExtraction",
    "PoseSourceConfig",
    "RecordingSceneBuildConfig",
    "SceneChannelConfig",
    "TimeSource",
]
