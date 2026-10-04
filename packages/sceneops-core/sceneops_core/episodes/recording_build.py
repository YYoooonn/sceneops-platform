"""Build configuration of the recording Episode builder (ADR-007 §29.10, §31).

``RecordingEpisodeBuildConfig`` is everything that decides what a registered
recording means as canonical Episodes, and nothing else:

    streams        which recording topics are inside the Episode boundary, each
                   with its role (observation, state, action), canonical time
                   policy, payload decoding, selected fields and, for
                   observations, an optional payload extraction
    events         which topics carry task / event markers recorded at
                   acquisition time, with their selected fields
    segmentation   how the recording is cut into Episodes, on one declared clock

It names topics, field paths and clocks verbatim and never a robot, a field
convention or a source format: steering / throttle / brake, joint commands
or a mission topic are one configuration, not the Episode model (I-32). Its
normalized form (:meth:`RecordingEpisodeBuildConfig.normalized`) is the
producer's ``ProducerInfo.build_config`` and therefore part of the producer
fingerprint; it holds no execution context.

Canonical time per stream (§29.5, I-33)::

    log_time        MCAP Message.log_time      clock "mcap_log_time"
    publish_time    MCAP Message.publish_time  clock "mcap_publish_time"
    header_stamp    the message's std_msgs/Header stamp, in the declared clock
    payload_field   an integer-nanosecond field of the decoded payload (e.g. the
                    ``source_timestamp_ns`` of a JSON-in-String message), in the
                    declared clock

Segmentation clock (§30.2, applied to Episodes): an Episode window is a
half-open interval in exactly one clock. Every stream and event source must
be placeable on it: either its canonical time is in that clock, or the
segmentation clock is a recording clock every message carries. Timestamps
in any other clock are never compared with the window.

Nothing here aligns, resamples or associates streams. Those are derived
(``ALIGN_EPISODE``).
"""

from __future__ import annotations

import re
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
    validate_local_id,
    validate_source_clock,
    validate_verbatim_key,
)
from sceneops_core.robots.clock import (
    MCAP_LOG_TIME_CLOCK,
    MCAP_PUBLISH_TIME_CLOCK,
    RECORDING_CLOCKS,
)
from sceneops_core.robots.recording_payload import PayloadExtraction

FIELD_PATH_PATTERN: Final = r"^[A-Za-z_][A-Za-z0-9_]*(\.[A-Za-z_][A-Za-z0-9_]*)*$"
_FIELD_PATH_RE = re.compile(FIELD_PATH_PATTERN)


def _validate_field_path(value: str) -> str:
    if not isinstance(value, str) or not _FIELD_PATH_RE.fullmatch(value):
        raise ValueError(
            f"field path must be dotted identifiers ({FIELD_PATH_PATTERN}), got {value!r}"
        )
    return value


VerbatimKey = Annotated[
    StrictStr, AfterValidator(lambda v: validate_verbatim_key(v, field="source key"))
]
SourceClock = Annotated[StrictStr, AfterValidator(validate_source_clock)]
FieldName = Annotated[
    StrictStr, AfterValidator(lambda v: validate_local_id(v, field="field name"))
]
FieldPath = Annotated[StrictStr, AfterValidator(_validate_field_path)]


class EpisodeStreamRole(StrEnum):
    """What a recorded stream is to the Episode. ``event`` is reserved for
    task / event sources (``RecordingEpisodeBuildConfig.events``)."""

    OBSERVATION = "observation"
    STATE = "state"
    ACTION = "action"
    EVENT = "event"


class EpisodeTimeSource(StrEnum):
    HEADER_STAMP = "header_stamp"
    LOG_TIME = "log_time"
    PUBLISH_TIME = "publish_time"
    PAYLOAD_FIELD = "payload_field"


class PayloadDecoding(StrEnum):
    """How field paths are resolved against a message.

    ``ros2``         against the ROS 2 message decoded with the schema the
                     recording embeds
    ``json_string``  against the JSON object carried in the ``data`` of a
                     ``std_msgs/msg/String`` (a recording convention for
                     messages without a standard ROS 2 type)
    """

    ROS2 = "ros2"
    JSON_STRING = "json_string"


class _ConfigModel(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)


class EpisodeTimePolicy(_ConfigModel):
    source: EpisodeTimeSource
    clock: SourceClock
    # payload_field only: the field holding integer nanoseconds.
    field: FieldPath | None = None

    @model_validator(mode="after")
    def _check(self) -> EpisodeTimePolicy:
        fixed = {
            EpisodeTimeSource.LOG_TIME: MCAP_LOG_TIME_CLOCK,
            EpisodeTimeSource.PUBLISH_TIME: MCAP_PUBLISH_TIME_CLOCK,
        }.get(self.source)
        if fixed is not None and self.clock != fixed:
            raise ValueError(
                f"{self.source.value} is in clock {fixed!r}, not {self.clock!r}"
            )
        if fixed is None and self.clock in RECORDING_CLOCKS:
            raise ValueError(
                f"{self.clock!r} is a recording clock; a source timestamp needs the "
                "identifier of the clock the source stamps it in"
            )
        if (self.source == EpisodeTimeSource.PAYLOAD_FIELD) != (self.field is not None):
            raise ValueError("field is required for, and only for, payload_field time")
        return self


class FieldSelection(_ConfigModel):
    """One recorded field kept as a canonical value: ``path`` in the
    message, stored under ``name``. The value is kept as the source holds it
    (bool, integer, float, string or a numeric array); a path that resolves
    to a nested message or to raw bytes is rejected."""

    name: FieldName
    path: FieldPath


def _check_field_names(fields: list[FieldSelection], topic: str) -> None:
    names = [f.name for f in fields]
    if len(names) != len(set(names)):
        raise ValueError(f"{topic!r} selects two fields under one name")


class EpisodeStreamConfig(_ConfigModel):
    topic: VerbatimKey
    role: Literal[
        EpisodeStreamRole.OBSERVATION, EpisodeStreamRole.STATE, EpisodeStreamRole.ACTION
    ]
    time: EpisodeTimePolicy
    decoding: PayloadDecoding = PayloadDecoding.ROS2
    fields: list[FieldSelection] = Field(default_factory=list)
    # observation only: the message becomes a SceneOps-owned payload artifact.
    payload: PayloadExtraction | None = None

    @model_validator(mode="after")
    def _check(self) -> EpisodeStreamConfig:
        _check_field_names(self.fields, self.topic)
        if self.payload is not None and self.role != EpisodeStreamRole.OBSERVATION:
            raise ValueError(
                f"{self.topic!r}: only an observation stream has a payload"
            )
        if self.payload is not None and self.decoding != PayloadDecoding.ROS2:
            raise ValueError(f"{self.topic!r}: a payload stream is decoded as ros2")
        if not self.fields and self.payload is None:
            raise ValueError(f"{self.topic!r} selects neither fields nor a payload")
        return self


class EpisodeEventSourceConfig(_ConfigModel):
    """Task / event markers recorded at acquisition time (§29.5 R11). Every
    marker is kept as an Episode event with its selected fields; none is
    interpreted as success, reward or a label."""

    topic: VerbatimKey
    time: EpisodeTimePolicy
    decoding: PayloadDecoding = PayloadDecoding.ROS2
    fields: list[FieldSelection] = Field(min_length=1)

    @model_validator(mode="after")
    def _check(self) -> EpisodeEventSourceConfig:
        _check_field_names(self.fields, self.topic)
        return self


class WholeRecordingSegmentation(_ConfigModel):
    """One Episode: ``[earliest, latest + 1)`` of every included message's
    timestamp on ``clock``. ``unit_key`` is ``recording``."""

    policy: Literal["whole_recording"] = "whole_recording"
    clock: SourceClock


class FixedDurationEpisodeSegmentation(_ConfigModel):
    """Half-open windows of ``duration_ns`` on ``clock`` from the earliest
    included message. A window with no message is not an Episode.
    ``unit_key`` is ``segment-<index>``."""

    policy: Literal["fixed_duration"] = "fixed_duration"
    clock: SourceClock
    duration_ns: StrictInt = Field(ge=1)


class EventMarkerSegmentation(_ConfigModel):
    """One Episode per start / end marker pair recorded on ``event_topic``
    (an event source). A marker's ``key_field`` value identifies the task;
    its ``state_field`` value opens the Episode (``start_values``) or closes
    it (``end_values``); any other value is an ordinary event. The window is
    ``[start marker, end marker + 1)`` on the event source's clock, so the
    end marker belongs to its Episode. ``unit_key`` is
    ``task-<key>-<occurrence>``. Markers that do not pair up (an end without
    a start, a second start, a start never ended) fail the build."""

    policy: Literal["event_markers"] = "event_markers"
    event_topic: VerbatimKey
    key_field: FieldName
    state_field: FieldName
    start_values: list[VerbatimKey] = Field(min_length=1)
    end_values: list[VerbatimKey] = Field(min_length=1)

    @model_validator(mode="after")
    def _check(self) -> EventMarkerSegmentation:
        if self.key_field == self.state_field:
            raise ValueError("key_field and state_field must differ")
        if set(self.start_values) & set(self.end_values):
            raise ValueError("a state value cannot both start and end an Episode")
        return self


EpisodeSegmentation = Annotated[
    WholeRecordingSegmentation
    | FixedDurationEpisodeSegmentation
    | EventMarkerSegmentation,
    Field(discriminator="policy"),
]


class RecordingEpisodeBuildConfig(_ConfigModel):
    streams: list[EpisodeStreamConfig] = Field(min_length=1)
    events: list[EpisodeEventSourceConfig] = Field(default_factory=list)
    segmentation: EpisodeSegmentation

    @model_validator(mode="after")
    def _check_consistency(self) -> RecordingEpisodeBuildConfig:
        topics = [s.topic for s in self.streams] + [e.topic for e in self.events]
        if len(topics) != len(set(topics)):
            raise ValueError("a topic is configured as more than one stream or event")

        clock = self.segmentation_clock()
        if clock not in RECORDING_CLOCKS:
            unplaceable = sorted(
                {
                    s.topic
                    for s in [*self.streams, *self.events]
                    if s.time.clock != clock
                }
            )
            if unplaceable:
                raise ValueError(
                    f"segmentation clock {clock!r} is not a recording clock, so every "
                    f"stream and event source must take its time from it; "
                    f"{unplaceable} do not"
                )
        return self

    def event_source(self, topic: str) -> EpisodeEventSourceConfig | None:
        return next((e for e in self.events if e.topic == topic), None)

    def segmentation_clock(self) -> str:
        segmentation = self.segmentation
        if isinstance(segmentation, EventMarkerSegmentation):
            source = self.event_source(segmentation.event_topic)
            if source is None:
                raise ValueError(
                    f"event_markers segmentation reads {segmentation.event_topic!r}, "
                    "which is not a configured event source"
                )
            names = {f.name for f in source.fields}
            missing = sorted({segmentation.key_field, segmentation.state_field} - names)
            if missing:
                raise ValueError(
                    f"event source {source.topic!r} selects no field named {missing}"
                )
            return source.time.clock
        return segmentation.clock

    def normalized(self) -> dict[str, Any]:
        """``ProducerInfo.build_config``: defaults explicit, unordered
        collections sorted (§27.7)."""
        data = self.model_dump(mode="json")
        for source in [*data["streams"], *data["events"]]:
            source["fields"] = sorted(source["fields"], key=lambda f: f["name"])
        data["streams"] = sorted(data["streams"], key=lambda s: s["topic"])
        data["events"] = sorted(data["events"], key=lambda e: e["topic"])
        segmentation = data["segmentation"]
        for key in ("start_values", "end_values"):
            if key in segmentation:
                segmentation[key] = sorted(set(segmentation[key]))
        return data


__all__ = [
    "FIELD_PATH_PATTERN",
    "EpisodeEventSourceConfig",
    "EpisodeSegmentation",
    "EpisodeStreamConfig",
    "EpisodeStreamRole",
    "EpisodeTimePolicy",
    "EpisodeTimeSource",
    "EventMarkerSegmentation",
    "FieldSelection",
    "FixedDurationEpisodeSegmentation",
    "PayloadDecoding",
    "RecordingEpisodeBuildConfig",
    "WholeRecordingSegmentation",
]
