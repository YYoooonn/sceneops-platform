"""RecordingEpisodeBuilder: one verified L1 recording -> canonical Episodes
(ADR-007 §29.10, §31).

Pure and database-free. It reads the local, verified copy of a registered
recording (``resolve_recording`` owns acquiring it) through the shared
recording reader, and decides what the recording means as Episodes as a
function of recording content, producer semantics and the normalized
``RecordingEpisodeBuildConfig`` only (I-32). It never reads recording
metadata records, the acquisition mode or the robot platform, never renames
a topic, and never resamples, interpolates, forward-fills, associates or
synchronizes streams: every occurrence keeps its own canonical timestamp in
its stream's clock, and duplicates stay duplicates.

Two passes over the file:

    plan_recording_episodes   decode every selected message; take each one's
                              canonical time and selected field values; plan
                              observation payloads; segment on the declared
                              clock; build the complete EpisodeManifest set and
                              the payload plan. Nothing is written.
    iter_planned_payloads     re-read the payload streams and yield each planned
                              payload's bytes, verified against the plan.

Everything that cannot be expressed faithfully fails the build loudly: an
unsupported encoding, a configured topic absent from the recording, a field
path that does not resolve or resolves to a structure or raw bytes, a
non-finite float, a message without the configured timestamp, unpaired task
markers, a build that yields no Episode.

Identity rules (frozen by ``semantics_version`` 1):

    occurrence_id    <topic slug>-<rank>: rank in the stream's canonical order
                     over the whole recording (canonical timestamp, then MCAP
                     sequence when every message of the stream carries one, then
                     per-stream file order; I-34)
    unit_key         recording | segment-<window index> | task-<key>-<occurrence>
    payload artifact shared with the Scene builder (sceneops_worker.recordings)
"""

from __future__ import annotations

import hashlib
import json
import math
from collections.abc import Iterator
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Final

from sceneops_core.common.ids import robot_run_recording_artifact_id
from sceneops_core.episodes.recording_build import (
    EpisodeEventSourceConfig,
    EpisodeStreamConfig,
    EpisodeStreamRole,
    EpisodeTimePolicy,
    EpisodeTimeSource,
    EventMarkerSegmentation,
    FixedDurationEpisodeSegmentation,
    PayloadDecoding,
    RecordingEpisodeBuildConfig,
    WholeRecordingSegmentation,
)
from sceneops_core.episodes.schemas import (
    EpisodeField,
    EpisodeLineage,
    EpisodeManifest,
    EpisodeOccurrence,
    EpisodeStream,
)
from sceneops_core.provenance import (
    INT64_MAX,
    ProducerInfo,
    RecordingSegmentSource,
    RecordingSourceRevision,
)
from sceneops_core.robots.clock import MCAP_LOG_TIME_CLOCK, MCAP_PUBLISH_TIME_CLOCK
from sceneops_integrations.recording import (
    RecordingMessage,
    Ros2Decoder,
    iter_recording_messages,
    stamp_ns,
)

from sceneops_worker.recordings.payloads import (
    PlannedPayload,
    RecordingBuildError,
    RecordingRevision,
    canonical_order,
    extract_payload,
    plan_payload,
    topic_slug,
)

RECORDING_EPISODE_PRODUCER_ID: Final = "sceneops.recording_episode_builder"
RECORDING_EPISODE_SEMANTICS_VERSION: Final = 1
STRING_SCHEMA: Final = "std_msgs/msg/String"
WHOLE_RECORDING_UNIT_KEY: Final = "recording"

SourceConfig = EpisodeStreamConfig | EpisodeEventSourceConfig


class RecordingEpisodeBuildError(RecordingBuildError):
    """The recording cannot be canonicalized faithfully as Episodes under this
    configuration."""


@dataclass(frozen=True)
class PlannedEpisode:
    unit_key: str
    manifest: EpisodeManifest


@dataclass(frozen=True)
class EpisodeBuildPlan:
    producer: ProducerInfo
    episodes: list[PlannedEpisode]
    # keyed by (topic, per-topic file index)
    payloads: dict[tuple[str, int], PlannedPayload]

    def count(self, role: EpisodeStreamRole) -> int:
        return sum(len(e.manifest.occurrences_of(role)) for e in self.episodes)


# --- field values ---------------------------------------------------------------


def _canonical_value(value: Any, *, where: str) -> Any:
    """A decoded value as the manifest keeps it: bool, int, finite float,
    string or a list of numbers. Never converts a value's meaning."""
    if hasattr(value, "tolist") and not isinstance(value, (bool, int, float, str)):
        value = value.tolist()  # numpy scalars / arrays
    if isinstance(value, bool | str):
        return value
    if isinstance(value, int):
        return int(value)
    if isinstance(value, float):
        if not math.isfinite(value):
            raise RecordingEpisodeBuildError(
                f"{where} is {value!r}; EpisodeManifest v1 holds finite floats only"
            )
        return float(value)
    if isinstance(value, bytes | bytearray):
        raise RecordingEpisodeBuildError(
            f"{where} is raw bytes; select a payload extraction instead of a field"
        )
    if isinstance(value, list | tuple):
        items = [_canonical_value(v, where=f"{where}[]") for v in value]
        if any(isinstance(v, bool) or not isinstance(v, int | float) for v in items):
            raise RecordingEpisodeBuildError(
                f"{where} is a list of non-numbers; only numeric lists are values"
            )
        return items
    raise RecordingEpisodeBuildError(
        f"{where} resolves to a structure ({type(value).__name__}); select its leaf fields"
    )


def _resolve(decoded: Any, path: str, *, where: str) -> Any:
    value = decoded
    for segment in path.split("."):
        if isinstance(value, dict):
            if segment not in value:
                raise RecordingEpisodeBuildError(f"{where}: no field {path!r}")
            value = value[segment]
        elif hasattr(type(value), "__slots__") and segment in getattr(
            type(value), "__slots__", ()
        ):
            value = getattr(value, segment)
        else:
            raise RecordingEpisodeBuildError(f"{where}: no field {path!r}")
    return value


# --- planning -------------------------------------------------------------------


@dataclass
class _Occurrence:
    topic: str
    timestamp_ns: int
    segment_ns: int
    sequence: int | None
    channel_index: int
    values: dict[str, Any]
    payload: PlannedPayload | None


@dataclass(frozen=True)
class _Window:
    unit_key: str
    start_ns: int
    end_ns: int


@dataclass
class _Planner:
    revision: RecordingRevision
    config: RecordingEpisodeBuildConfig
    decoder: Ros2Decoder = field(default_factory=Ros2Decoder)
    sources: dict[str, SourceConfig] = field(init=False)
    roles: dict[str, EpisodeStreamRole] = field(init=False)
    schemas: dict[str, str] = field(default_factory=dict)
    observed: dict[str, list[_Occurrence]] = field(default_factory=dict)

    def __post_init__(self) -> None:
        self.sources = {s.topic: s for s in self.config.streams}
        self.sources.update({e.topic: e for e in self.config.events})
        self.roles = {s.topic: EpisodeStreamRole(s.role) for s in self.config.streams}
        self.roles.update(
            {e.topic: EpisodeStreamRole.EVENT for e in self.config.events}
        )

    def _decode(self, source: SourceConfig, message: RecordingMessage) -> Any:
        decoded = self.decoder.decode(message)
        if source.decoding == PayloadDecoding.ROS2:
            return decoded
        if message.schema_name != STRING_SCHEMA:
            raise RecordingEpisodeBuildError(
                f"{message.topic!r} is {message.schema_name!r}; json_string decoding "
                f"needs {STRING_SCHEMA!r}"
            )
        try:
            payload = json.loads(decoded.data)
        except (TypeError, ValueError) as exc:
            raise RecordingEpisodeBuildError(
                f"message {message.channel_index} on {message.topic!r} is not JSON: {exc}"
            ) from exc
        if not isinstance(payload, dict):
            raise RecordingEpisodeBuildError(
                f"message {message.channel_index} on {message.topic!r} is not a JSON object"
            )
        return payload

    def _time(
        self, policy: EpisodeTimePolicy, message: RecordingMessage, decoded: Any
    ) -> int:
        where = f"message {message.channel_index} on {message.topic!r}"
        if policy.source == EpisodeTimeSource.LOG_TIME:
            return message.log_time_ns
        if policy.source == EpisodeTimeSource.PUBLISH_TIME:
            return message.publish_time_ns
        if policy.source == EpisodeTimeSource.HEADER_STAMP:
            header = (
                getattr(decoded, "header", None)
                if not isinstance(decoded, dict)
                else None
            )
            if header is None or not hasattr(header, "stamp"):
                raise RecordingEpisodeBuildError(
                    f"{where} ({message.schema_name}) carries no header stamp"
                )
            return stamp_ns(header.stamp)
        value = _resolve(decoded, policy.field or "", where=where)
        if (
            isinstance(value, bool)
            or not isinstance(value, int)
            or not 0 <= value <= INT64_MAX
        ):
            raise RecordingEpisodeBuildError(
                f"{where}: time field {policy.field!r} is {value!r}, not integer nanoseconds"
            )
        return value

    def _segment_time(
        self, policy: EpisodeTimePolicy, timestamp_ns: int, message: RecordingMessage
    ) -> int:
        clock = self.config.segmentation_clock()
        if policy.clock == clock:
            return timestamp_ns
        if clock == MCAP_LOG_TIME_CLOCK:
            return message.log_time_ns
        if clock == MCAP_PUBLISH_TIME_CLOCK:
            return message.publish_time_ns
        raise RecordingEpisodeBuildError(  # excluded by config validation
            f"{message.topic!r} has no timestamp on segmentation clock {clock!r}"
        )

    def on_message(self, message: RecordingMessage) -> None:
        source = self.sources[message.topic]
        self.schemas.setdefault(message.topic, message.schema_name)
        decoded = self._decode(source, message)
        timestamp_ns = self._time(source.time, message, decoded)
        where = f"message {message.channel_index} on {message.topic!r}"
        values = {
            f.name: _canonical_value(
                _resolve(decoded, f.path, where=where),
                where=f"{where} field {f.path!r}",
            )
            for f in source.fields
        }
        payload = None
        if isinstance(source, EpisodeStreamConfig) and source.payload is not None:
            try:
                payload = plan_payload(
                    robot_run_id=self.revision.robot_run_id,
                    extraction=source.payload,
                    message=message,
                    decoded=decoded,
                )
            except RecordingBuildError as exc:
                raise RecordingEpisodeBuildError(str(exc)) from exc
        self.observed.setdefault(message.topic, []).append(
            _Occurrence(
                topic=message.topic,
                timestamp_ns=timestamp_ns,
                segment_ns=self._segment_time(source.time, timestamp_ns, message),
                sequence=message.sequence,
                channel_index=message.channel_index,
                values=values,
                payload=payload,
            )
        )

    def check_presence(self) -> None:
        missing = sorted(set(self.sources) - set(self.schemas))
        if missing:
            raise RecordingEpisodeBuildError(
                f"configured topics {missing} are not in the recording"
            )


def _windows(
    config: RecordingEpisodeBuildConfig,
    planner: _Planner,
    items: list[_Occurrence],
) -> list[_Window]:
    segmentation = config.segmentation
    if isinstance(segmentation, WholeRecordingSegmentation):
        segment_times = [i.segment_ns for i in items]
        return [
            _Window(
                WHOLE_RECORDING_UNIT_KEY, min(segment_times), max(segment_times) + 1
            )
        ]
    if isinstance(segmentation, FixedDurationEpisodeSegmentation):
        origin = min(i.segment_ns for i in items)
        duration = segmentation.duration_ns
        indexes = sorted({(i.segment_ns - origin) // duration for i in items})
        return [
            _Window(
                f"segment-{k:06d}",
                origin + k * duration,
                origin + (k + 1) * duration,
            )
            for k in indexes
        ]
    assert isinstance(segmentation, EventMarkerSegmentation)
    return _marker_windows(
        segmentation, planner.observed.get(segmentation.event_topic, [])
    )


def _marker_windows(
    segmentation: EventMarkerSegmentation, markers: list[_Occurrence]
) -> list[_Window]:
    """Pair start / end markers per task key, in canonical marker order."""
    open_starts: dict[str, _Occurrence] = {}
    occurrences: dict[str, int] = {}
    windows: list[_Window] = []
    start_values = set(segmentation.start_values)
    end_values = set(segmentation.end_values)
    for marker in canonical_order(markers):
        key = marker.values[segmentation.key_field]
        state = marker.values[segmentation.state_field]
        where = f"marker {marker.channel_index} on {segmentation.event_topic!r}"
        if not isinstance(key, str) or not isinstance(state, str):
            raise RecordingEpisodeBuildError(
                f"{where}: {segmentation.key_field!r} and {segmentation.state_field!r} "
                "must be strings"
            )
        if state in start_values:
            if key in open_starts:
                raise RecordingEpisodeBuildError(
                    f"{where}: task {key!r} starts again before it ended"
                )
            open_starts[key] = marker
        elif state in end_values:
            start = open_starts.pop(key, None)
            if start is None:
                raise RecordingEpisodeBuildError(
                    f"{where}: task {key!r} ends without having started"
                )
            index = occurrences.get(key, 0)
            occurrences[key] = index + 1
            windows.append(
                _Window(
                    f"task-{key}-{index:03d}", start.segment_ns, marker.segment_ns + 1
                )
            )
    if open_starts:
        raise RecordingEpisodeBuildError(
            f"task(s) {sorted(open_starts)} start but never end in the recording"
        )
    return sorted(windows, key=lambda w: (w.start_ns, w.unit_key))


def plan_recording_episodes(
    path: Path, *, revision: RecordingRevision, config: RecordingEpisodeBuildConfig
) -> EpisodeBuildPlan:
    """Pass 1: the complete Episode set of the recording scope, without
    writing anything."""
    sources: list[SourceConfig] = [*config.streams, *config.events]
    uses_log_time = config.segmentation_clock() == MCAP_LOG_TIME_CLOCK or any(
        s.time.source == EpisodeTimeSource.LOG_TIME for s in sources
    )
    if uses_log_time and revision.recording_clock != MCAP_LOG_TIME_CLOCK:
        raise RecordingEpisodeBuildError(
            f"the recording clock is {revision.recording_clock!r}; log_time is "
            f"only defined as {MCAP_LOG_TIME_CLOCK!r}"
        )

    planner = _Planner(revision=revision, config=config)
    for message in iter_recording_messages(path, topics=planner.sources):
        planner.on_message(message)
    planner.check_presence()

    producer = ProducerInfo.create(
        producer_id=RECORDING_EPISODE_PRODUCER_ID,
        semantics_version=RECORDING_EPISODE_SEMANTICS_VERSION,
        build_config=config.normalized(),
        source=RecordingSourceRevision(
            robot_run_id=revision.robot_run_id,
            recording_checksum=revision.recording_checksum,
        ),
    )

    # Canonical occurrences, identified by their rank in stream order.
    ranked: list[tuple[_Occurrence, EpisodeOccurrence]] = []
    payloads: dict[tuple[str, int], PlannedPayload] = {}
    slugs: dict[str, str] = {}
    for topic, items in sorted(planner.observed.items()):
        try:
            slug = topic_slug(topic)
        except RecordingBuildError as exc:
            raise RecordingEpisodeBuildError(str(exc)) from exc
        if slug in slugs:
            raise RecordingEpisodeBuildError(
                f"topics {slugs[slug]!r} and {topic!r} collide on their id slug"
            )
        slugs[slug] = topic
        for rank, item in enumerate(canonical_order(items)):
            ranked.append(
                (
                    item,
                    EpisodeOccurrence(
                        occurrence_id=f"{slug}-{rank:08d}",
                        topic=topic,
                        timestamp_ns=item.timestamp_ns,
                        values=item.values,
                        payload=item.payload.ref() if item.payload else None,
                    ),
                )
            )
            if item.payload is not None:
                payloads[(topic, item.channel_index)] = item.payload
    if not ranked:
        raise RecordingEpisodeBuildError(
            "no message of any configured topic is in the recording; a recording "
            "build that yields no Episode fails (§18.3)"
        )

    windows = _windows(config, planner, [item for item, _ in ranked])
    if not windows:
        raise RecordingEpisodeBuildError(
            "the segmentation yields no Episode; a recording build that yields no "
            "Episode fails (§18.3)"
        )

    streams = sorted(
        (
            EpisodeStream(
                topic=s.topic,
                role=planner.roles[s.topic],
                schema_name=planner.schemas[s.topic],
                source_clock=s.time.clock,
                fields=sorted(
                    (EpisodeField(name=f.name, path=f.path) for f in s.fields),
                    key=lambda f: f.name,
                ),
                has_payload=isinstance(s, EpisodeStreamConfig)
                and s.payload is not None,
            )
            for s in sources
        ),
        key=lambda s: s.topic,
    )
    lists = {
        EpisodeStreamRole.OBSERVATION: "observations",
        EpisodeStreamRole.STATE: "states",
        EpisodeStreamRole.ACTION: "actions",
        EpisodeStreamRole.EVENT: "events",
    }
    clock = config.segmentation_clock()

    episodes: list[PlannedEpisode] = []
    for window in windows:
        if window.start_ns < 0 or window.end_ns > INT64_MAX:
            raise RecordingEpisodeBuildError(
                f"episode window [{window.start_ns}, {window.end_ns}) is outside the "
                "int64 time range"
            )
        members: dict[str, list[EpisodeOccurrence]] = {
            name: [] for name in lists.values()
        }
        for item, occurrence in ranked:
            if window.start_ns <= item.segment_ns < window.end_ns:
                members[lists[planner.roles[item.topic]]].append(occurrence)
        source = RecordingSegmentSource(
            robot_run_id=revision.robot_run_id,
            recording_artifact_id=robot_run_recording_artifact_id(
                revision.robot_run_id
            ),
            recording_checksum=revision.recording_checksum,
            source_clock=clock,
            start_timestamp_ns=window.start_ns,
            end_timestamp_ns=window.end_ns,
            unit_key=window.unit_key,
        )
        manifest = EpisodeManifest(
            lineage=EpisodeLineage(source=source, producer=producer),
            streams=streams,
            **{
                name: sorted(
                    occurrences,
                    key=lambda o: (o.topic, o.timestamp_ns, o.occurrence_id),
                )
                for name, occurrences in members.items()
            },
        )
        episodes.append(PlannedEpisode(unit_key=window.unit_key, manifest=manifest))

    unit_keys = [e.unit_key for e in episodes]
    if len(unit_keys) != len(set(unit_keys)):
        raise RecordingEpisodeBuildError("the segmentation yields duplicate unit keys")
    return EpisodeBuildPlan(
        producer=producer,
        episodes=sorted(episodes, key=lambda e: e.unit_key),
        payloads=payloads,
    )


def iter_planned_payloads(
    path: Path, plan: EpisodeBuildPlan, config: RecordingEpisodeBuildConfig
) -> Iterator[tuple[PlannedPayload, bytes]]:
    """Pass 2: each planned payload's bytes, verified against the plan, in
    file order. A recording that changed between passes fails loudly."""
    streams = {s.topic: s for s in config.streams if s.payload is not None}
    decoder = Ros2Decoder()
    remaining = set(plan.payloads)
    for message in iter_recording_messages(path, topics=streams):
        planned = plan.payloads.get((message.topic, message.channel_index))
        if planned is None:
            continue
        stream = streams[message.topic]
        try:
            data, media_type = extract_payload(
                stream.payload, message, decoder.decode(message)
            )
        except RecordingBuildError as exc:
            raise RecordingEpisodeBuildError(str(exc)) from exc
        checksum = "sha256:" + hashlib.sha256(data).hexdigest()
        if (checksum, len(data), media_type) != (
            planned.checksum,
            planned.size_bytes,
            planned.media_type,
        ):
            raise RecordingEpisodeBuildError(
                f"payload of message {message.channel_index} on {message.topic!r} "
                "differs from the plan"
            )
        remaining.discard((message.topic, message.channel_index))
        yield planned, data
    if remaining:
        raise RecordingEpisodeBuildError(
            f"{len(remaining)} planned payload(s) were not found on re-read"
        )


__all__ = [
    "RECORDING_EPISODE_PRODUCER_ID",
    "RECORDING_EPISODE_SEMANTICS_VERSION",
    "EpisodeBuildPlan",
    "PlannedEpisode",
    "RecordingEpisodeBuildError",
    "iter_planned_payloads",
    "plan_recording_episodes",
]
