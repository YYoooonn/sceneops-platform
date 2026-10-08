"""SceneManifestValidator over canonical SceneManifest revisions."""

from __future__ import annotations

import json

from sceneops_core.scenes.schemas import SceneManifest
from sceneops_core.scenes.testing import build_scene_manifest
from sceneops_scenes.validation import SceneManifestValidator

_validator = SceneManifestValidator()


def _mutated(manifest: SceneManifest, mutate) -> SceneManifest:
    payload = json.loads(manifest.to_canonical_bytes())
    mutate(payload)
    return SceneManifest.model_validate(payload)


def _issues(result, issue_type):
    return [i for i in result.issues if i.type == issue_type]


def test_complete_scene_is_ready():
    result = _validator.validate(
        scene_id="scene-a",
        manifest=build_scene_manifest(),
        required_channels=["CAM_FRONT", "LIDAR_TOP"],
        validate_keyframes=True,
    )
    assert result.status == "ready"
    assert not result.should_block
    assert result.issues == []
    assert result.observed_channels == ["CAM_FRONT", "LIDAR_TOP"]
    assert (result.observation_count, result.keyframe_count) == (5, 2)


def test_missing_required_channel_blocks():
    result = _validator.validate(
        scene_id="scene-a",
        manifest=build_scene_manifest(),
        required_channels=["CAM_FRONT", "RADAR_FRONT"],
    )
    assert result.should_block
    assert result.status == "failed"
    assert result.missing_channels == ["RADAR_FRONT"]
    assert [i.channel for i in _issues(result, "missing_channel")] == ["RADAR_FRONT"]


def test_declared_channel_without_observations_is_a_warning():
    def add_radar(payload):
        payload["coordinate_frames"].append(
            {"frame_id": "RADAR_FRONT", "role": "sensor"}
        )
        payload["coordinate_frames"].sort(key=lambda f: f["frame_id"])
        payload["channels"].append(
            {
                "channel": "RADAR_FRONT",
                "modality": "radar",
                "frame_id": "RADAR_FRONT",
                "source_clock": "nuscenes.timestamp_us",
                "sensor_id": None,
            }
        )
        payload["channels"].sort(key=lambda c: c["channel"])

    result = _validator.validate(
        scene_id="scene-a", manifest=_mutated(build_scene_manifest(), add_radar)
    )
    assert result.status == "warning"
    assert [i.channel for i in _issues(result, "empty_channel")] == ["RADAR_FRONT"]


def test_keyframe_channel_coverage_is_aggregated_and_optionally_blocking():
    def drop_lidar_from_first_keyframe(payload):
        group = payload["groups"][0]
        group["observation_ids"] = [
            i for i in group["observation_ids"] if not i.startswith("lidar")
        ]

    manifest = _mutated(build_scene_manifest(), drop_lidar_from_first_keyframe)
    warning = _validator.validate(
        scene_id="scene-a",
        manifest=manifest,
        required_channels=["CAM_FRONT", "LIDAR_TOP"],
        validate_keyframes=True,
    )
    [issue] = _issues(warning, "keyframe_missing_channel")
    assert (issue.channel, issue.count, issue.blocking) == ("LIDAR_TOP", 1, False)
    assert not warning.should_block

    blocking = _validator.validate(
        scene_id="scene-a",
        manifest=manifest,
        required_channels=["LIDAR_TOP"],
        validate_keyframes=True,
        block_on_keyframe_missing_channels=True,
    )
    assert blocking.should_block


def test_geometry_gaps_are_counted_per_channel():
    def strip_geometry(payload):
        for observation in payload["observations"]:
            if observation["channel"] == "CAM_FRONT":
                observation["image_size"] = None
            if observation["channel"] == "LIDAR_TOP":
                observation["calibration_id"] = None
        for calibration in payload["calibrations"]:
            calibration["camera_intrinsic"] = None
        payload["poses"] = []
        for observation in payload["observations"]:
            observation["ego_pose_id"] = None

    result = _validator.validate(
        scene_id="scene-a", manifest=_mutated(build_scene_manifest(), strip_geometry)
    )
    assert not result.should_block
    assert {(i.type, i.channel, i.count) for i in result.issues} == {
        ("missing_calibration", "LIDAR_TOP", 2),
        ("missing_camera_intrinsic", "CAM_FRONT", 3),
        ("missing_image_size", "CAM_FRONT", 3),
        ("missing_ego_poses", None, None),
    }
