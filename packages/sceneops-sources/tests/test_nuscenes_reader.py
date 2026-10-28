"""The native nuScenes v1.0 reader on synthetic trees of the v1.0 layout
(ADR-015 §10; M3.0 D6). Expectations are the values the tree was written
with, never the reader's own output."""

from __future__ import annotations

from pathlib import Path

import pytest

from sceneops_core.sources import SnapshotSelection
from sceneops_sources import (
    ContentFileIntegrityError,
    LocalContentFiles,
    build_inventory,
    select_files,
)
from sceneops_sources.nuscenes import (
    NUSCENES_TABLES,
    NuScenesFormatError,
    UnsupportedNuScenesVersionError,
    nuscenes_read_paths,
    read_nuscenes_content,
)
from sceneops_sources.nuscenes_synthetic import (
    BASE_US,
    CAMERA_K,
    CHANNELS,
    VERSION,
    token,
    write_nuscenes_tree,
)


def _files(root: Path, include=("**",)) -> LocalContentFiles:
    paths = select_files(root, SnapshotSelection(include=tuple(include)))
    return LocalContentFiles(root, build_inventory(root, paths))


def test_reads_scenes_samples_readings_poses_and_calibrations(tmp_path):
    root = write_nuscenes_tree(tmp_path, scenes=2, samples=3, sweeps=1)
    content = read_nuscenes_content(_files(root))

    assert content.version == VERSION
    assert [s.token for s in content.scenes] == [token("scene", 0), token("scene", 1)]
    scene = content.scene(token("scene", 1))
    assert scene is not None and scene.name == "scene-0002"
    samples = content.scene_samples(scene)
    assert [s.token for s in samples] == [token("sample", 1, k) for k in range(3)]
    assert [s.timestamp for s in samples] == [
        BASE_US + 10**8 + k * 500_000 for k in range(3)
    ]
    readings = content.scene_readings(scene)
    # One key frame and one sweep per channel and sample.
    assert len(readings) == 3 * len(CHANNELS) * 2
    assert sum(r.is_key_frame for r in readings) == 3 * len(CHANNELS)
    camera = next(r for r in readings if content.sensor_of(r).channel == "CAM_FRONT")
    assert (camera.width, camera.height, camera.fileformat) == (1600, 900, "jpg")
    calibrated = content.calibrated_sensors[camera.calibrated_sensor_token]
    assert calibrated.camera_intrinsic == tuple(tuple(row) for row in CAMERA_K)
    assert calibrated.rotation == (1.0, 0.0, 0.0, 0.0)
    radar = next(r for r in readings if content.sensor_of(r).channel == "RADAR_FRONT")
    assert (
        content.calibrated_sensors[radar.calibrated_sensor_token].camera_intrinsic == ()
    )
    pose = content.ego_poses[camera.ego_pose_token]
    assert pose.timestamp == camera.timestamp
    assert pose.rotation == (0.6, 0.0, 0.0, -0.8)


def test_reads_only_the_tables(tmp_path):
    root = write_nuscenes_tree(tmp_path)
    files = _files(root)
    assert nuscenes_read_paths(files.inventory) == [
        f"{VERSION}/{name}.json" for name in NUSCENES_TABLES
    ]
    # Sensor files and other tables are never read, so damaging one does not
    # fail the reader.
    sensor_file = next(p for p in (root / "samples").rglob("*.jpg"))
    sensor_file.write_bytes(b"not the inventoried image")
    (root / VERSION / "log.json").write_text("not json")
    read_nuscenes_content(files)


def test_every_byte_read_is_verified_against_the_inventory(tmp_path):
    root = write_nuscenes_tree(tmp_path)
    files = _files(root)
    path = root / VERSION / "sample.json"
    path.write_text(path.read_text().replace("scene_token", "scene_token "))
    with pytest.raises(ContentFileIntegrityError):
        read_nuscenes_content(files)


@pytest.mark.parametrize("version", ["v0.1-mini", "v2.0-trainval", "mini"])
def test_other_table_versions_are_refused(tmp_path, version):
    root = write_nuscenes_tree(tmp_path, version=version)
    with pytest.raises(UnsupportedNuScenesVersionError):
        read_nuscenes_content(_files(root))


def test_exactly_one_table_directory_is_read(tmp_path):
    root = write_nuscenes_tree(tmp_path)
    write_nuscenes_tree(tmp_path, version="v1.0-trainval")
    with pytest.raises(NuScenesFormatError, match="exactly one is read"):
        read_nuscenes_content(_files(root))


def test_a_missing_table_is_refused(tmp_path):
    root = write_nuscenes_tree(tmp_path)
    (root / VERSION / "ego_pose.json").unlink()
    with pytest.raises(NuScenesFormatError, match="ego_pose"):
        read_nuscenes_content(_files(root))


def _break(table: str, field: str, value):
    def mutate(tables):
        tables[table][0][field] = value

    return mutate


@pytest.mark.parametrize(
    ("table", "field", "value", "match"),
    [
        ("sample_data", "ego_pose_token", "f" * 32, "unknown ego_pose"),
        ("sample_data", "calibrated_sensor_token", "f" * 32, "unknown calibrated"),
        ("sample_data", "sample_token", "f" * 32, "unknown sample"),
        ("calibrated_sensor", "sensor_token", "f" * 32, "unknown sensor"),
        ("sample", "scene_token", "f" * 32, "unknown scene"),
        ("sample_data", "timestamp", 1.5, "not an integer"),
        ("sample_data", "is_key_frame", 1, "not a boolean"),
        ("ego_pose", "rotation", [1.0, 0.0, 0.0], "4 finite numbers"),
        ("calibrated_sensor", "camera_intrinsic", [[1.0]], "3x3"),
    ],
)
def test_broken_tables_are_refused(tmp_path, table, field, value, match):
    root = write_nuscenes_tree(tmp_path, mutate_tables=_break(table, field, value))
    with pytest.raises(NuScenesFormatError, match=match):
        read_nuscenes_content(_files(root))


def test_a_duplicated_token_is_refused(tmp_path):
    def duplicate(tables):
        tables["ego_pose"].append(dict(tables["ego_pose"][0]))

    root = write_nuscenes_tree(tmp_path, mutate_tables=duplicate)
    with pytest.raises(NuScenesFormatError, match="twice"):
        read_nuscenes_content(_files(root))


def test_a_sample_chain_that_disagrees_with_its_scene_is_refused(tmp_path):
    def shorten(tables):
        tables["scene"][0]["nbr_samples"] = 2

    content = read_nuscenes_content(
        _files(write_nuscenes_tree(tmp_path, mutate_tables=shorten))
    )
    with pytest.raises(NuScenesFormatError, match="declares 2"):
        content.scene_samples(content.scenes[0])
