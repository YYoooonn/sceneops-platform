"""Native reader of LeRobot v3.0 datasets (ADR-015 §6, §10; M3.0 D6).

Parses the preserved bytes of one SourceContent into typed native records,
through ``ContentFiles`` only, so every byte it reads is verified against the
content inventory first. It derives the format version from the bytes
(``meta/info.json`` ``codebase_version``) and refuses any other than v3.0.

It decides nothing canonical: no unit key, clock, timestamp normalization,
role, media type or manifest. Those belong to the Episode builder
(``sceneops_episodes.lerobot_builder``), which sits on top of this reader.

Layout read (LeRobot v3.0)::

    meta/info.json                     codebase_version, fps, features, path templates
    meta/tasks.parquet                 task_index -> task string (the string is the
                                       pandas index column when there is no ``task``
                                       column)
    meta/episodes/**/*.parquet         one row per episode: length, data file, row
                                       range, and per video feature its file and
                                       [from_timestamp, to_timestamp) in that file
    data/chunk-*/file-*.parquet        one row per frame of every episode the file holds

Video files are never read: an episode's frames are addressed as
(video file, time in that file), and the reader only names the file.
"""

from __future__ import annotations

import json
from collections.abc import Mapping
from dataclasses import dataclass, field
from typing import Any, Final

import pyarrow as pa
import pyarrow.parquet as pq

from sceneops_core.sources import SourceContentInventory

from .content_files import ContentFiles

LEROBOT_CODEBASE_VERSION: Final = "v3.0"
INFO_PATH: Final = "meta/info.json"
TASKS_PATH: Final = "meta/tasks.parquet"
EPISODES_PREFIX: Final = "meta/episodes/"
VIDEO_DTYPE: Final = "video"

# Per-frame columns every v3.0 data file has.
FRAME_COLUMNS: Final = (
    "episode_index",
    "frame_index",
    "timestamp",
    "index",
    "task_index",
)


class LeRobotFormatError(ValueError):
    """The content is not a readable LeRobot dataset of the supported layout."""


class UnsupportedLeRobotVersionError(LeRobotFormatError):
    """The content declares a LeRobot codebase version this reader does not
    support."""


@dataclass(frozen=True)
class LeRobotFeature:
    """One entry of ``info.json`` ``features``, as declared."""

    key: str
    dtype: str
    shape: tuple[int, ...]
    names: Any

    @property
    def is_video(self) -> bool:
        return self.dtype == VIDEO_DTYPE


@dataclass(frozen=True)
class LeRobotVideoSegment:
    """Where one episode's frames of one video feature lie: a time range of
    one video file, in that file's own time base (seconds, as stored)."""

    path: str
    from_timestamp: float
    to_timestamp: float


@dataclass(frozen=True)
class LeRobotEpisode:
    episode_index: int
    length: int
    data_path: str
    dataset_from_index: int
    dataset_to_index: int
    videos: Mapping[str, LeRobotVideoSegment]

    def referenced_paths(self) -> list[str]:
        return sorted({self.data_path, *(v.path for v in self.videos.values())})


@dataclass
class LeRobotContent:
    codebase_version: str
    fps: Any
    features: Mapping[str, LeRobotFeature]
    tasks: Mapping[int, str]
    episodes: list[LeRobotEpisode]
    files: ContentFiles
    _data: dict[str, pa.Table] = field(default_factory=dict, repr=False)

    def episode(self, episode_index: int) -> LeRobotEpisode | None:
        return next(
            (e for e in self.episodes if e.episode_index == episode_index), None
        )

    def episode_rows(self, episode: LeRobotEpisode) -> pa.Table:
        """The episode's frame rows of its data file, in file order. The rows
        are the ones whose ``index`` lies in ``[dataset_from_index,
        dataset_to_index)``; their ``episode_index`` must be the episode's and
        their ``index`` contiguous, or the dataset is inconsistent."""
        table = self._data.get(episode.data_path)
        if table is None:
            table = _read_parquet(self.files, episode.data_path)
            missing = [c for c in FRAME_COLUMNS if c not in table.column_names]
            if missing:
                raise LeRobotFormatError(
                    f"{episode.data_path} has no column(s) {missing}"
                )
            self._data[episode.data_path] = table
        indexes = table.column("index").to_pylist()
        rows = [
            i
            for i, value in enumerate(indexes)
            if episode.dataset_from_index <= value < episode.dataset_to_index
        ]
        selected = table.take(pa.array(rows, type=pa.int64()))
        where = f"episode {episode.episode_index} in {episode.data_path}"
        if selected.column("index").to_pylist() != list(
            range(episode.dataset_from_index, episode.dataset_to_index)
        ):
            raise LeRobotFormatError(
                f"{where}: rows of index [{episode.dataset_from_index}, "
                f"{episode.dataset_to_index}) are missing, duplicated or out of order"
            )
        if set(selected.column("episode_index").to_pylist()) != {episode.episode_index}:
            raise LeRobotFormatError(f"{where}: rows of another episode_index")
        return selected


def _read_json(files: ContentFiles, path: str) -> Any:
    try:
        return json.loads(files.local_path(path).read_bytes())
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise LeRobotFormatError(f"{path} is not JSON: {exc}") from exc


def _read_parquet(
    files: ContentFiles, path: str, columns: list[str] | None = None
) -> pa.Table:
    try:
        return pq.read_table(files.local_path(path), columns=columns)
    except (pa.ArrowException, OSError) as exc:
        raise LeRobotFormatError(
            f"{path} is not a readable Parquet file: {exc}"
        ) from exc


def _features(info: Mapping[str, Any]) -> dict[str, LeRobotFeature]:
    raw = info.get("features")
    if not isinstance(raw, dict) or not raw:
        raise LeRobotFormatError(f"{INFO_PATH} declares no features")
    features: dict[str, LeRobotFeature] = {}
    for key, spec in raw.items():
        if not isinstance(spec, dict) or not isinstance(spec.get("dtype"), str):
            raise LeRobotFormatError(f"feature {key!r} declares no dtype")
        shape = spec.get("shape")
        if not isinstance(shape, list) or not all(
            isinstance(n, int) and not isinstance(n, bool) for n in shape
        ):
            raise LeRobotFormatError(f"feature {key!r} declares no integer shape")
        features[key] = LeRobotFeature(
            key=key, dtype=spec["dtype"], shape=tuple(shape), names=spec.get("names")
        )
    return features


def _tasks(files: ContentFiles) -> dict[int, str]:
    table = _read_parquet(files, TASKS_PATH)
    column = "task"
    if column not in table.column_names:
        # pandas-written tasks keep the string as the frame's index.
        metadata = (table.schema.metadata or {}).get(b"pandas")
        index_columns = (
            json.loads(metadata).get("index_columns", []) if metadata else []
        )
        named = [c for c in index_columns if isinstance(c, str)]
        if len(named) != 1 or named[0] not in table.column_names:
            raise LeRobotFormatError(f"{TASKS_PATH} has no task string column")
        column = named[0]
    if "task_index" not in table.column_names:
        raise LeRobotFormatError(f"{TASKS_PATH} has no task_index column")
    tasks: dict[int, str] = {}
    for index, task in zip(
        table.column("task_index").to_pylist(),
        table.column(column).to_pylist(),
        strict=True,
    ):
        if not isinstance(index, int) or not isinstance(task, str) or index in tasks:
            raise LeRobotFormatError(
                f"{TASKS_PATH}: invalid or duplicate task {index!r}"
            )
        tasks[index] = task
    return tasks


def _template(info: Mapping[str, Any], key: str) -> str:
    value = info.get(key)
    if not isinstance(value, str):
        raise LeRobotFormatError(f"{INFO_PATH} declares no {key}")
    return value


def _episodes(
    files: ContentFiles,
    info: Mapping[str, Any],
    features: Mapping[str, LeRobotFeature],
) -> list[LeRobotEpisode]:
    paths = sorted(
        entry.path
        for entry in files.inventory.files
        if entry.path.startswith(EPISODES_PREFIX) and entry.path.endswith(".parquet")
    )
    if not paths:
        raise LeRobotFormatError(f"the content holds no {EPISODES_PREFIX} Parquet file")
    data_template = _template(info, "data_path")
    video_keys = sorted(k for k, f in features.items() if f.is_video)
    video_template = _template(info, "video_path") if video_keys else ""
    columns = [
        "episode_index",
        "length",
        "data/chunk_index",
        "data/file_index",
        "dataset_from_index",
        "dataset_to_index",
    ]
    for key in video_keys:
        columns += [
            f"videos/{key}/{name}"
            for name in ("chunk_index", "file_index", "from_timestamp", "to_timestamp")
        ]
    episodes: list[LeRobotEpisode] = []
    for path in paths:
        table = _read_parquet(files, path)
        missing = [c for c in columns if c not in table.column_names]
        if missing:
            raise LeRobotFormatError(f"{path} has no column(s) {missing}")
        for row in table.select(columns).to_pylist():
            videos = {
                key: LeRobotVideoSegment(
                    path=video_template.format(
                        video_key=key,
                        chunk_index=row[f"videos/{key}/chunk_index"],
                        file_index=row[f"videos/{key}/file_index"],
                    ),
                    from_timestamp=row[f"videos/{key}/from_timestamp"],
                    to_timestamp=row[f"videos/{key}/to_timestamp"],
                )
                for key in video_keys
            }
            episodes.append(
                LeRobotEpisode(
                    episode_index=row["episode_index"],
                    length=row["length"],
                    data_path=data_template.format(
                        chunk_index=row["data/chunk_index"],
                        file_index=row["data/file_index"],
                    ),
                    dataset_from_index=row["dataset_from_index"],
                    dataset_to_index=row["dataset_to_index"],
                    videos=videos,
                )
            )
    indexes = [e.episode_index for e in episodes]
    if len(indexes) != len(set(indexes)):
        raise LeRobotFormatError("meta/episodes lists an episode_index more than once")
    for episode in episodes:
        if episode.dataset_to_index - episode.dataset_from_index != episode.length:
            raise LeRobotFormatError(
                f"episode {episode.episode_index}: row range "
                f"[{episode.dataset_from_index}, {episode.dataset_to_index}) is not "
                f"its length {episode.length}"
            )
    return sorted(episodes, key=lambda e: e.episode_index)


def lerobot_read_paths(inventory: SourceContentInventory) -> list[str]:
    """Every file of ``inventory`` this reader may read: the metadata and the
    data files. Video files and anything else are never read, so a caller
    that materializes content for the reader can leave them out."""
    return [
        entry.path
        for entry in inventory.files
        if entry.path.startswith(("meta/", "data/"))
    ]


def read_lerobot_content(files: ContentFiles) -> LeRobotContent:
    """The native structure of a LeRobot v3.0 content. Data files are read
    on demand (``LeRobotContent.episode_rows``); video files never."""
    info = _read_json(files, INFO_PATH)
    if not isinstance(info, dict):
        raise LeRobotFormatError(f"{INFO_PATH} is not a JSON object")
    version = info.get("codebase_version")
    if version != LEROBOT_CODEBASE_VERSION:
        raise UnsupportedLeRobotVersionError(
            f"LeRobot codebase_version {version!r} is not supported; this reader "
            f"reads {LEROBOT_CODEBASE_VERSION!r}"
        )
    features = _features(info)
    return LeRobotContent(
        codebase_version=version,
        fps=info.get("fps"),
        features=features,
        tasks=_tasks(files),
        episodes=_episodes(files, info, features),
        files=files,
    )


__all__ = [
    "FRAME_COLUMNS",
    "LEROBOT_CODEBASE_VERSION",
    "LeRobotContent",
    "LeRobotEpisode",
    "LeRobotFeature",
    "LeRobotFormatError",
    "LeRobotVideoSegment",
    "UnsupportedLeRobotVersionError",
    "lerobot_read_paths",
    "read_lerobot_content",
]
