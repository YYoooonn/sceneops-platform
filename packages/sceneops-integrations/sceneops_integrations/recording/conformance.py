"""L1 raw-recording conformance checks (ADR-007 §29.5).

One suite applied to every L1 writer's output -- the external acquisition
tool's batch MCAP today, capture output once capture's timing is corrected.
It checks what the recording bytes can prove:

    finalized   footer present; Statistics (if written) match the records
    R1          one channel definition per topic
    R2/R3       every channel has an embedded schema and encodings, and
                every message decodes with its own embedded schema
    R4/R6       log_time (recorder receive time) never decreases in write
                order; MCAP sequence numbers a channel carries increase
                strictly within that channel
    R9          every camera / range-sensor channel names a frame that a
                /tf_static or /tf transform recorded at or before its first
                message connects; every image channel has CameraInfo for
                its frame
    I-37        no SceneOps canonical identifiers in metadata, attachments
                or SceneOps-namespaced schemas

Source-time fidelity (R4), source sequence (R6) and event timelines (R11)
are writer obligations the bytes cannot prove. Writer tests check them
against source data, using the per-channel facts this report exposes.

The suite never reads acquisition-origin values to decide anything (I-32);
it only reports them. ``mcap`` / ``mcap_ros2`` are imported lazily, like
:mod:`.facts`.
"""

from __future__ import annotations

import hashlib
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from .reader import (
    ROS2_MESSAGE_ENCODING,
    ROS2_SCHEMA_ENCODING,
    TF_SCHEMA,
    header_stamps,
    stamp_ns,
)

ROS2_PROFILE = "ros2"

ACQUISITION_ORIGIN_METADATA = "sceneops.acquisition_origin"

CAMERA_INFO_SCHEMA = "sensor_msgs/msg/CameraInfo"
IMAGE_SCHEMAS = frozenset({"sensor_msgs/msg/Image", "sensor_msgs/msg/CompressedImage"})
# Observations whose interpretation needs an extrinsic calibration (R9).
FRAME_DEPENDENT_SCHEMAS = IMAGE_SCHEMAS | frozenset(
    {
        CAMERA_INFO_SCHEMA,
        "sensor_msgs/msg/PointCloud2",
        "sensor_msgs/msg/LaserScan",
        "sensor_msgs/msg/Range",
    }
)

# Canonical (L2) identifiers an L1 recording must not carry (I-37).
_CANONICAL_METADATA_KEYS = frozenset(
    {
        "scene_id",
        "scene_key",
        "episode_id",
        "episode_key",
        "unit_key",
        "dataset_id",
        "dataset_version",
        "dataset_version_id",
    }
)


@dataclass(frozen=True)
class ConformanceViolation:
    requirement: str
    check: str
    detail: str


@dataclass(frozen=True)
class ChannelReport:
    topic: str
    schema_name: str
    schema_encoding: str
    message_encoding: str
    message_count: int
    first_log_time_ns: int
    last_log_time_ns: int
    # Source observation stamps found in the payload (std_msgs/Header, or
    # the transforms of a TFMessage); None when the channel carries none.
    first_stamp_ns: int | None
    last_stamp_ns: int | None
    frame_ids: tuple[str, ...]
    sequenced: bool


@dataclass(frozen=True)
class ConformanceReport:
    profile: str
    message_count: int
    channels: dict[str, ChannelReport]
    metadata_names: tuple[str, ...]
    attachment_names: tuple[str, ...]
    acquisition_origin: dict[str, str] | None
    # sha256 over the logical message stream in write order: topic, schema,
    # encodings, log/publish time, sequence and payload digest per message.
    # Independent of chunking, compression and indexes.
    message_stream_sha256: str
    violations: tuple[ConformanceViolation, ...]

    @property
    def conforms(self) -> bool:
        return not self.violations

    def to_dict(self) -> dict[str, Any]:
        return {
            "conforms": self.conforms,
            "profile": self.profile,
            "message_count": self.message_count,
            "message_stream_sha256": self.message_stream_sha256,
            "metadata_names": list(self.metadata_names),
            "attachment_names": list(self.attachment_names),
            "acquisition_origin": self.acquisition_origin,
            "channels": {
                topic: {
                    "schema_name": c.schema_name,
                    "schema_encoding": c.schema_encoding,
                    "message_encoding": c.message_encoding,
                    "message_count": c.message_count,
                    "first_log_time_ns": c.first_log_time_ns,
                    "last_log_time_ns": c.last_log_time_ns,
                    "first_stamp_ns": c.first_stamp_ns,
                    "last_stamp_ns": c.last_stamp_ns,
                    "frame_ids": list(c.frame_ids),
                    "sequenced": c.sequenced,
                }
                for topic, c in sorted(self.channels.items())
            },
            "violations": [
                {"requirement": v.requirement, "check": v.check, "detail": v.detail}
                for v in self.violations
            ],
        }


@dataclass
class _ChannelState:
    topic: str
    schema_name: str
    schema_encoding: str
    message_encoding: str
    decoder: Any
    count: int = 0
    first_index: int = -1
    first_log_time: int = 0
    last_log_time: int = 0
    first_stamp: int | None = None
    last_stamp: int | None = None
    last_sequence: int | None = None
    sequenced: bool = False
    frame_ids: set[str] = field(default_factory=set)


class _Checker:
    def __init__(self, *, require_ros2_profile: bool) -> None:
        self.require_ros2_profile = require_ros2_profile
        self.violations: list[ConformanceViolation] = []
        self.profile = ""
        self.schemas: dict[int, Any] = {}
        self.channel_defs: dict[int, Any] = {}
        self.channels: dict[int, _ChannelState] = {}
        self.topic_channel: dict[str, int] = {}
        self.message_count = 0
        self.last_log_time: int | None = None
        self.metadata: list[tuple[str, dict[str, str]]] = []
        self.attachment_names: list[str] = []
        self.stats_message_count: int | None = None
        self.footer_seen = False
        self.stream_digest = hashlib.sha256()
        # child frame -> write index of the first transform that connects it
        self.tf_connected_at: dict[str, int] = {}
        self._decoder_factory: Any = None

    def violate(self, requirement: str, check: str, detail: str) -> None:
        self.violations.append(ConformanceViolation(requirement, check, detail))

    # -- record handlers ------------------------------------------------

    def on_schema(self, schema: Any) -> None:
        existing = self.schemas.get(schema.id)
        if existing is not None:
            if (existing.name, existing.encoding, existing.data) != (
                schema.name,
                schema.encoding,
                schema.data,
            ):
                self.violate(
                    "R3", "schema_id_reused", f"schema id {schema.id} redefined"
                )
            return
        self.schemas[schema.id] = schema

    def on_channel(self, channel: Any) -> None:
        existing = self.channel_defs.get(channel.id)
        if existing is not None:
            if (existing.topic, existing.schema_id, existing.message_encoding) != (
                channel.topic,
                channel.schema_id,
                channel.message_encoding,
            ):
                self.violate(
                    "R1", "channel_id_reused", f"channel id {channel.id} redefined"
                )
            return
        self.channel_defs[channel.id] = channel
        other = self.topic_channel.get(channel.topic)
        if other is not None:
            self.violate(
                "R1",
                "single_channel_definition",
                f"topic {channel.topic!r} has channel ids {other} and {channel.id}",
            )
            return
        self.topic_channel[channel.topic] = channel.id
        self.channels[channel.id] = self._channel_state(channel)

    def _channel_state(self, channel: Any) -> _ChannelState:
        schema = self.schemas.get(channel.schema_id)
        if schema is None or not schema.name or not schema.encoding or not schema.data:
            self.violate(
                "R3",
                "embedded_schema",
                f"topic {channel.topic!r} has no complete embedded schema "
                f"(schema_id={channel.schema_id})",
            )
        if not channel.message_encoding:
            self.violate("R3", "message_encoding", f"topic {channel.topic!r} has none")
        schema_name = schema.name if schema is not None else ""
        schema_encoding = schema.encoding if schema is not None else ""
        if self.require_ros2_profile and (
            channel.message_encoding != ROS2_MESSAGE_ENCODING
            or schema_encoding != ROS2_SCHEMA_ENCODING
        ):
            self.violate(
                "R3",
                "ros2_encoding_profile",
                f"topic {channel.topic!r} uses message_encoding="
                f"{channel.message_encoding!r} schema_encoding={schema_encoding!r}; "
                f"expected {ROS2_MESSAGE_ENCODING!r}/{ROS2_SCHEMA_ENCODING!r}",
            )
        decoder = None
        if (
            schema is not None
            and channel.message_encoding == ROS2_MESSAGE_ENCODING
            and schema_encoding == ROS2_SCHEMA_ENCODING
        ):
            try:
                decoder = self._factory().decoder_for(channel.message_encoding, schema)
            except Exception as exc:  # noqa: BLE001 - reported as a violation
                self.violate(
                    "R3",
                    "schema_parses",
                    f"topic {channel.topic!r} schema {schema_name!r} does not parse: {exc}",
                )
        return _ChannelState(
            topic=channel.topic,
            schema_name=schema_name,
            schema_encoding=schema_encoding,
            message_encoding=channel.message_encoding,
            decoder=decoder,
        )

    def _factory(self) -> Any:
        if self._decoder_factory is None:
            from mcap_ros2.decoder import DecoderFactory

            self._decoder_factory = DecoderFactory()
        return self._decoder_factory

    def on_message(self, message: Any) -> None:
        index = self.message_count
        self.message_count += 1
        state = self.channels.get(message.channel_id)
        if state is None:
            self.violate(
                "R1",
                "message_channel_defined",
                f"message {index} references undefined channel {message.channel_id}",
            )
            return

        self.stream_digest.update(
            "\x1f".join(
                (
                    state.topic,
                    state.schema_name,
                    state.schema_encoding,
                    state.message_encoding,
                    str(message.log_time),
                    str(message.publish_time),
                    str(message.sequence),
                    hashlib.sha256(message.data).hexdigest(),
                )
            ).encode()
            + b"\x1e"
        )

        if self.last_log_time is not None and message.log_time < self.last_log_time:
            self.violate(
                "R4",
                "receive_order",
                f"message {index} on {state.topic!r} has log_time {message.log_time} "
                f"< previous {self.last_log_time}",
            )
        self.last_log_time = message.log_time

        if state.count == 0:
            state.first_index = index
            state.first_log_time = message.log_time
        state.count += 1
        state.last_log_time = message.log_time

        if message.sequence:
            state.sequenced = True
        if state.sequenced:
            if (
                state.last_sequence is not None
                and message.sequence <= state.last_sequence
            ):
                self.violate(
                    "R6",
                    "channel_sequence",
                    f"{state.topic!r} sequence {message.sequence} after "
                    f"{state.last_sequence}",
                )
            state.last_sequence = message.sequence

        if state.decoder is None:
            return
        try:
            decoded = state.decoder(message.data)
        except Exception as exc:  # noqa: BLE001 - reported as a violation
            self.violate(
                "R2",
                "payload_decodes",
                f"message {index} on {state.topic!r} does not decode as "
                f"{state.schema_name!r}: {exc}",
            )
            state.decoder = None  # one report per channel
            return

        for header in header_stamps(decoded, state.schema_name):
            stamp = stamp_ns(header.stamp)
            state.first_stamp = (
                stamp if state.first_stamp is None else state.first_stamp
            )
            state.last_stamp = stamp
            if header.frame_id:
                state.frame_ids.add(header.frame_id)
        if state.schema_name == TF_SCHEMA:
            for transform in decoded.transforms:
                self.tf_connected_at.setdefault(transform.child_frame_id, index)
                self.tf_connected_at.setdefault(transform.header.frame_id, index)

    def on_metadata(self, record: Any) -> None:
        self.metadata.append((record.name, dict(record.metadata)))

    def on_attachment(self, record: Any) -> None:
        self.attachment_names.append(record.name)

    # -- whole-recording checks ------------------------------------------

    def finish(self) -> None:
        if not self.footer_seen:
            self.violate("finalized", "footer", "MCAP footer missing (not finalized)")
        if self.stats_message_count is not None and self.stats_message_count != (
            self.message_count
        ):
            self.violate(
                "finalized",
                "statistics",
                f"Statistics message_count {self.stats_message_count} != "
                f"{self.message_count} message records",
            )
        if self.message_count == 0:
            self.violate("R2", "non_empty", "recording contains zero messages")
        if self.require_ros2_profile and self.profile != ROS2_PROFILE:
            self.violate(
                "R3", "ros2_encoding_profile", f"MCAP profile is {self.profile!r}"
            )
        self._check_frames()
        self._check_canonical_semantics()

    def _check_frames(self) -> None:
        camera_info_frames = {
            frame
            for state in self.channels.values()
            if state.schema_name == CAMERA_INFO_SCHEMA
            for frame in state.frame_ids
        }
        for state in self.channels.values():
            if state.schema_name not in FRAME_DEPENDENT_SCHEMAS or state.count == 0:
                continue
            if not state.frame_ids:
                self.violate(
                    "R9", "sensor_frame", f"{state.topic!r} messages name no frame_id"
                )
                continue
            for frame in sorted(state.frame_ids):
                connected_at = self.tf_connected_at.get(frame)
                if connected_at is None or connected_at > state.first_index:
                    self.violate(
                        "R9",
                        "sensor_transform",
                        f"frame {frame!r} of {state.topic!r} is not connected by a "
                        f"transform recorded at or before its first message",
                    )
                if (
                    state.schema_name in IMAGE_SCHEMAS
                    and frame not in camera_info_frames
                ):
                    self.violate(
                        "R9",
                        "camera_info",
                        f"no CameraInfo recorded for frame {frame!r} of {state.topic!r}",
                    )

    def _check_canonical_semantics(self) -> None:
        names = [name for name, _ in self.metadata] + self.attachment_names
        for name in names:
            if name.startswith("sceneops.") and name != ACQUISITION_ORIGIN_METADATA:
                self.violate(
                    "I-37", "sceneops_records", f"unexpected SceneOps record {name!r}"
                )
        if (
            sum(1 for name, _ in self.metadata if name == ACQUISITION_ORIGIN_METADATA)
            > 1
        ):
            self.violate(
                "R12",
                "acquisition_origin",
                f"more than one {ACQUISITION_ORIGIN_METADATA!r} metadata record",
            )
        for name, values in self.metadata:
            forbidden = sorted(set(values) & _CANONICAL_METADATA_KEYS)
            if forbidden:
                self.violate(
                    "I-37",
                    "canonical_identifiers",
                    f"metadata {name!r} carries canonical keys {forbidden}",
                )
        for state in self.channels.values():
            if state.schema_name.split("/", 1)[0].startswith("sceneops"):
                self.violate(
                    "I-37",
                    "standard_messages",
                    f"{state.topic!r} uses SceneOps-namespaced schema "
                    f"{state.schema_name!r}",
                )

    def report(self) -> ConformanceReport:
        origin = next(
            (v for n, v in self.metadata if n == ACQUISITION_ORIGIN_METADATA), None
        )
        channels = {
            s.topic: ChannelReport(
                topic=s.topic,
                schema_name=s.schema_name,
                schema_encoding=s.schema_encoding,
                message_encoding=s.message_encoding,
                message_count=s.count,
                first_log_time_ns=s.first_log_time,
                last_log_time_ns=s.last_log_time,
                first_stamp_ns=s.first_stamp,
                last_stamp_ns=s.last_stamp,
                frame_ids=tuple(sorted(s.frame_ids)),
                sequenced=s.sequenced,
            )
            for s in self.channels.values()
            if s.count
        }
        return ConformanceReport(
            profile=self.profile,
            message_count=self.message_count,
            channels=channels,
            metadata_names=tuple(name for name, _ in self.metadata),
            attachment_names=tuple(self.attachment_names),
            acquisition_origin=origin,
            message_stream_sha256=f"sha256:{self.stream_digest.hexdigest()}",
            violations=tuple(self.violations),
        )


def check_l1_recording(
    path: Path, *, require_ros2_profile: bool = True
) -> ConformanceReport:
    """Scan every record of the MCAP at ``path`` once, in file order, and
    report its L1 conformance.

    ``require_ros2_profile`` enforces the SceneOps-produced encoding profile
    (``ros2`` / ``cdr`` / ``ros2msg``). An unreadable or corrupt file is
    reported as a ``finalized`` violation, never raised.
    """
    from mcap import records
    from mcap.stream_reader import StreamReader

    checker = _Checker(require_ros2_profile=require_ros2_profile)
    handlers = {
        records.Schema: checker.on_schema,
        records.Channel: checker.on_channel,
        records.Message: checker.on_message,
        records.Metadata: checker.on_metadata,
        records.Attachment: checker.on_attachment,
    }
    try:
        with path.open("rb") as stream:
            reader = StreamReader(stream, validate_crcs=True)
            for record in reader.records:
                handler = handlers.get(type(record))
                if handler is not None:
                    handler(record)
                elif isinstance(record, records.Header):
                    checker.profile = record.profile
                elif isinstance(record, records.Statistics):
                    checker.stats_message_count = record.message_count
                elif isinstance(record, records.Footer):
                    checker.footer_seen = True
    except Exception as exc:  # noqa: BLE001 - any read failure is non-conformance
        checker.violate(
            "finalized", "readable", f"MCAP is unreadable or corrupt: {exc}"
        )
    checker.finish()
    return checker.report()


__all__ = [
    "ACQUISITION_ORIGIN_METADATA",
    "ChannelReport",
    "ConformanceReport",
    "ConformanceViolation",
    "check_l1_recording",
]
