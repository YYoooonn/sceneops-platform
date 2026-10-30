"""LeRobot Episode builder: one registered LeRobot v3.0 SourceContent ->
canonical Episodes (ADR-015 §6.2, §6.5–§6.7; M3.0 D2, D3, D7–D9, D13).

Pure and database-free. It reads the content only through the native reader
(``sceneops_sources.lerobot``), which reads only verified content files, and
decides what the content means as Episodes as a function of the content
bytes, producer semantics and the normalized ``LeRobotEpisodeBuildConfig``
(MS-4). It never reads a SourceSnapshot or its descriptor, writes nothing
and plans no payload: images stay in the preserved videos.

Canonical rules (frozen by ``semantics_version`` 2):

    unit             one Episode per native episode; unit_key = str(episode_index)
    clock            lerobot.frame_time, origin unit, resolution 1 ns
    time             frame k at round_half_even(k · 10⁹ / fps); fps a positive
                     integer; frame_index dense from 0 in row order, and every
                     recorded timestamp exactly float32(frame_index / fps), or
                     the build fails (never snapped to the grid)
    window           [0, t(length - 1) + 1); frame_timeline {clock, fps, length}
    streams          one per selected feature, topic = feature key, schema_name =
                     its dtype, one occurrence per frame; numeric values decoded
                     exactly (float32 as its float64 widening), one field
                     ``value`` read from the feature column; a vector feature
                     declares its source names as component_names
    video            an observation stream whose occurrences are PayloadRefs
                     (video/mp4) to the episode's preserved video file in place
                     (sources Logical URI, the inventory's sha256 and size), with
                     a video_frame selector at
                     round_half_even((from_timestamp + k / fps) · 10⁹),
                     from_timestamp its exact float64 value
    task events      stream ``task_index``: one event at every frame whose
                     task_index differs from the previous frame's (always frame
                     0), values {task, task_index}, the task string from
                     meta/tasks.parquet
    transitions      a ``transition`` feature keeps one occurrence per frame at
                     the frame it is recorded on, value verbatim; no event is
                     derived from it
    occurrence id    <feature>-<frame index, 8 digits>
    native_ref       the frame's global ``index``, decimal
"""

from __future__ import annotations

import math
import struct
from collections.abc import Sequence
from fractions import Fraction
from typing import Any, Final

from sceneops_core.artifacts.schemas import PayloadRef, VideoFrameSelector
from sceneops_core.episodes.lerobot_build import (
    LeRobotEpisodeBuildConfig,
    LeRobotFeatureSelection,
)
from sceneops_core.episodes.recording_build import EpisodeStreamRole
from sceneops_core.episodes.schemas import (
    EpisodeField,
    EpisodeLineage,
    EpisodeManifest,
    EpisodeOccurrence,
    EpisodeStream,
    FrameTimeline,
)
from sceneops_core.provenance import (
    ClockDomain,
    ClockOrigin,
    PinnedContent,
    ProducerInfo,
    frame_time_ns,
)
from sceneops_sources.lerobot import (
    LeRobotContent,
    LeRobotEpisode,
    LeRobotFeature,
    LeRobotFormatError,
    read_lerobot_content,
)
from sceneops_sources import ContentFileError, ContentFiles, content_file_payload_ref

from .plan import EpisodeBuildPlan, PlannedEpisode

LEROBOT_EPISODE_PRODUCER_ID: Final = "sceneops.lerobot_episode_builder"
# Bump whenever the canonical bytes a build produces change for the same
# content and configuration (it enters the producer fingerprint).
# 2: EpisodeManifest v4 (a video frame is a PayloadRef by Logical URI with a
# video_frame selector).
LEROBOT_EPISODE_SEMANTICS_VERSION: Final = 2
LEROBOT_FRAME_CLOCK: Final = "lerobot.frame_time"
LEROBOT_FRAME_CLOCK_DOMAIN: Final = ClockDomain(
    clock=LEROBOT_FRAME_CLOCK, origin=ClockOrigin.UNIT, resolution_ns=1
)
VALUE_FIELD: Final = "value"
TASK_TOPIC: Final = "task_index"
TASK_FIELDS: Final = ("task", "task_index")
VIDEO_MEDIA_TYPE: Final = "video/mp4"
VIDEO_SUFFIX: Final = ".mp4"
_NUMERIC_DTYPES: Final = frozenset(
    {"float16", "float32", "float64", "int8", "int16", "int32", "int64", "uint8",
     "uint16", "uint32", "uint64", "bool"}
)  # fmt: skip


class LeRobotEpisodeBuildError(ValueError):
    """The content cannot be canonicalized faithfully as Episodes under this
    configuration."""


def _float32(value: float) -> float:
    return struct.unpack("<f", struct.pack("<f", value))[0]


def _component_names(feature: LeRobotFeature) -> list[str] | None:
    """The source's names of a vector feature's dimensions, in source order:
    a list, or a dict with exactly one key whose value is that list (D8)."""
    names = feature.names
    if names is None:
        return None
    if isinstance(names, dict):
        if len(names) != 1:
            raise LeRobotEpisodeBuildError(
                f"feature {feature.key!r} names its dimensions by {sorted(names)}; "
                "only one group of names is supported"
            )
        (names,) = names.values()
    if not isinstance(names, list) or not all(isinstance(n, str) for n in names):
        raise LeRobotEpisodeBuildError(
            f"feature {feature.key!r} has names that are not a list of strings"
        )
    return names


def _value(raw: Any, *, where: str) -> Any:
    """A decoded Parquet value as the manifest keeps it: bool, int, finite
    float or a list of numbers. pyarrow returns float32 as its exact float64
    widening, which serializes and reads back bit-identically."""
    if isinstance(raw, bool | int):
        return raw
    if isinstance(raw, float):
        if not math.isfinite(raw):
            raise LeRobotEpisodeBuildError(f"{where} is {raw!r}; only finite floats")
        return raw
    if isinstance(raw, list):
        items = [_value(v, where=f"{where}[]") for v in raw]
        if any(isinstance(v, bool) or not isinstance(v, int | float) for v in items):
            raise LeRobotEpisodeBuildError(f"{where} is a list of non-numbers")
        return items
    raise LeRobotEpisodeBuildError(f"{where} is {type(raw).__name__}, not a value")


class _Stream:
    """A selected feature as one Episode stream."""

    def __init__(
        self, selection: LeRobotFeatureSelection, feature: LeRobotFeature
    ) -> None:
        self.selection = selection
        self.feature = feature
        self.role = EpisodeStreamRole(selection.role)
        if feature.is_video:
            if self.role != EpisodeStreamRole.OBSERVATION:
                raise LeRobotEpisodeBuildError(
                    f"video feature {feature.key!r} can only be an observation"
                )
            self.component_names = None
        else:
            if feature.dtype not in _NUMERIC_DTYPES:
                raise LeRobotEpisodeBuildError(
                    f"feature {feature.key!r} has dtype {feature.dtype!r}; only "
                    "numeric, bool and video features are supported"
                )
            names = _component_names(feature)
            vector = feature.shape not in ((), (1,))
            if names is not None and (
                not vector or list(feature.shape) != [len(names)]
            ):
                raise LeRobotEpisodeBuildError(
                    f"feature {feature.key!r} declares {len(names)} names for shape "
                    f"{list(feature.shape)}"
                )
            self.component_names = names if vector else None

    @property
    def topic(self) -> str:
        return self.feature.key

    def manifest_stream(self) -> EpisodeStream:
        fields = (
            []
            if self.feature.is_video
            else [
                EpisodeField(
                    name=VALUE_FIELD,
                    path=self.topic,
                    component_names=self.component_names,
                )
            ]
        )
        return EpisodeStream(
            topic=self.topic,
            role=self.role,
            schema_name=self.feature.dtype,
            source_clock=LEROBOT_FRAME_CLOCK,
            fields=fields,
            has_payload=self.feature.is_video,
        )


def _streams(
    content: LeRobotContent, config: LeRobotEpisodeBuildConfig
) -> list[_Stream]:
    streams = []
    for selection in config.features:
        feature = content.features.get(selection.feature)
        if feature is None:
            raise LeRobotEpisodeBuildError(
                f"feature {selection.feature!r} is not a feature of the dataset"
            )
        streams.append(_Stream(selection, feature))
    return sorted(streams, key=lambda s: s.topic)


def _select_episodes(
    content: LeRobotContent, unit_keys: Sequence[str] | None, pinned: PinnedContent
) -> list[LeRobotEpisode]:
    by_key = {str(e.episode_index): e for e in content.episodes}
    if unit_keys is None:
        selected = list(content.episodes)
    else:
        unknown = sorted(set(unit_keys) - set(by_key))
        if unknown:
            raise LeRobotEpisodeBuildError(
                f"unit_keys {unknown} name no episode of {pinned.content_id}"
            )
        selected = [by_key[k] for k in sorted(set(unit_keys), key=int)]
    if not selected:
        raise LeRobotEpisodeBuildError(
            "the content holds no episode; a build that yields no Episode fails"
        )
    inventoried = {entry.path for entry in pinned.inventory.files}
    for episode in selected:
        missing = [p for p in episode.referenced_paths() if p not in inventoried]
        if missing:
            raise LeRobotEpisodeBuildError(
                f"episode {episode.episode_index} references {missing}, which are not "
                f"files of {pinned.content_id}"
            )
    return selected


def _video_ref(
    pinned: PinnedContent, episode: LeRobotEpisode, stream: _Stream
) -> tuple[PayloadRef, Fraction, Fraction]:
    segment = episode.videos.get(stream.topic)
    if segment is None:
        raise LeRobotEpisodeBuildError(
            f"episode {episode.episode_index} has no {stream.topic!r} video segment"
        )
    if not segment.path.endswith(VIDEO_SUFFIX):
        raise LeRobotEpisodeBuildError(
            f"video feature {stream.topic!r} is stored as {segment.path!r}; the "
            f"evidence for {VIDEO_MEDIA_TYPE} is a {VIDEO_SUFFIX} file"
        )
    entry = next(e for e in pinned.inventory.files if e.path == segment.path)
    ref = content_file_payload_ref(
        pinned.content_id, entry, media_type=VIDEO_MEDIA_TYPE
    )
    return ref, Fraction(segment.from_timestamp), Fraction(segment.to_timestamp)


def _plan_episode(
    *,
    content: LeRobotContent,
    pinned: PinnedContent,
    producer: ProducerInfo,
    config: LeRobotEpisodeBuildConfig,
    streams: list[_Stream],
    episode: LeRobotEpisode,
    fps: int,
) -> PlannedEpisode:
    where = f"episode {episode.episode_index}"
    try:
        table = content.episode_rows(episode)
    except LeRobotFormatError as exc:
        raise LeRobotEpisodeBuildError(str(exc)) from exc
    columns = [s.topic for s in streams if not s.feature.is_video]
    missing = [c for c in columns if c not in table.column_names]
    if missing:
        raise LeRobotEpisodeBuildError(f"{where}: the data has no column(s) {missing}")
    rows = table.select(
        ["frame_index", "timestamp", "index", "task_index", *columns]
    ).to_pylist()
    if len(rows) != episode.length:
        raise LeRobotEpisodeBuildError(
            f"{where} has {len(rows)} rows, meta/episodes declares {episode.length}"
        )

    videos = {
        s.topic: _video_ref(pinned, episode, s) for s in streams if s.feature.is_video
    }
    lists: dict[EpisodeStreamRole, list[EpisodeOccurrence]] = {
        role: [] for role in EpisodeStreamRole
    }
    previous_task: int | None = None
    for k, row in enumerate(rows):
        if row["frame_index"] != k:
            raise LeRobotEpisodeBuildError(
                f"{where}: row {k} has frame_index {row['frame_index']}; frames must "
                "be dense from 0 in row order"
            )
        recorded = row["timestamp"]
        if not isinstance(recorded, float) or recorded != _float32(k / fps):
            raise LeRobotEpisodeBuildError(
                f"{where} frame {k}: recorded timestamp {recorded!r} is not "
                f"float32({k} / {fps}); a dataset off its frame grid is not snapped"
            )
        t_ns = frame_time_ns(k, fps)
        native_ref = str(row["index"])
        for stream in streams:
            if stream.feature.is_video:
                ref, start, end = videos[stream.topic]
                position = start + Fraction(k, fps)
                if not position < end:
                    raise LeRobotEpisodeBuildError(
                        f"{where} frame {k} lies outside its {stream.topic!r} segment "
                        f"[{float(start)}, {float(end)})"
                    )
                payload = ref.model_copy(
                    update={
                        "selector": VideoFrameSelector(
                            timestamp_ns=round(position * 10**9)
                        )
                    }
                )
                values = {}
            else:
                payload = None
                values = {
                    VALUE_FIELD: _value(
                        row[stream.topic], where=f"{where} frame {k} {stream.topic!r}"
                    )
                }
            lists[stream.role].append(
                EpisodeOccurrence(
                    occurrence_id=f"{stream.topic}-{k:08d}",
                    topic=stream.topic,
                    timestamp_ns=t_ns,
                    values=values,
                    payload=payload,
                    native_ref=native_ref,
                )
            )
        task_index = row["task_index"]
        if config.task_events and task_index != previous_task:
            task = content.tasks.get(task_index)
            if task is None:
                raise LeRobotEpisodeBuildError(
                    f"{where} frame {k}: task_index {task_index!r} is not in "
                    "meta/tasks.parquet"
                )
            lists[EpisodeStreamRole.EVENT].append(
                EpisodeOccurrence(
                    occurrence_id=f"{TASK_TOPIC}-{k:08d}",
                    topic=TASK_TOPIC,
                    timestamp_ns=t_ns,
                    values={"task": task, "task_index": task_index},
                    native_ref=native_ref,
                )
            )
        previous_task = task_index

    manifest_streams = [s.manifest_stream() for s in streams]
    if config.task_events:
        manifest_streams.append(
            EpisodeStream(
                topic=TASK_TOPIC,
                role=EpisodeStreamRole.EVENT,
                schema_name=content.features[TASK_TOPIC].dtype,
                source_clock=LEROBOT_FRAME_CLOCK,
                fields=[EpisodeField(name=n, path=n) for n in TASK_FIELDS],
            )
        )
    timeline = FrameTimeline(
        clock=LEROBOT_FRAME_CLOCK, fps=fps, frame_count=episode.length
    )
    start_ns, end_ns = timeline.window()
    unit_key = str(episode.episode_index)
    manifest = EpisodeManifest(
        lineage=EpisodeLineage(
            source=pinned.unit_source(
                source_clock=LEROBOT_FRAME_CLOCK,
                start_timestamp_ns=start_ns,
                end_timestamp_ns=end_ns,
                unit_key=unit_key,
            ),
            producer=producer,
        ),
        clock_domains=[LEROBOT_FRAME_CLOCK_DOMAIN],
        frame_timeline=timeline,
        streams=sorted(manifest_streams, key=lambda s: s.topic),
        observations=_ordered(lists[EpisodeStreamRole.OBSERVATION]),
        states=_ordered(lists[EpisodeStreamRole.STATE]),
        actions=_ordered(lists[EpisodeStreamRole.ACTION]),
        events=_ordered(lists[EpisodeStreamRole.EVENT]),
        transitions=_ordered(lists[EpisodeStreamRole.TRANSITION]),
    )
    return PlannedEpisode(unit_key=unit_key, manifest=manifest)


def _ordered(items: list[EpisodeOccurrence]) -> list[EpisodeOccurrence]:
    return sorted(items, key=lambda o: (o.topic, o.timestamp_ns, o.occurrence_id))


def plan_lerobot_episodes(
    *,
    pinned: PinnedContent,
    files: ContentFiles,
    config: LeRobotEpisodeBuildConfig,
    unit_keys: Sequence[str] | None = None,
) -> EpisodeBuildPlan:
    """The Episodes of ``unit_keys`` (default: every episode of the content),
    without writing anything. A manifest depends only on its own episode, so
    building a subset yields the same revisions as building everything."""
    if files.inventory != pinned.inventory:
        raise LeRobotEpisodeBuildError(
            "the content files are not the pinned content's inventory"
        )
    try:
        content = read_lerobot_content(files)
    except (LeRobotFormatError, ContentFileError) as exc:
        raise LeRobotEpisodeBuildError(str(exc)) from exc
    fps = content.fps
    if isinstance(fps, bool) or not isinstance(fps, int) or fps < 1:
        raise LeRobotEpisodeBuildError(
            f"fps {fps!r} is not a positive integer; a frame grid needs one"
        )
    if config.task_events and TASK_TOPIC not in content.features:
        raise LeRobotEpisodeBuildError(f"the dataset declares no {TASK_TOPIC!r}")
    streams = _streams(content, config)
    episodes = _select_episodes(content, unit_keys, pinned)
    producer = ProducerInfo.create(
        producer_id=LEROBOT_EPISODE_PRODUCER_ID,
        semantics_version=LEROBOT_EPISODE_SEMANTICS_VERSION,
        build_config=config.normalized(),
        source=pinned.source_revision,
    )
    try:
        planned = [
            _plan_episode(
                content=content,
                pinned=pinned,
                producer=producer,
                config=config,
                streams=streams,
                episode=episode,
                fps=fps,
            )  # fmt: skip
            for episode in episodes
        ]
    except (LeRobotFormatError, ContentFileError) as exc:
        raise LeRobotEpisodeBuildError(str(exc)) from exc
    return EpisodeBuildPlan(
        producer=producer,
        episodes=sorted(planned, key=lambda e: int(e.unit_key)),
        payloads={},
    )


__all__ = [
    "LEROBOT_EPISODE_PRODUCER_ID",
    "LEROBOT_EPISODE_SEMANTICS_VERSION",
    "LEROBOT_FRAME_CLOCK",
    "LeRobotEpisodeBuildError",
    "plan_lerobot_episodes",
]
