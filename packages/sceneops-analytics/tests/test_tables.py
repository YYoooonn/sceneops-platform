"""Observation-centric Scene table builders."""

from __future__ import annotations

import json

from sceneops_core.artifacts.logical_uri import LogicalNamespace
from sceneops_core.provenance import ContentUnitSource, manifest_artifact_id_for
from sceneops_core.scenes.schemas import (
    SceneManifest,
    project_scene_revision,
    scene_id_for,
)
from sceneops_core.scenes.testing import build_scene_manifest, recording_source
from sceneops_core.sources import content_id_for
from sceneops_analytics.tables import (
    OBSERVATIONS_SCHEMA,
    TABLE_BUILDERS,
    build_keyframes_table,
    build_observations_table,
    build_scenes_table,
)


def _manifest(key: str = "scene-a"):
    return build_scene_manifest(
        source=recording_source(unit_key=key),
        keyframe_timestamps_ns=(1_000, 3_000),
    )


def _revision(manifest):
    return project_scene_revision(
        manifest=manifest,
        manifest_artifact_id=manifest_artifact_id_for(
            unit_id=scene_id_for(manifest.lineage.source),
            manifest_checksum=manifest.checksum(),
        ),
        manifest_checksum=manifest.checksum(),
    )


def _scenes_table(records):
    return build_scenes_table(dataset_id="d", dataset_version="v1", scenes=records)


def test_table_names():
    assert TABLE_BUILDERS == ("scenes", "observations", "keyframes")


def test_build_scenes_table_row_per_scene():
    manifest = _manifest()
    record = _revision(manifest)
    df = _scenes_table([record])
    assert df.height == 1
    row = df.row(0, named=True)
    assert (row["dataset_id"], row["dataset_version"]) == ("d", "v1")
    assert (row["robot_run_id"], row["unit_key"]) == ("run-001", "scene-a")
    assert "source_kind" not in row and "external_format" not in row
    assert row["manifest_artifact_id"] == record.manifest_artifact_id
    assert (row["observation_count"], row["keyframe_count"]) == (5, 2)
    assert row["observed_channels"] == ["CAM_FRONT", "LIDAR_TOP"]


def test_build_scenes_table_empty_is_empty_not_error():
    assert _scenes_table([]).height == 0


def test_observations_table_keeps_every_observation_with_its_own_time():
    df = build_observations_table(
        dataset_id="d", dataset_version="v1", manifests=[("scene-1", _manifest())]
    )
    assert df.height == 5
    assert df["timestamp_ns"].to_list() == [1_007, 2_000, 3_007, 1_000, 3_000]
    assert set(df["modality"].to_list()) == {"camera", "lidar"}
    assert df["source_clock"].unique().to_list() == ["mcap_log_time"]
    assert df["payload_checksum"].null_count() == 0
    assert df["payload_uri"].null_count() == 0
    assert all(
        uri.startswith("sceneops://observation-payloads/run-001/")
        for uri in df["payload_uri"].to_list()
    )
    assert df["payload_selector"].null_count() == df.height
    assert "payload_artifact_id" not in df.columns


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
    df = build_keyframes_table(
        dataset_id="d", dataset_version="v1", manifests=manifests
    )
    assert df["source_clock"].to_list() == ["mcap_log_time"] * df.height


def test_scenes_table_carries_the_segment_window():
    manifest = _manifest()
    record = _revision(manifest)
    row = _scenes_table([record]).row(0, named=True)
    assert (
        row["window_clock"],
        row["window_start_timestamp_ns"],
        row["window_end_timestamp_ns"],
    ) == ("mcap_log_time", 0, 10_000_000_000)


def test_keyframes_table_has_one_row_per_group_and_no_annotation_data():
    manifests = [("scene-1", _manifest("a")), ("scene-2", _manifest("b"))]
    keyframes = build_keyframes_table(
        dataset_id="d", dataset_version="v1", manifests=manifests
    )
    assert keyframes.height == 4
    assert not any("annotation" in column for column in keyframes.columns)


def test_every_source_exports_the_same_payload_columns():
    """A content Scene's preserved files are exported exactly like recording
    payloads: by reference, with no source-specific column or refusal; a
    selector is kept as canonical JSON."""
    checksum = "sha256:" + "c" * 64
    source = ContentUnitSource(
        content_id=content_id_for(checksum),
        content_checksum=checksum,
        source_clock="mcap_log_time",
        start_timestamp_ns=0,
        end_timestamp_ns=10_000_000_000,
        unit_key="scene-token",
    )
    payload = json.loads(build_scene_manifest(source=source).to_canonical_bytes())
    for observation in payload["observations"]:
        observation["payload"].update(
            media_type="video/mp4",
            selector={"kind": "video_frame", "timestamp_ns": 33_333_333},
        )
    content = SceneManifest.model_validate(payload)
    df = build_observations_table(
        dataset_id="ds",
        dataset_version="v1",
        manifests=[("scene-1", content), ("scene-2", _manifest())],
    )
    assert df.columns == list(OBSERVATIONS_SCHEMA)
    rows = df.filter(df["scene_id"] == "scene-1").to_dicts()
    assert [r["payload_uri"] for r in rows] == [
        o.payload.uri for o in content.observations
    ]
    assert {LogicalNamespace(r["payload_uri"].split("/")[2]) for r in rows} == {
        LogicalNamespace.SOURCES
    }
    assert {r["payload_selector"] for r in rows} == {
        '{"kind":"video_frame","timestamp_ns":33333333}'
    }
