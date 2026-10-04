"""Observation-centric Scene table builders."""

from __future__ import annotations

from sceneops_core.scenes.schemas import project_scene_record
from sceneops_core.scenes.testing import build_scene_manifest, recording_source
from sceneops_analytics.tables import (
    TABLE_BUILDERS,
    build_annotations_table,
    build_keyframes_table,
    build_observations_table,
    build_scenes_table,
)


def _manifest(key: str = "scene-a"):
    return build_scene_manifest(
        source=recording_source(unit_key=key),
        keyframe_timestamps_ns=(1_000, 3_000),
        annotations_per_keyframe=2,
    )


def test_table_names():
    assert TABLE_BUILDERS == ("scenes", "observations", "keyframes", "annotations")


def test_build_scenes_table_row_per_scene():
    manifest = _manifest()
    record = project_scene_record(
        dataset_id="d",
        dataset_version="v1",
        manifest=manifest,
        manifest_artifact_id="art-1",
        manifest_checksum=manifest.checksum(),
    )
    df = build_scenes_table([record])
    assert df.height == 1
    row = df.row(0, named=True)
    assert (row["robot_run_id"], row["unit_key"]) == ("run-001", "scene-a")
    assert "source_kind" not in row and "external_format" not in row
    assert row["manifest_artifact_id"] == "art-1"
    assert (row["observation_count"], row["keyframe_count"]) == (5, 2)
    assert row["observed_channels"] == ["CAM_FRONT", "LIDAR_TOP"]


def test_build_scenes_table_empty_is_empty_not_error():
    assert build_scenes_table([]).height == 0


def test_observations_table_keeps_every_observation_with_its_own_time():
    df = build_observations_table(
        dataset_id="d", dataset_version="v1", manifests=[("scene-1", _manifest())]
    )
    assert df.height == 5
    assert df["timestamp_ns"].to_list() == [1_007, 2_000, 3_007, 1_000, 3_000]
    assert set(df["modality"].to_list()) == {"camera", "lidar"}
    assert df["source_clock"].unique().to_list() == ["mcap_log_time"]
    assert df["payload_checksum"].null_count() == 0
    assert df["payload_artifact_id"].null_count() == 0
    assert "payload_uri" not in df.columns


def test_every_timestamp_row_carries_its_own_clock():
    manifest = build_scene_manifest(
        camera_clock="camera.exposure_clock", keyframe_timestamps_ns=(1_000,)
    )
    manifests = [("scene-1", manifest)]
    observations = build_observations_table(
        dataset_id="d", dataset_version="v1", manifests=manifests
    )
    clocks = dict(
        zip(observations["channel"].to_list(), observations["source_clock"].to_list())
    )
    assert clocks == {
        "CAM_FRONT": "camera.exposure_clock",
        "LIDAR_TOP": "mcap_log_time",
    }
    for table in (build_keyframes_table, build_annotations_table):
        df = table(dataset_id="d", dataset_version="v1", manifests=manifests)
        assert df["source_clock"].to_list() == ["mcap_log_time"] * df.height


def test_scenes_table_carries_the_segment_window():
    manifest = _manifest()
    record = project_scene_record(
        dataset_id="d",
        dataset_version="v1",
        manifest=manifest,
        manifest_artifact_id="art-1",
        manifest_checksum=manifest.checksum(),
    )
    row = build_scenes_table([record]).row(0, named=True)
    assert (
        row["window_clock"],
        row["window_start_timestamp_ns"],
        row["window_end_timestamp_ns"],
    ) == ("mcap_log_time", 0, 10_000_000_000)


def test_keyframes_and_annotations_tables():
    manifests = [("scene-1", _manifest("a")), ("scene-2", _manifest("b"))]
    keyframes = build_keyframes_table(
        dataset_id="d", dataset_version="v1", manifests=manifests
    )
    annotations = build_annotations_table(
        dataset_id="d", dataset_version="v1", manifests=manifests
    )
    assert keyframes.height == 4
    assert keyframes["annotation_count"].to_list() == [2, 2, 2, 2]
    assert annotations.height == 8
    assert annotations.row(0, named=True)["size_wlh_m"] == [1.9, 4.5, 1.6]
