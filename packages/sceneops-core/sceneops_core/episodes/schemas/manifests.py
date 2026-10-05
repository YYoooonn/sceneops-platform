"""EpisodeManifest v1 -- the canonical, source-faithful description of one
Episode (ADR-007 §13.10, §31).

An Episode is a task / behavior-oriented projection of one RobotRun
recording. Within its window it keeps every recorded occurrence of every
included stream -- observations, states, actions and task / event markers --
each at its own canonical timestamp in its stream's declared clock, with the
field values the build configuration selects, kept as the source holds
them, and, for observations, a SceneOps-owned payload.

Streams stay asynchronous. Observations at t0, t3, t8, states at t1, t2, t7
and actions at t2, t5, t9 are kept exactly so: nothing is resampled,
interpolated, forward-filled, padded, associated with a nearest frame or put
on a common timeline. Two occurrences with equal timestamps stay two
occurrences. Temporal alignment is a derived L3 operation (``ALIGN_EPISODE``
-> ``AlignedEpisode``), never part of this manifest.

The manifest holds only facts the recording contains. It carries no
success label, reward, outcome, language instruction or subtask boundary
unless a recorded event stream carries one, and then only as that event's
values.

The manifest describes the unit, not its membership: no DatasetVersion,
episode id, record state or execution context. Identical source, producer
and build configuration produce byte-identical manifests whatever
DatasetVersion registers them; the registrar derives the DatasetVersion-
scoped ``episode_id`` from (dataset_id, dataset_version, robot_run_id,
unit_key).

Representation conventions fixed by ``sceneops.episode_manifest/v1``:

    time       integer nanoseconds in a declared clock, one clock per stream
    values     bool, integer, finite float, string, or a list of numbers, exactly
               as decoded from the recorded message
    payloads   referenced by artifact identity and integrity, never by location

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

from sceneops_core.artifacts.schemas.payload import PayloadRef
from sceneops_core.common.canonical_json import canonical_json_bytes
from sceneops_core.common.checksums import sha256_checksum
from sceneops_core.common.identifiers import (
    validate_local_id,
    validate_source_clock,
    validate_verbatim_key,
)
from sceneops_core.provenance import (
    ProducerInfo,
    RecordingSegmentSource,
    SourceTimestampNs,
)

from ..recording_build import EpisodeStreamRole, FieldPath

EPISODE_MANIFEST_SCHEMA_V1: Final = "sceneops.episode_manifest/v1"


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
    """A selected field: kept under ``name``, read from ``path``."""

    name: LocalId
    path: FieldPath


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
    """One recorded message of a stream, at its own canonical time in its
    stream's clock. ``occurrence_id`` is ``<topic slug>-<rank>``, the rank in
    the stream's canonical order over the whole recording."""

    occurrence_id: LocalId
    topic: VerbatimKey
    timestamp_ns: SourceTimestampNs
    values: dict[str, EpisodeValue] = Field(default_factory=dict)
    payload: PayloadRef | None = None


class EpisodeLineage(_ManifestModel):
    """The recording segment the Episode was built from and the producer
    that built it. The fingerprint must re-derive from the source revision."""

    source: RecordingSegmentSource
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
}


class EpisodeManifest(_ManifestModel):
    schema_version: Literal["sceneops.episode_manifest/v1"] = EPISODE_MANIFEST_SCHEMA_V1
    lineage: EpisodeLineage

    streams: list[EpisodeStream] = Field(min_length=1)
    observations: list[EpisodeOccurrence] = Field(default_factory=list)
    states: list[EpisodeOccurrence] = Field(default_factory=list)
    actions: list[EpisodeOccurrence] = Field(default_factory=list)
    events: list[EpisodeOccurrence] = Field(default_factory=list)

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
        if not seen_ids:
            raise ValueError("an Episode holds at least one recorded occurrence")
        self._check_source_window()
        return self

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

    def stream(self, topic: str) -> EpisodeStream:
        for stream in self.streams:
            if stream.topic == topic:
                return stream
        raise KeyError(topic)

    def observed_topics(self, role: EpisodeStreamRole) -> list[str]:
        """Topics of ``role`` with at least one occurrence in the Episode."""
        return sorted({o.topic for o in getattr(self, _ROLE_LISTS[role])})


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
    if schema_version != EPISODE_MANIFEST_SCHEMA_V1:
        raise UnsupportedEpisodeManifestVersionError(
            f"unsupported EpisodeManifest schema_version: {schema_version!r}"
        )
    try:
        manifest = EpisodeManifest.model_validate(payload)
    except ValidationError as exc:
        raise EpisodeManifestError(f"invalid EpisodeManifest: {exc}") from exc
    if manifest.to_canonical_bytes() != data:
        raise NonCanonicalEpisodeManifestError(
            "manifest bytes are not in canonical EpisodeManifest v1 form"
        )
    return manifest


__all__ = [
    "EPISODE_MANIFEST_SCHEMA_V1",
    "EpisodeField",
    "EpisodeLineage",
    "EpisodeManifest",
    "EpisodeManifestError",
    "EpisodeOccurrence",
    "EpisodeStream",
    "EpisodeTimeWindow",
    "EpisodeValue",
    "NonCanonicalEpisodeManifestError",
    "UnsupportedEpisodeManifestVersionError",
    "load_canonical_episode_manifest",
]
