"""EpisodeManifest v3 -- the canonical, source-faithful description of one
Episode (ADR-007 §13.10, §31; ADR-015 §6.6).

An Episode is a task / behavior-oriented projection of one source unit: a
window of a registered RobotRun recording (``RecordingSegmentSource``) or
one native episode of a registered SourceContent (``ContentUnitSource``).
Within its window it keeps every occurrence of every included stream --
observations, states, actions, per-frame transitions and task / event
markers -- each at its own canonical timestamp in its stream's clock, with
the field values the build configuration selects, kept as the source holds
them, and, for observations, a payload reference.

Streams stay as the source has them. Recording streams are asynchronous:
observations at t0, t3, t8, states at t1, t2, t7 and actions at t2, t5, t9
are kept exactly so. A source sampled on a frame grid declares one
``frame_timeline`` and every occurrence on its clock lies on that grid.
Nothing is resampled, interpolated, forward-filled, padded, associated with
a nearest frame or put on a common timeline. Two occurrences with equal
timestamps stay two occurrences. Temporal alignment is a derived L3
operation (``ALIGN_EPISODE`` -> ``AlignedEpisode``), never part of this
manifest.

The manifest holds only facts the source contains. It carries no success
label, outcome, language instruction or subtask boundary unless a source
stream carries one, and then only as that stream's values: a ``transition``
stream keeps a per-frame signal (a reward, a done flag) verbatim and no event
is derived from it.

The manifest describes the unit, not its membership: no DatasetVersion,
episode id, record state, SourceSnapshot, upstream declaration or execution
context. Identical source, producer and build configuration produce
byte-identical manifests whatever DatasetVersion registers them. The unit id
is derived from the source binding (``episode_id_for``) and the revision is
the manifest checksum (``sceneops_core.provenance.revisions``); neither is
stored here.

Every clock the window, the frame timeline or a stream names is declared
exactly once in ``clock_domains`` with its origin (ADR-015 §6.2), and every
declared clock is used.

Representation conventions fixed by ``sceneops.episode_manifest/v3``:

    time       integer nanoseconds in a declared clock domain, one clock per
               stream; on a frame timeline, round_half_even(k · 10⁹ / fps)
               for the frame index k, which is therefore not stored
    values     bool, integer, finite float, string, or a list of numbers, exactly
               as decoded from the source (a float32 as its exact float64
               widening); a field with component_names holds lists of exactly
               that length
    payloads   referenced by artifact identity (ArtifactPayloadRef) or by
               preserved file of the unit's own content (SourceFileRef), with
               integrity, never by location
    native_ref the source's own identifier of an occurrence, verbatim

All bytes go through :meth:`EpisodeManifest.to_canonical_bytes` and
:func:`load_canonical_episode_manifest`; the loader rejects bytes that parse
but are not already canonical.
"""

from __future__ import annotations

import json
import math
from collections.abc import Iterable
from dataclasses import dataclass
from typing import Annotated, Any, Final, Literal

from pydantic import (
    AfterValidator,
    BaseModel,
    BeforeValidator,
    ConfigDict,
    Field,
    StrictBool,
    StrictInt,
    StrictStr,
    ValidationError,
    model_validator,
)

from sceneops_core.artifacts.schemas.payload import PayloadRef, SourceFileRef
from sceneops_core.common.canonical_json import canonical_json_bytes
from sceneops_core.common.checksums import sha256_checksum
from sceneops_core.common.identifiers import (
    validate_local_id,
    validate_source_clock,
    validate_verbatim_key,
)
from sceneops_core.provenance import (
    ClockDomain,
    ClockInstance,
    ClockOrigin,
    ProducerInfo,
    SourceBinding,
    SourceTimestampNs,
    check_clock_domains,
    clock_instance,
    frame_index_of,
    frame_time_ns,
)

from ..recording_build import EpisodeStreamRole, FieldPath

EPISODE_MANIFEST_SCHEMA_V3: Final = "sceneops.episode_manifest/v3"


class EpisodeManifestError(ValueError):
    """Bytes are not a valid canonical EpisodeManifest."""


class UnsupportedEpisodeManifestVersionError(EpisodeManifestError):
    pass


class NonCanonicalEpisodeManifestError(EpisodeManifestError):
    """The bytes parse as a valid manifest but are not byte-identical to its
    canonical serialization."""


def _finite_float(value: Any) -> float:
    if not isinstance(value, float):
        raise ValueError(f"expected a float, got {type(value).__name__}")
    if not math.isfinite(value):
        raise ValueError("NaN and Infinity are not representable")
    return value


FiniteFloat = Annotated[float, BeforeValidator(_finite_float)]
Number = StrictInt | FiniteFloat
EpisodeValue = StrictBool | StrictInt | FiniteFloat | StrictStr | list[Number]

LocalId = Annotated[
    StrictStr, AfterValidator(lambda v: validate_local_id(v, field="local id"))
]
VerbatimKey = Annotated[
    StrictStr, AfterValidator(lambda v: validate_verbatim_key(v, field="source key"))
]
SourceClock = Annotated[StrictStr, AfterValidator(validate_source_clock)]


class _ManifestModel(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)


class EpisodeField(_ManifestModel):
    """A selected field: kept under ``name``, read from ``path``.

    ``component_names`` are the source's names of a vector value's
    dimensions, in source order; every value of the field is then a list of
    exactly that length."""

    name: LocalId
    path: FieldPath
    component_names: list[VerbatimKey] | None = Field(default=None, min_length=1)


class EpisodeStream(_ManifestModel):
    """One recorded stream included in the Episode.

    ``topic`` is the recording's own channel identity, verbatim;
    ``schema_name`` its recorded message type. ``source_clock`` is the clock
    of every occurrence's timestamp. ``role`` and ``fields`` are canonical
    semantics from the build configuration. A stream with no occurrence in
    the window is kept, so the absence stays visible.
    """

    topic: VerbatimKey
    role: EpisodeStreamRole
    schema_name: VerbatimKey
    source_clock: SourceClock
    fields: list[EpisodeField] = Field(default_factory=list)
    has_payload: StrictBool = False

    @model_validator(mode="after")
    def _check_fields(self) -> EpisodeStream:
        names = [f.name for f in self.fields]
        if names != sorted(set(names)):
            raise ValueError(f"fields of {self.topic!r} must be sorted and unique")
        if self.has_payload and self.role != EpisodeStreamRole.OBSERVATION:
            raise ValueError(
                f"{self.topic!r}: only an observation stream has a payload"
            )
        return self


class EpisodeOccurrence(_ManifestModel):
    """One source occurrence of a stream (a recorded message, a frame's
    value), at its own canonical time in its stream's clock.
    ``occurrence_id`` is producer-assigned and unique in the manifest.
    ``native_ref`` is the source's own identifier of the occurrence, verbatim
    (a LeRobot global ``index``), where the source has one."""

    occurrence_id: LocalId
    topic: VerbatimKey
    timestamp_ns: SourceTimestampNs
    values: dict[str, EpisodeValue] = Field(default_factory=dict)
    payload: PayloadRef | None = None
    native_ref: VerbatimKey | None = None


class FrameTimeline(_ManifestModel):
    """The frame grid an Episode was sampled on: frame ``k`` of
    ``[0, frame_count)`` lies at ``round_half_even(k · 10⁹ / fps)`` on
    ``clock``, whose origin is the unit itself. ``fps`` is stored here once,
    never on the clock domain: a sampling rate does not decide
    comparability."""

    clock: SourceClock
    fps: StrictInt = Field(ge=1)
    frame_count: StrictInt = Field(ge=1)

    def time_ns(self, frame_index: int) -> int:
        return frame_time_ns(frame_index, self.fps)

    def frame_index(self, timestamp_ns: int) -> int | None:
        """The frame at ``timestamp_ns``, or None when it is off the grid or
        outside ``[0, frame_count)``."""
        k = frame_index_of(timestamp_ns, self.fps)
        return k if k is not None and k < self.frame_count else None

    def window(self) -> tuple[int, int]:
        """``[0, t(frame_count - 1) + 1)``."""
        return 0, self.time_ns(self.frame_count - 1) + 1


class EpisodeLineage(_ManifestModel):
    """The source unit the Episode was built from and the producer that
    built it. The fingerprint must re-derive from the source revision."""

    source: SourceBinding
    producer: ProducerInfo

    @model_validator(mode="after")
    def _check_fingerprint(self) -> EpisodeLineage:
        self.producer.verify(self.source.source_revision())
        return self


@dataclass(frozen=True)
class EpisodeTimeWindow:
    """A half-open interval ``[start, end)`` in one clock."""

    source_clock: str
    start_timestamp_ns: int
    end_timestamp_ns: int


_ROLE_LISTS: Final = {
    EpisodeStreamRole.OBSERVATION: "observations",
    EpisodeStreamRole.STATE: "states",
    EpisodeStreamRole.ACTION: "actions",
    EpisodeStreamRole.EVENT: "events",
    EpisodeStreamRole.TRANSITION: "transitions",
}


class EpisodeManifest(_ManifestModel):
    schema_version: Literal["sceneops.episode_manifest/v3"] = EPISODE_MANIFEST_SCHEMA_V3
    lineage: EpisodeLineage
    clock_domains: list[ClockDomain] = Field(min_length=1)
    frame_timeline: FrameTimeline | None = None

    streams: list[EpisodeStream] = Field(min_length=1)
    observations: list[EpisodeOccurrence] = Field(default_factory=list)
    states: list[EpisodeOccurrence] = Field(default_factory=list)
    actions: list[EpisodeOccurrence] = Field(default_factory=list)
    events: list[EpisodeOccurrence] = Field(default_factory=list)
    transitions: list[EpisodeOccurrence] = Field(default_factory=list)

    @model_validator(mode="after")
    def _check_invariants(self) -> EpisodeManifest:
        topics = [s.topic for s in self.streams]
        if topics != sorted(set(topics)):
            raise ValueError("streams must be sorted by topic and unique")
        streams = {s.topic: s for s in self.streams}

        seen_ids: set[str] = set()
        for role, attribute in _ROLE_LISTS.items():
            items: list[EpisodeOccurrence] = getattr(self, attribute)
            keys = [(o.topic, o.timestamp_ns, o.occurrence_id) for o in items]
            if keys != sorted(keys):
                raise ValueError(f"{attribute} are not in canonical order")
            for occurrence in items:
                if occurrence.occurrence_id in seen_ids:
                    raise ValueError(
                        f"duplicate occurrence id {occurrence.occurrence_id!r}"
                    )
                seen_ids.add(occurrence.occurrence_id)
                stream = streams.get(occurrence.topic)
                if stream is None or stream.role != role:
                    raise ValueError(
                        f"{attribute} holds {occurrence.occurrence_id!r} of "
                        f"{occurrence.topic!r}, which is not a {role.value} stream"
                    )
                expected = [f.name for f in stream.fields]
                if sorted(occurrence.values) != expected:
                    raise ValueError(
                        f"{occurrence.occurrence_id!r} has values "
                        f"{sorted(occurrence.values)}, its stream selects {expected}"
                    )
                if (occurrence.payload is not None) != stream.has_payload:
                    raise ValueError(
                        f"{occurrence.occurrence_id!r}: payload presence does not "
                        f"match stream {stream.topic!r}"
                    )
                _check_component_lengths(occurrence, stream)
        if not seen_ids:
            raise ValueError("an Episode holds at least one recorded occurrence")
        check_clock_domains(
            self.clock_domains,
            source=self.lineage.source,
            referenced=[
                self.lineage.source.source_clock,
                *(s.source_clock for s in self.streams),
                *([self.frame_timeline.clock] if self.frame_timeline else []),
            ],
            timestamps=self.clocked_timestamps(),
        )
        self._check_source_window()
        self._check_source_files()
        self._check_frame_timeline()
        return self

    def _check_source_files(self) -> None:
        """A preserved-file reference names a file of the unit's own content:
        a unit reads one source, so a reference to any other content (or from
        a recording-sourced unit) is a wrong binding."""
        source = self.lineage.source
        for occurrence in self.occurrences():
            payload = occurrence.payload
            if not isinstance(payload, SourceFileRef):
                continue
            if (
                source.source_kind != "content"
                or payload.content_id != source.content_id
            ):
                raise ValueError(
                    f"{occurrence.occurrence_id!r} references a file of "
                    f"{payload.content_id}, not of the Episode's own source "
                    f"{source.source_id}"
                )

    def _check_frame_timeline(self) -> None:
        """With a frame timeline: its clock is the window's and has origin
        ``unit``; the window is ``[0, t(frame_count - 1) + 1)``; every
        occurrence on its clock lies on a frame of the grid; every
        ``transition`` stream has exactly one occurrence per frame. Without
        one, no stream is a ``transition`` stream."""
        timeline = self.frame_timeline
        transition_topics = [
            s.topic for s in self.streams if s.role == EpisodeStreamRole.TRANSITION
        ]
        if timeline is None:
            if transition_topics:
                raise ValueError(
                    f"transition streams {transition_topics} need a frame_timeline"
                )
            return
        source = self.lineage.source
        if source.source_clock != timeline.clock:
            raise ValueError(
                f"the window is on {source.source_clock!r}, the frame timeline on "
                f"{timeline.clock!r}"
            )
        if self.clock_domain(timeline.clock).origin != ClockOrigin.UNIT:
            raise ValueError(
                f"frame timeline clock {timeline.clock!r} must have origin "
                f"{ClockOrigin.UNIT.value!r}: its frame 0 is the unit's own origin"
            )
        window = (source.start_timestamp_ns, source.end_timestamp_ns)
        if window != timeline.window():
            raise ValueError(
                f"window {list(window)} disagrees with the frame timeline "
                f"(fps {timeline.fps}, {timeline.frame_count} frames): "
                f"{list(timeline.window())}"
            )
        clocks = {s.topic: s.source_clock for s in self.streams}
        frames: dict[str, list[int]] = {topic: [] for topic in transition_topics}
        for occurrence in self.occurrences():
            if clocks[occurrence.topic] != timeline.clock:
                continue
            k = timeline.frame_index(occurrence.timestamp_ns)
            if k is None:
                raise ValueError(
                    f"{occurrence.occurrence_id!r} at {occurrence.timestamp_ns} ns is "
                    f"not a frame of the timeline (fps {timeline.fps}, "
                    f"{timeline.frame_count} frames)"
                )
            if occurrence.topic in frames:
                frames[occurrence.topic].append(k)
        every_frame = list(range(timeline.frame_count))
        for topic, seen in frames.items():
            if clocks[topic] != timeline.clock:
                raise ValueError(
                    f"transition stream {topic!r} is not on the frame timeline clock"
                )
            if sorted(seen) != every_frame:
                raise ValueError(
                    f"transition stream {topic!r} must have exactly one occurrence "
                    f"per frame of [0, {timeline.frame_count})"
                )

    def _check_source_window(self) -> None:
        """Every timestamp in the window's clock lies inside the half-open
        window; timestamps in other clocks are never compared with it."""
        source = self.lineage.source
        for clock, timestamp in self.clocked_timestamps():
            if clock == source.source_clock and not source.contains(timestamp):
                raise ValueError(
                    f"timestamp {timestamp} ({clock}) lies outside the episode "
                    f"window [{source.start_timestamp_ns}, {source.end_timestamp_ns})"
                )

    def occurrences(self) -> Iterable[EpisodeOccurrence]:
        for attribute in _ROLE_LISTS.values():
            yield from getattr(self, attribute)

    def occurrences_of(self, role: EpisodeStreamRole) -> list[EpisodeOccurrence]:
        return list(getattr(self, _ROLE_LISTS[role]))

    def clocked_timestamps(self) -> Iterable[tuple[str, int]]:
        clock = {s.topic: s.source_clock for s in self.streams}
        for occurrence in self.occurrences():
            yield clock[occurrence.topic], occurrence.timestamp_ns

    # --- serialization ---------------------------------------------------------

    def to_canonical_bytes(self) -> bytes:
        return canonical_json_bytes(self.model_dump(mode="json"))

    def checksum(self) -> str:
        return sha256_checksum(self.to_canonical_bytes())

    # --- read helpers ----------------------------------------------------------

    def declared_window(self) -> EpisodeTimeWindow:
        source = self.lineage.source
        return EpisodeTimeWindow(
            source_clock=source.source_clock,
            start_timestamp_ns=source.start_timestamp_ns,
            end_timestamp_ns=source.end_timestamp_ns,
        )

    def clock_domain(self, clock: str) -> ClockDomain:
        for domain in self.clock_domains:
            if domain.clock == clock:
                return domain
        raise KeyError(clock)

    def clock_instance(self, clock: str) -> ClockInstance:
        """The origin instance of ``clock`` in this Episode: timestamps of two
        Episodes are comparable only when their instances are equal."""
        return clock_instance(self.clock_domain(clock), self.lineage.source)

    def stream(self, topic: str) -> EpisodeStream:
        for stream in self.streams:
            if stream.topic == topic:
                return stream
        raise KeyError(topic)

    def observed_topics(self, role: EpisodeStreamRole) -> list[str]:
        """Topics of ``role`` with at least one occurrence in the Episode."""
        return sorted({o.topic for o in getattr(self, _ROLE_LISTS[role])})


def _check_component_lengths(
    occurrence: EpisodeOccurrence, stream: EpisodeStream
) -> None:
    for field in stream.fields:
        names = field.component_names
        if names is None:
            continue
        value = occurrence.values[field.name]
        if not isinstance(value, list) or len(value) != len(names):
            raise ValueError(
                f"{occurrence.occurrence_id!r} field {field.name!r} must be a list "
                f"of {len(names)} components {names}, got {value!r}"
            )


def load_canonical_episode_manifest(data: bytes) -> EpisodeManifest:
    """Strictly parse manifest bytes and require them to be canonical:
    ``canonical(parse(data)) == data``."""
    try:
        payload = json.loads(data.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise EpisodeManifestError(f"manifest is not UTF-8 JSON: {exc}") from exc
    if not isinstance(payload, dict):
        raise EpisodeManifestError("manifest must be a JSON object")
    schema_version = payload.get("schema_version")
    if schema_version != EPISODE_MANIFEST_SCHEMA_V3:
        raise UnsupportedEpisodeManifestVersionError(
            f"unsupported EpisodeManifest schema_version: {schema_version!r}"
        )
    try:
        manifest = EpisodeManifest.model_validate(payload)
    except ValidationError as exc:
        raise EpisodeManifestError(f"invalid EpisodeManifest: {exc}") from exc
    if manifest.to_canonical_bytes() != data:
        raise NonCanonicalEpisodeManifestError(
            "manifest bytes are not in canonical EpisodeManifest v3 form"
        )
    return manifest


__all__ = [
    "EPISODE_MANIFEST_SCHEMA_V3",
    "EpisodeField",
    "EpisodeLineage",
    "EpisodeManifest",
    "EpisodeManifestError",
    "EpisodeOccurrence",
    "EpisodeStream",
    "EpisodeTimeWindow",
    "EpisodeValue",
    "FrameTimeline",
    "NonCanonicalEpisodeManifestError",
    "UnsupportedEpisodeManifestVersionError",
    "load_canonical_episode_manifest",
]
