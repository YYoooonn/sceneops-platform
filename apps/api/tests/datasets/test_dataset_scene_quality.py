"""Unit tests for dataset scene quality aggregate builder.

Tests the pure aggregate function in app.domains.datasets.quality — no DB,
no async, no FastAPI.

Covers:
- aggregate counts readiness buckets correctly
- total keyframe/observation counts are summed
- the aggregate carries no ground-truth or selectability notion (labels are
  derived label-set concerns, ADR-007 §33)
- observed channels union is deterministic (sorted)
- summary is global, scenes list is paginated
- empty dataset version returns zero summary
"""

from __future__ import annotations

from sceneops_core.runs.schemas import RunStatus
from sceneops_core.scenes.schemas.records import SceneRecord
from sceneops_core.scenes.schemas.runs import (
    SceneProfileRunRecord,
    SceneValidationRunRecord,
)

from app.domains.datasets.quality import build_dataset_scene_quality_aggregate
from app.domains.scenes.quality import build_scene_quality

_CHECKSUM = "sha256:" + "1" * 64


# ── helpers ───────────────────────────────────────────────────────────────────


def _scene(
    scene_id: str = "scene-001",
    keyframe_count: int = 40,
    observation_count: int = 80,
    annotation_count: int = 0,
) -> SceneRecord:
    return SceneRecord(
        scene_id=scene_id,
        dataset_id="nuscenes",
        dataset_version="v1.0-mini",
        robot_run_id="run-0",
        unit_key=scene_id,
        window_clock="mcap_log_time",
        window_start_timestamp_ns=0,
        window_end_timestamp_ns=20_000_000_000,
        producer_fingerprint="sha256:" + "a" * 64,
        manifest_artifact_id=f"art-{scene_id}",
        manifest_checksum=_CHECKSUM,
        observation_count=observation_count,
        keyframe_count=keyframe_count,
        annotation_count=annotation_count,
    )


def _validation_run(
    scene_id: str = "scene-001",
    validation_status: str = "ready",
    should_block_pipeline: bool = False,
) -> SceneValidationRunRecord:
    return SceneValidationRunRecord(
        run_id=f"val-{scene_id}",
        status=RunStatus.SUCCEEDED,
        scene_id=scene_id,
        manifest_artifact_id=f"art-{scene_id}",
        manifest_checksum=_CHECKSUM,
        validation_status=validation_status,
        should_block_pipeline=should_block_pipeline,
        error_count=0,
        warning_count=0,
        issue_count=0,
    )


def _profile_run(
    scene_id: str = "scene-001",
    observed_channels: list[str] | None = None,
) -> SceneProfileRunRecord:
    return SceneProfileRunRecord(
        run_id=f"prof-{scene_id}",
        status=RunStatus.SUCCEEDED,
        scene_id=scene_id,
        manifest_artifact_id=f"art-{scene_id}",
        manifest_checksum=_CHECKSUM,
        keyframe_count=40,
        observation_count=80,
        observed_channels=observed_channels or ["CAM_FRONT", "LIDAR_TOP"],
    )


def _quality(
    scene: SceneRecord,
    validation_run: SceneValidationRunRecord | None = None,
    profile_run: SceneProfileRunRecord | None = None,
):
    return build_scene_quality(
        scene=scene, validation_run=validation_run, profile_run=profile_run
    )


# ── empty dataset ─────────────────────────────────────────────────────────────


def test_empty_dataset_returns_zero_summary():
    summary = build_dataset_scene_quality_aggregate([])
    assert summary.scene_count == 0
    assert summary.ready_scene_count == 0
    assert summary.warning_scene_count == 0
    assert summary.blocked_scene_count == 0
    assert summary.unknown_scene_count == 0
    assert summary.total_keyframe_count == 0
    assert summary.total_observation_count == 0
    assert summary.observed_channels == []


# ── readiness buckets ─────────────────────────────────────────────────────────


def test_readiness_buckets_are_counted_correctly():
    scenes = [
        _quality(_scene("s1"), _validation_run("s1", "ready")),  # ready
        _quality(_scene("s2"), _validation_run("s2", "warning")),  # warning
        _quality(
            _scene("s3"), _validation_run("s3", should_block_pipeline=True)
        ),  # blocked
        _quality(_scene("s4"), validation_run=None),  # unknown
    ]
    summary = build_dataset_scene_quality_aggregate(scenes)
    assert summary.scene_count == 4
    assert summary.ready_scene_count == 1
    assert summary.warning_scene_count == 1
    assert summary.blocked_scene_count == 1
    assert summary.unknown_scene_count == 1


def test_all_ready_scenes():
    scenes = [
        _quality(_scene(f"s{i}"), _validation_run(f"s{i}", "ready")) for i in range(5)
    ]
    summary = build_dataset_scene_quality_aggregate(scenes)
    assert summary.ready_scene_count == 5
    assert summary.warning_scene_count == 0
    assert summary.blocked_scene_count == 0
    assert summary.unknown_scene_count == 0


# ── totals ────────────────────────────────────────────────────────────────────


def test_total_keyframe_and_observation_counts_are_summed():
    q1 = _quality(_scene("s1", keyframe_count=40, observation_count=80))
    q2 = _quality(_scene("s2", keyframe_count=30, observation_count=60))
    summary = build_dataset_scene_quality_aggregate([q1, q2])
    assert summary.total_keyframe_count == 70
    assert summary.total_observation_count == 140


def test_aggregate_has_no_ground_truth_or_selectability_fields():
    dumped = build_dataset_scene_quality_aggregate([]).model_dump()
    assert not [k for k in dumped if "ground_truth" in k or "selectable" in k]
    assert "exclusion_reason_counts" not in dumped


# ── observed channels ─────────────────────────────────────────────────────────


def test_observed_channels_union_is_sorted():
    q1 = _quality(
        _scene("s1"),
        _validation_run("s1"),
        _profile_run("s1", observed_channels=["LIDAR_TOP", "CAM_FRONT"]),
    )
    q2 = _quality(
        _scene("s2"),
        _validation_run("s2"),
        _profile_run("s2", observed_channels=["CAM_BACK", "CAM_FRONT"]),
    )
    summary = build_dataset_scene_quality_aggregate([q1, q2])
    assert summary.observed_channels == sorted(["CAM_BACK", "CAM_FRONT", "LIDAR_TOP"])


def test_observed_channels_empty_when_no_profile_runs():
    q = _quality(_scene("s1"), _validation_run("s1"), profile_run=None)
    summary = build_dataset_scene_quality_aggregate([q])
    assert summary.observed_channels == []


def test_observed_channels_deterministic_across_calls():
    scenes = [
        _quality(
            _scene(f"s{i}"),
            _validation_run(f"s{i}"),
            _profile_run(
                f"s{i}", observed_channels=["CAM_FRONT", "LIDAR_TOP", "CAM_BACK"]
            ),
        )
        for i in range(3)
    ]
    a = build_dataset_scene_quality_aggregate(scenes)
    b = build_dataset_scene_quality_aggregate(scenes)
    assert a.observed_channels == b.observed_channels


# ── summary is global, not page-scoped ───────────────────────────────────────


def test_summary_counts_all_scenes_not_just_page():
    all_quality = [
        _quality(
            _scene(f"s{i}"),
            _validation_run(f"s{i}", "ready"),
        )
        for i in range(10)
    ]
    # Summary built over all 10
    summary = build_dataset_scene_quality_aggregate(all_quality)
    assert summary.scene_count == 10
    assert summary.ready_scene_count == 10

    # Pagination is separate — first page of 3
    page = all_quality[:3]
    assert len(page) == 3

    # Summary still covers all 10, page covers 3
    page_summary = build_dataset_scene_quality_aggregate(page)
    assert page_summary.scene_count == 3  # page-scope summary would differ
    # Full summary is invariant to pagination
    assert summary.scene_count == 10
