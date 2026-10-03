"""SceneManifestProfiler over canonical SceneManifest revisions."""

from __future__ import annotations

import json

from sceneops_core.scenes.schemas import SceneManifest
from sceneops_core.scenes.testing import build_scene_manifest
from sceneops_worker.scenes.profiling import SceneManifestProfiler

_profiler = SceneManifestProfiler()


def test_profile_counts_every_observation_not_only_keyframes():
    manifest = build_scene_manifest(
        keyframe_timestamps_ns=(1_000, 3_000, 5_000), annotations_per_keyframe=2
    )
    result = _profiler.profile(scene_id="scene-a", manifest=manifest)

    assert result.scene_id == "scene-a"
    assert result.keyframe_count == 3
    # 3 camera keyframe observations + 2 camera sweeps + 3 lidar.
    assert result.observation_count == 8
    assert result.observations_by_channel == {"CAM_FRONT": 5, "LIDAR_TOP": 3}
    assert result.annotation_count == 6
    assert result.category_distribution == {"vehicle.car": 6}
    assert result.calibration_coverage == {"CAM_FRONT": 1.0, "LIDAR_TOP": 1.0}
    assert result.ego_pose_coverage == {"CAM_FRONT": 1.0, "LIDAR_TOP": 1.0}
    assert result.camera_intrinsic_coverage == {"CAM_FRONT": 1.0}
    assert result.image_size_coverage == {"CAM_FRONT": 1.0}


def test_partial_coverage():
    payload = json.loads(build_scene_manifest().to_canonical_bytes())
    camera = [o for o in payload["observations"] if o["channel"] == "CAM_FRONT"]
    camera[0]["image_size"] = None
    camera[0]["ego_pose_id"] = None
    result = _profiler.profile(
        scene_id="scene-a", manifest=SceneManifest.model_validate(payload)
    )
    assert result.image_size_coverage == {"CAM_FRONT": 2 / 3}
    assert result.ego_pose_coverage == {"CAM_FRONT": 2 / 3, "LIDAR_TOP": 1.0}
