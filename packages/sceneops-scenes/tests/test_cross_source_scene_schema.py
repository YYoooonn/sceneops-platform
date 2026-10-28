"""One Scene schema for both source kinds (ADR-015 §6.6; M3 plan S4 tests).

The same strict loader, validator and profiler run over a recording-sourced
Scene and a content-sourced nuScenes Scene, with no source branch: both are
SceneManifest v4, and consumers see only its structure.
"""

from __future__ import annotations

from sceneops_core.provenance import PinnedContent
from sceneops_core.scenes.nuscenes_build import NuScenesSceneBuildConfig
from sceneops_core.scenes.schemas import load_canonical_scene_manifest
from sceneops_core.scenes.testing import build_scene_manifest
from sceneops_core.sources import SnapshotSelection
from sceneops_scenes.nuscenes_builder import plan_nuscenes_scenes
from sceneops_scenes.profiling import SceneManifestProfiler
from sceneops_scenes.validation import SceneManifestValidator
from sceneops_sources import LocalContentFiles, build_inventory, select_files
from sceneops_sources.nuscenes_synthetic import CHANNELS, INCLUDE, write_nuscenes_tree


def _content_scene(root):
    write_nuscenes_tree(root, scenes=1)
    inventory = build_inventory(
        root, select_files(root, SnapshotSelection(include=INCLUDE))
    )
    plan = plan_nuscenes_scenes(
        pinned=PinnedContent(inventory=inventory),
        files=LocalContentFiles(root, inventory),
        config=NuScenesSceneBuildConfig(channels=sorted(CHANNELS)),
    )
    return plan.scenes[0].manifest


def test_one_loader_validator_and_profiler_for_both_sources(tmp_path):
    recording = build_scene_manifest()
    content = _content_scene(tmp_path)
    assert recording.schema_version == content.schema_version
    for manifest, required in (
        (recording, ["CAM_FRONT", "LIDAR_TOP"]),
        (content, ["CAM_FRONT", "LIDAR_TOP"]),
    ):
        assert load_canonical_scene_manifest(manifest.to_canonical_bytes()) == manifest
        result = SceneManifestValidator().validate(
            scene_id="scene-x",
            manifest=manifest,
            required_channels=required,
            validate_keyframes=True,
            block_on_keyframe_missing_channels=True,
        )
        assert not result.should_block, result.issues
        profile = SceneManifestProfiler().profile(scene_id="scene-x", manifest=manifest)
        assert profile.observation_count == len(manifest.observations)
        assert profile.keyframe_count == len(manifest.keyframes())
        assert set(profile.ego_pose_coverage.values()) == {1.0}
    content_result = SceneManifestValidator().validate(
        scene_id="scene-x", manifest=content
    )
    assert content_result.status == "ready", content_result.issues
