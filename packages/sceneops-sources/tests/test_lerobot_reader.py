"""The native LeRobot v3.0 reader on synthetic trees of the v3.0 layout
(ADR-015 §10; M3.0 D6). Expectations are the values the tree was written
with, never the reader's own output."""

from __future__ import annotations

import json
from pathlib import Path

import pyarrow as pa
import pyarrow.parquet as pq
import pytest

from sceneops_core.sources import SnapshotSelection
from sceneops_sources import (
    ContentFileIntegrityError,
    LocalContentFiles,
    build_inventory,
    select_files,
)
from sceneops_sources.lerobot import (
    LeRobotFormatError,
    UnsupportedLeRobotVersionError,
    lerobot_read_paths,
    read_lerobot_content,
)
from sceneops_sources.lerobot_synthetic import (
    DATA_PATH,
    INCLUDE,
    VIDEO_PATH,
    float32,
    write_lerobot_tree,
)


def _files(root: Path) -> LocalContentFiles:
    paths = select_files(root, SnapshotSelection(include=INCLUDE))
    return LocalContentFiles(root, build_inventory(root, paths))


@pytest.mark.parametrize("names_form", ["dict", "list"])
@pytest.mark.parametrize("fixed", [True, False])
@pytest.mark.parametrize("pandas_tasks", [True, False])
def test_reads_both_names_forms_list_types_and_task_tables(
    tmp_path, names_form, fixed, pandas_tasks
):
    root = write_lerobot_tree(
        tmp_path,
        lengths=(3, 2),
        names_form=names_form,
        fixed_size_lists=fixed,
        pandas_tasks=pandas_tasks,
    )
    content = read_lerobot_content(_files(root))

    assert content.codebase_version == "v3.0"
    assert content.fps == 10
    assert content.tasks == {0: "push the block"}
    assert content.features["observation.state"].names == (
        {"motors": ["motor_0", "motor_1"]}
        if names_form == "dict"
        else ["motor_0", "motor_1"]
    )
    assert content.features["observation.image"].is_video
    assert [(e.episode_index, e.length) for e in content.episodes] == [(0, 3), (1, 2)]
    second = content.episodes[1]
    assert second.data_path == DATA_PATH
    assert (second.dataset_from_index, second.dataset_to_index) == (3, 5)
    assert second.videos["observation.image"].path == VIDEO_PATH
    assert second.videos["observation.image"].from_timestamp == 0.3
    assert second.referenced_paths() == sorted([DATA_PATH, VIDEO_PATH])

    rows = content.episode_rows(second).to_pylist()
    assert [r["index"] for r in rows] == [3, 4]
    assert [r["frame_index"] for r in rows] == [0, 1]
    assert rows[1]["observation.state"] == [1.25, -1.0]
    assert rows[1]["timestamp"] == float32(0.1)


def test_reads_only_metadata_and_data_files(tmp_path):
    root = write_lerobot_tree(tmp_path)
    files = _files(root)
    assert lerobot_read_paths(files.inventory) == [
        DATA_PATH,
        "meta/episodes/chunk-000/file-000.parquet",
        "meta/info.json",
        "meta/tasks.parquet",
    ]
    (root / VIDEO_PATH).write_bytes(b"not the inventoried video")
    # The video is never read, so a damaged video does not fail the reader.
    read_lerobot_content(files)


def test_every_byte_read_is_verified_against_the_inventory(tmp_path):
    root = write_lerobot_tree(tmp_path)
    files = _files(root)
    info = json.loads((root / "meta/info.json").read_text())
    info["fps"] = 11
    (root / "meta/info.json").write_text(json.dumps(info))
    with pytest.raises(ContentFileIntegrityError):
        read_lerobot_content(files)


@pytest.mark.parametrize("version", ["v2.1", "v2.0", "v3.1", None])
def test_other_codebase_versions_are_refused(tmp_path, version):
    root = write_lerobot_tree(tmp_path, codebase_version=version or "")
    if version is None:
        info = json.loads((root / "meta/info.json").read_text())
        del info["codebase_version"]
        (root / "meta/info.json").write_text(json.dumps(info))
    with pytest.raises(UnsupportedLeRobotVersionError):
        read_lerobot_content(_files(root))


def test_rows_of_another_episode_in_the_range_are_refused(tmp_path):
    root = write_lerobot_tree(tmp_path, lengths=(2, 2))
    table = pq.read_table(root / DATA_PATH)
    episode_index = table.column("episode_index").to_pylist()
    episode_index[1] = 1
    table = table.set_column(
        table.column_names.index("episode_index"),
        "episode_index",
        pa.array(episode_index, pa.int64()),
    )
    pq.write_table(table, root / DATA_PATH)
    content = read_lerobot_content(_files(root))
    with pytest.raises(LeRobotFormatError, match="another episode_index"):
        content.episode_rows(content.episodes[0])


def test_a_row_range_that_disagrees_with_the_length_is_refused(tmp_path):
    root = write_lerobot_tree(tmp_path, lengths=(2, 2))
    path = root / "meta/episodes/chunk-000/file-000.parquet"
    table = pq.read_table(path)
    lengths = table.column("length").to_pylist()
    lengths[0] = 3
    table = table.set_column(
        table.column_names.index("length"),
        "length",
        pa.array(lengths, pa.int64()),
    )
    pq.write_table(table, path)
    with pytest.raises(LeRobotFormatError, match="is not its length"):
        read_lerobot_content(_files(root))
