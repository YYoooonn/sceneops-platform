"""A small, real LeRobot v3.0 tree for tests of the reader and of canonical
builders: real Parquet and JSON in the v3.0 layout, with knobs for the layout
variants the reader must accept (both ``names`` forms, fixed-size and
variable lists, a pandas-index task table) and for the faults builders must
refuse. The video bytes are opaque placeholders: nothing decodes them.
"""

from __future__ import annotations

import json
import struct
from collections.abc import Callable, Sequence
from pathlib import Path
from typing import Any

import pyarrow as pa
import pyarrow.parquet as pq

VIDEO_KEY = "observation.image"
VIDEO_PATH = "videos/observation.image/chunk-000/file-000.mp4"
DATA_PATH = "data/chunk-000/file-000.parquet"
EPISODES_PATH = "meta/episodes/chunk-000/file-000.parquet"
INCLUDE = ("README.md", "data/**", "meta/**", "videos/**")


def float32(value: float) -> float:
    return struct.unpack("<f", struct.pack("<f", value))[0]


def _vector_type(fixed: bool, size: int) -> pa.DataType:
    return pa.list_(pa.float32(), size) if fixed else pa.list_(pa.float32())


def write_lerobot_tree(
    root: Path,
    *,
    lengths: Sequence[int] = (3, 2),
    fps: Any = 10,
    codebase_version: str = "v3.0",
    names_form: str = "dict",
    fixed_size_lists: bool = True,
    pandas_tasks: bool = True,
    tasks: Sequence[str] = ("push the block",),
    task_index: Callable[[int, int], int] = lambda episode, frame: 0,
    timestamp: Callable[[int, int], float] | None = None,
    frame_index: Callable[[int, int], int] | None = None,
    video_path: str = VIDEO_PATH,
    state_names: Sequence[str] = ("motor_0", "motor_1"),
) -> Path:
    """Write the tree under ``root``. Frame ``k`` of episode ``e`` has state
    ``[e + k / 4, -k]``, action ``[k * 1.5, e]``, ``next.reward`` ``k / 8``,
    ``next.done`` on its last frame and ``next.success`` never, by default
    timestamp ``float32(k / fps)``."""
    # ``fps`` is written to info.json as given; frame times use it only when it
    # is a usable rate, so a tree with an invalid declared rate can be written.
    rate = (
        fps
        if isinstance(fps, int | float) and not isinstance(fps, bool) and fps > 0
        else 10
    )
    timestamp = timestamp or (lambda e, k: float32(k / rate))
    frame_index = frame_index or (lambda e, k: k)
    names: Any = (
        {"motors": list(state_names)} if names_form == "dict" else list(state_names)
    )
    vector = {"dtype": "float32", "shape": [len(state_names)], "names": names}
    scalar = {"shape": [1], "names": None}
    info = {
        "codebase_version": codebase_version,
        "fps": fps,
        "total_episodes": len(lengths),
        "total_frames": sum(lengths),
        "data_path": "data/chunk-{chunk_index:03d}/file-{file_index:03d}.parquet",
        "video_path": "videos/{video_key}/chunk-{chunk_index:03d}/file-{file_index:03d}.mp4",
        "features": {
            VIDEO_KEY: {"dtype": "video", "shape": [4, 4, 3], "names": ["h", "w", "c"]},
            "observation.state": vector,
            "action": vector,
            "episode_index": {"dtype": "int64", **scalar},
            "frame_index": {"dtype": "int64", **scalar},
            "timestamp": {"dtype": "float32", **scalar},
            "next.reward": {"dtype": "float32", **scalar},
            "next.done": {"dtype": "bool", **scalar},
            "next.success": {"dtype": "bool", **scalar},
            "index": {"dtype": "int64", **scalar},
            "task_index": {"dtype": "int64", **scalar},
        },
    }
    rows: dict[str, list[Any]] = {
        k: []
        for k in (
            "observation.state", "action", "episode_index", "frame_index",
            "timestamp", "next.reward", "next.done", "next.success", "index",
            "task_index",
        )
    }  # fmt: skip
    episodes: dict[str, list[Any]] = {
        k: []
        for k in (
            "episode_index", "data/chunk_index", "data/file_index",
            "dataset_from_index", "dataset_to_index",
            f"videos/{VIDEO_KEY}/chunk_index", f"videos/{VIDEO_KEY}/file_index",
            f"videos/{VIDEO_KEY}/from_timestamp", f"videos/{VIDEO_KEY}/to_timestamp",
            "tasks", "length",
        )
    }  # fmt: skip
    index = 0
    video_start = 0.0
    for e, length in enumerate(lengths):
        start = index
        for k in range(length):
            rows["observation.state"].append([e + k / 4, -float(k)])
            rows["action"].append([k * 1.5, float(e)])
            rows["episode_index"].append(e)
            rows["frame_index"].append(frame_index(e, k))
            rows["timestamp"].append(timestamp(e, k))
            rows["next.reward"].append(k / 8)
            rows["next.done"].append(k == length - 1)
            rows["next.success"].append(False)
            rows["index"].append(index)
            rows["task_index"].append(task_index(e, k))
            index += 1
        episodes["episode_index"].append(e)
        episodes["data/chunk_index"].append(0)
        episodes["data/file_index"].append(0)
        episodes["dataset_from_index"].append(start)
        episodes["dataset_to_index"].append(index)
        episodes[f"videos/{VIDEO_KEY}/chunk_index"].append(0)
        episodes[f"videos/{VIDEO_KEY}/file_index"].append(0)
        episodes[f"videos/{VIDEO_KEY}/from_timestamp"].append(video_start)
        video_start += length / rate
        episodes[f"videos/{VIDEO_KEY}/to_timestamp"].append(video_start)
        episodes["tasks"].append(list(tasks))
        episodes["length"].append(length)

    vector_type = _vector_type(fixed_size_lists, len(state_names))
    data = pa.table(
        {
            "observation.state": pa.array(rows["observation.state"], vector_type),
            "action": pa.array(rows["action"], vector_type),
            "episode_index": pa.array(rows["episode_index"], pa.int64()),
            "frame_index": pa.array(rows["frame_index"], pa.int64()),
            "timestamp": pa.array(rows["timestamp"], pa.float32()),
            "next.reward": pa.array(rows["next.reward"], pa.float32()),
            "next.done": pa.array(rows["next.done"], pa.bool_()),
            "next.success": pa.array(rows["next.success"], pa.bool_()),
            "index": pa.array(rows["index"], pa.int64()),
            "task_index": pa.array(rows["task_index"], pa.int64()),
        }
    )
    if pandas_tasks:
        task_table = pa.table(
            {
                "task_index": pa.array(range(len(tasks)), pa.int64()),
                "__index_level_0__": pa.array(list(tasks), pa.string()),
            }
        ).replace_schema_metadata(
            {b"pandas": json.dumps({"index_columns": ["__index_level_0__"]}).encode()}
        )
    else:
        task_table = pa.table(
            {
                "task_index": pa.array(range(len(tasks)), pa.int64()),
                "task": pa.array(list(tasks), pa.string()),
            }
        )

    (root / "meta" / "episodes" / "chunk-000").mkdir(parents=True, exist_ok=True)
    (root / "data" / "chunk-000").mkdir(parents=True, exist_ok=True)
    (root / video_path).parent.mkdir(parents=True, exist_ok=True)
    (root / "README.md").write_text("# synthetic lerobot\n")
    (root / "meta" / "info.json").write_text(json.dumps(info, indent=2))
    pq.write_table(task_table, root / "meta" / "tasks.parquet")
    pq.write_table(pa.table(episodes), root / EPISODES_PATH)
    pq.write_table(data, root / DATA_PATH)
    (root / video_path).write_bytes(b"\x00\x00\x00\x18ftypisom" + b"\x02" * 512)
    return root


__all__ = [
    "DATA_PATH",
    "EPISODES_PATH",
    "INCLUDE",
    "VIDEO_KEY",
    "VIDEO_PATH",
    "float32",
    "write_lerobot_tree",
]
