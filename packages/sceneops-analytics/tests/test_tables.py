from __future__ import annotations

from sceneops_core.scenes.schemas import (
    SceneAnnotationManifest,
    SceneManifest,
    SceneOriginType,
    SceneRecord,
    SceneSampleManifest,
    SceneSensorFrameManifest,
    SceneStatus,
)

from sceneops_analytics.tables import (
    build_annotations_table,
    build_samples_table,
    build_scenes_table,
    build_sensor_frames_table,
)

DATASET_ID = "nuscenes"
DATASET_VERSION = "v1.0-mini"


def _scene_record(scene_id: str) -> SceneRecord:
    return SceneRecord(
        scene_id=scene_id,
        dataset_id=DATASET_ID,
        dataset_version=DATASET_VERSION,
        status=SceneStatus.PROFILED,
        origin_type=SceneOriginType.REAL,
        sample_count=1,
        frame_count=2,
        annotation_count=1,
        channels=["CAM_FRONT", "LIDAR_TOP"],
        has_ground_truth=True,
    )


def _scene_manifest(scene_id: str) -> SceneManifest:
    frame_cam = SceneSensorFrameManifest(
        frame_id=f"{scene_id}-frame-cam",
        sample_id=f"{scene_id}-sample-0",
        timestamp_us=1000,
        channel="CAM_FRONT",
        uri=f"file:///{scene_id}/cam_front/0.jpg",
        calibration_id="calib-cam-front",
        ego_pose_id="ego-0",
    )
    frame_lidar = SceneSensorFrameManifest(
        frame_id=f"{scene_id}-frame-lidar",
        sample_id=f"{scene_id}-sample-0",
        timestamp_us=1000,
        channel="LIDAR_TOP",
        uri=f"file:///{scene_id}/lidar_top/0.pcd",
        calibration_id="calib-lidar-top",
        ego_pose_id="ego-0",
    )
    annotation = SceneAnnotationManifest(
        annotation_id=f"{scene_id}-ann-0",
        sample_id=f"{scene_id}-sample-0",
        category="vehicle.car",
        translation=[1.0, 2.0, 3.0],
        size=[4.0, 2.0, 1.5],
        rotation=[1.0, 0.0, 0.0, 0.0],
        num_lidar_points=10,
    )
    sample = SceneSampleManifest(
        sample_id=f"{scene_id}-sample-0",
        scene_id=scene_id,
        timestamp_us=1000,
        frame_index=0,
        sensor_frames=[frame_cam, frame_lidar],
        annotations=[annotation],
    )
    return SceneManifest(
        scene_id=scene_id,
        dataset_id=DATASET_ID,
        dataset_version=DATASET_VERSION,
        samples=[sample],
        sample_count=1,
        frame_count=2,
        annotation_count=1,
        channels=["CAM_FRONT", "LIDAR_TOP"],
    )


def test_build_scenes_table_row_per_scene():
    scenes = [_scene_record("scene-a"), _scene_record("scene-b")]

    df = build_scenes_table(scenes)

    assert df.height == 2
    assert set(df["scene_id"]) == {"scene-a", "scene-b"}
    assert df["dataset_id"].to_list() == [DATASET_ID, DATASET_ID]


def test_build_scenes_table_empty_is_empty_not_error():
    df = build_scenes_table([])
    assert df.height == 0
    assert "scene_id" in df.columns


def test_build_samples_table_flattens_manifests():
    manifests = [_scene_manifest("scene-a"), _scene_manifest("scene-b")]

    df = build_samples_table(
        dataset_id=DATASET_ID, dataset_version=DATASET_VERSION, manifests=manifests
    )

    assert df.height == 2  # one sample per scene manifest
    assert df["sensor_frame_count"].to_list() == [2, 2]
    assert df["annotation_count"].to_list() == [1, 1]


def test_build_sensor_frames_table_flattens_frames_across_samples():
    manifests = [_scene_manifest("scene-a")]

    df = build_sensor_frames_table(
        dataset_id=DATASET_ID, dataset_version=DATASET_VERSION, manifests=manifests
    )

    assert df.height == 2
    assert set(df["channel"]) == {"CAM_FRONT", "LIDAR_TOP"}
    assert df["scene_id"].to_list() == ["scene-a", "scene-a"]


def test_build_annotations_table_flattens_annotations():
    manifests = [_scene_manifest("scene-a"), _scene_manifest("scene-b")]

    df = build_annotations_table(
        dataset_id=DATASET_ID, dataset_version=DATASET_VERSION, manifests=manifests
    )

    assert df.height == 2
    assert df["category"].to_list() == ["vehicle.car", "vehicle.car"]
    assert df["translation"][0].to_list() == [1.0, 2.0, 3.0]
