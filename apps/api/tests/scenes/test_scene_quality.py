"""Scene quality builder (pure): readiness comes only from run records that
assessed the Scene's current manifest revision. Labels and detection
selectability are not Scene quality (ADR-007 §33)."""

from __future__ import annotations

from sceneops_core.runs.schemas import RunStatus
from sceneops_core.scenes import SceneReadiness
from sceneops_core.scenes.schemas import project_scene_record
from sceneops_core.scenes.schemas.runs import (
    SceneProfileRunRecord,
    SceneValidationRunRecord,
)
from sceneops_core.scenes.testing import build_scene_manifest

from app.domains.scenes.quality import build_scene_quality, compute_scene_readiness


def _scene(*, annotations_per_keyframe: int = 1, revision: str = "art-rev-1"):
    manifest = build_scene_manifest(annotations_per_keyframe=annotations_per_keyframe)
    return project_scene_record(
        dataset_id="ds",
        dataset_version="v1",
        manifest=manifest,
        manifest_artifact_id=revision,
        manifest_checksum=manifest.checksum(),
    )


def _validation(
    scene, *, status="ready", block=False, revision=None, run_status=RunStatus.SUCCEEDED
):
    return SceneValidationRunRecord(
        run_id="val-001",
        status=run_status,
        scene_id=scene.scene_id,
        manifest_artifact_id=revision[0] if revision else scene.manifest_artifact_id,
        manifest_checksum=revision[1] if revision else scene.manifest_checksum,
        validation_status=status,
        should_block_pipeline=block,
        error_count=1 if block else 0,
        warning_count=1 if status == "warning" else 0,
        issue_count=1 if (block or status == "warning") else 0,
        checked_observation_count=5,
        checked_keyframe_count=2,
        validation_report_uri="s3://b/val.json",
    )


def _profile(scene, revision=None):
    return SceneProfileRunRecord(
        run_id="profile-001",
        status=RunStatus.SUCCEEDED,
        scene_id=scene.scene_id,
        manifest_artifact_id=revision[0] if revision else scene.manifest_artifact_id,
        manifest_checksum=revision[1] if revision else scene.manifest_checksum,
        observation_count=5,
        keyframe_count=2,
        annotation_count=2,
        observed_channels=["CAM_FRONT", "LIDAR_TOP"],
    )


def test_ready_scene_quality():
    scene = _scene()
    quality = build_scene_quality(scene, _validation(scene), _profile(scene))

    assert quality.readiness == SceneReadiness.READY
    assert quality.manifest_artifact_id == scene.manifest_artifact_id
    assert quality.counts.model_dump() == {
        "keyframe_count": 2,
        "observation_count": 5,
    }
    assert quality.validation.checked_observation_count == 5
    assert quality.validation.manifest_artifact_id == scene.manifest_artifact_id
    assert quality.profile.observed_channels == ["CAM_FRONT", "LIDAR_TOP"]
    assert "status" not in quality.model_dump()


def test_quality_carries_no_ground_truth_or_selectability():
    scene = _scene(annotations_per_keyframe=0)
    dumped = build_scene_quality(scene, _validation(scene)).model_dump()
    assert "ground_truth" not in dumped
    assert "selectable_for_detection" not in dumped
    assert "exclusion_reasons" not in dumped


def test_missing_validation_is_unknown():
    scene = _scene()
    quality = build_scene_quality(scene)
    assert quality.readiness == SceneReadiness.UNKNOWN


def test_blocking_and_warning_validation():
    scene = _scene()
    blocked = build_scene_quality(
        scene, _validation(scene, status="failed", block=True)
    )
    assert blocked.readiness == SceneReadiness.BLOCKED

    warning = build_scene_quality(scene, _validation(scene, status="warning"))
    assert warning.readiness == SceneReadiness.WARNING


def test_runs_for_a_previous_revision_are_ignored():
    scene = _scene(revision="art-rev-2")
    stale = ("art-rev-1", scene.manifest_checksum)
    quality = build_scene_quality(
        scene,
        _validation(scene, status="ready", revision=stale),
        _profile(scene, stale),
    )
    assert quality.readiness == SceneReadiness.UNKNOWN
    assert quality.validation is None
    assert quality.profile is None
    assert compute_scene_readiness(scene, _validation(scene, revision=stale)) == (
        SceneReadiness.UNKNOWN
    )


def test_unfinished_run_does_not_count():
    scene = _scene()
    assert (
        compute_scene_readiness(scene, _validation(scene, run_status=RunStatus.RUNNING))
        == SceneReadiness.UNKNOWN
    )
