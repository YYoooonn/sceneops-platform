"""Unit tests for CenterDistanceDetectionEvaluator scene-level behavior.

Covers:
- GT scene with samples that have zero annotations is evaluated (valid negative)
- Prediction shard for non-GT scene is skipped and recorded in skipped_shards
- skipped_scene_ids is populated in evaluation manifest metadata
- missing_gt_policy=fail raises when a non-GT shard is encountered
- shard.scene_id / payload scene_id mismatch produces a warning
"""

from __future__ import annotations

from typing import Any
from unittest.mock import AsyncMock, MagicMock

import pytest

from sceneops_worker.evaluation.detection.center_distance import (
    evaluate_center_distance_detection,
)
from sceneops_worker.evaluation.detection.base import DetectionEvaluationRequest
from sceneops_core.inference.schemas.manifests import (
    DetectionPredictionManifest,
    DetectionPredictionShardRef,
)
from sceneops_core.scenes.schemas.manifests import SceneSampleManifest
from sceneops_worker.evaluation.detection.loading import EvaluationSceneEntry


# ── builders ──────────────────────────────────────────────────────────────────


def _sample_manifest(
    scene_id: str,
    sample_id: str,
    annotations: list[Any] | None = None,
) -> SceneSampleManifest:
    m = MagicMock(spec=SceneSampleManifest)
    m.scene_id = scene_id
    m.sample_id = sample_id
    m.annotations = annotations or []
    m.sensor_frames = []
    return m


def _scene_entry(
    scene_id: str,
    has_ground_truth: bool = True,
    annotation_count: int = 5,
    sample_count: int = 2,
) -> EvaluationSceneEntry:
    # Plain MagicMock (no spec) so build_scene_index can read any attribute.
    scene_manifest = MagicMock()
    scene_manifest.scene_id = scene_id
    scene_manifest.annotation_count = annotation_count if has_ground_truth else 0
    scene_manifest.sample_count = sample_count
    scene_manifest.frame_count = sample_count * 2
    scene_manifest.has_ground_truth = has_ground_truth
    scene_manifest.ground_truth_source = "nuscenes" if has_ground_truth else None
    scene_manifest.samples = []
    return EvaluationSceneEntry(
        scene_id=scene_id,
        scene_manifest_uri=f"file:///{scene_id}/manifest.json",
        manifest=scene_manifest,
        sample_count=sample_count,
        frame_count=sample_count * 2,
        annotation_count=annotation_count if has_ground_truth else 0,
        has_ground_truth=has_ground_truth,
        ground_truth_source="nuscenes" if has_ground_truth else None,
    )


def _prediction_manifest(
    shards: list[DetectionPredictionShardRef],
) -> DetectionPredictionManifest:
    return DetectionPredictionManifest(
        inference_run_id="infer-001",
        dataset_id="nuscenes",
        dataset_version="v1.0-mini",
        model_id="dummy",
        model_version="v1",
        prediction_shards=shards,
    )


def _shard(
    scene_id: str, sample_id: str, uri: str, prediction_count: int = 3
) -> DetectionPredictionShardRef:
    return DetectionPredictionShardRef(
        scene_id=scene_id,
        sample_id=sample_id,
        uri=uri,
        prediction_count=prediction_count,
    )


def _sample_payload(
    scene_id: str, sample_id: str, predictions: list[dict] | None = None
) -> dict:
    return {
        "dataset_id": "nuscenes",
        "dataset_version": "v1.0-mini",
        "scene_id": scene_id,
        "sample_id": sample_id,
        "predictions": predictions or [],
    }


def _build_request(
    shards: list[DetectionPredictionShardRef],
    scene_entries: list[EvaluationSceneEntry],
    sample_payloads: dict[str, dict],
    missing_gt_policy: str = "skip",
) -> DetectionEvaluationRequest:
    """Build a minimal DetectionEvaluationRequest with mocked artifact stores."""

    prediction_manifest = _prediction_manifest(shards)

    # Dataset manifest with one scene per entry
    dataset_manifest = MagicMock()
    dataset_manifest.dataset_id = "nuscenes"
    dataset_manifest.dataset_version = "v1.0-mini"
    dataset_manifest.scenes = [
        MagicMock(
            scene_id=e.scene_id,
            scene_manifest_uri=e.scene_manifest_uri,
        )
        for e in scene_entries
    ]

    # Build a real EvaluationSceneIndex
    samples_by_id: dict[str, SceneSampleManifest] = {}
    scene_by_sample_id: dict[str, EvaluationSceneEntry] = {}
    for entry in scene_entries:
        for payload in sample_payloads.values():
            if payload.get("scene_id") == entry.scene_id:
                sid = payload["sample_id"]
                sample = _sample_manifest(entry.scene_id, sid)
                samples_by_id[sid] = sample
                scene_by_sample_id[sid] = entry

    # scene_index = EvaluationSceneIndex(
    #     scenes=scene_entries,
    #     scenes_by_id={e.scene_id: e for e in scene_entries},
    #     samples_by_id=samples_by_id,
    #     scene_by_sample_id=scene_by_sample_id,
    #     scene_count=len(scene_entries),
    #     sample_count=sum(e.sample_count for e in scene_entries),
    #     frame_count=sum(e.frame_count for e in scene_entries),
    #     annotation_count=sum(e.annotation_count for e in scene_entries),
    #     ground_truth_scene_count=sum(1 for e in scene_entries if e.has_ground_truth),
    # )

    # Scene artifact store returns pre-built index entries
    scene_store = MagicMock()

    async def load_scene_manifest(uri: str) -> MagicMock | None:
        for entry in scene_entries:
            if entry.scene_manifest_uri == uri:
                m = entry.manifest
                # Ensure samples list reflects the payloads for build_scene_index.
                m.samples = [
                    _sample_manifest(entry.scene_id, payload["sample_id"])
                    for payload in sample_payloads.values()
                    if payload.get("scene_id") == entry.scene_id
                ]
                return m
        return None

    scene_store.load_scene_manifest = load_scene_manifest

    # Run artifact store: load_inference_prediction_manifest returns our manifest,
    # load_sample_prediction_manifest returns payloads keyed by URI,
    # write_* are no-ops.
    run_store = MagicMock()
    run_store.load_inference_prediction_manifest = AsyncMock(
        return_value=prediction_manifest
    )

    async def load_sample(uri: str) -> dict:
        return sample_payloads[uri]

    run_store.load_sample_prediction_manifest = load_sample
    run_store.write_evaluation_run_manifest = AsyncMock(return_value=None)
    run_store.write_sample_evaluation_manifest = AsyncMock(return_value=None)
    run_store.evaluation_run_manifest_uri = MagicMock(
        return_value="file:///eval/evaluation.json"
    )
    run_store.evaluation_run_metrics_uri = MagicMock(
        return_value="file:///eval/metrics.json"
    )
    run_store.evaluation_samples_root_uri = MagicMock(
        return_value="file:///eval/samples/"
    )

    return DetectionEvaluationRequest(
        dataset_manifest=dataset_manifest,
        scene_artifact_store=scene_store,
        run_artifact_store=run_store,
        inference_run_id="infer-001",
        evaluation_run_id="eval-001",
        match_distance_m=2.0,
        missing_gt_policy=missing_gt_policy,
    )


# ── GT scene with zero-annotation sample is evaluated, not skipped ────────────


async def test_zero_annotation_sample_in_gt_scene_is_evaluated():
    """A sample inside a GT-bearing scene is evaluated even with 0 annotations.
    It contributes a valid negative (FP count increases, GT pool stays at 0 for that sample).
    """
    gt_entry = _scene_entry("scene-gt", has_ground_truth=True, annotation_count=5)
    payload = _sample_payload(
        "scene-gt", "sample-001", predictions=[]
    )  # no annotations, no predictions
    shard = _shard(
        "scene-gt", "sample-001", "file:///preds/sample-001.json", prediction_count=0
    )

    request = _build_request(
        shards=[shard],
        scene_entries=[gt_entry],
        sample_payloads={"file:///preds/sample-001.json": payload},
    )

    manifest = await evaluate_center_distance_detection(request)

    # Should evaluate, not skip
    assert manifest.status == "succeeded"
    assert manifest.sample_count == 1


# ── non-GT scene shard is skipped ─────────────────────────────────────────────


async def test_non_gt_scene_shard_skipped_and_recorded():
    """Prediction shard belonging to a scene without GT is skipped and added to
    skipped_shards. The scene appears in skipped_scene_ids.
    """
    no_gt_entry = _scene_entry(
        "scene-no-gt", has_ground_truth=False, annotation_count=0
    )
    gt_entry = _scene_entry("scene-gt", has_ground_truth=True, annotation_count=5)
    payload_gt = _sample_payload("scene-gt", "sample-gt", predictions=[])
    payload_no_gt = _sample_payload("scene-no-gt", "sample-no-gt", predictions=[])

    shard_gt = _shard("scene-gt", "sample-gt", "file:///preds/sample-gt.json")
    shard_no_gt = _shard(
        "scene-no-gt",
        "sample-no-gt",
        "file:///preds/sample-no-gt.json",
        prediction_count=3,
    )

    request = _build_request(
        shards=[shard_gt, shard_no_gt],
        scene_entries=[gt_entry, no_gt_entry],
        sample_payloads={
            "file:///preds/sample-gt.json": payload_gt,
            "file:///preds/sample-no-gt.json": payload_no_gt,
        },
    )

    manifest = await evaluate_center_distance_detection(request)

    skipped_shards = manifest.metadata.get("skipped_shards", [])
    skipped_scene_ids = manifest.metadata.get("skipped_scene_ids", [])

    assert any(s["scene_id"] == "scene-no-gt" for s in skipped_shards)
    assert "scene-no-gt" in skipped_scene_ids
    assert "scene-gt" not in skipped_scene_ids


async def test_non_gt_scene_shard_skipped_reason():
    # Must include a GT entry so annotation_count > 0 and the shard loop executes.
    gt_entry = _scene_entry("scene-gt", has_ground_truth=True, annotation_count=5)
    no_gt_entry = _scene_entry("scene-no-gt", has_ground_truth=False)
    payload_gt = _sample_payload("scene-gt", "sample-gt")
    shard_gt = _shard("scene-gt", "sample-gt", "file:///preds/sample-gt.json")
    shard_no_gt = _shard("scene-no-gt", "sample-no-gt", "file:///preds/s.json")

    request = _build_request(
        shards=[shard_gt, shard_no_gt],
        scene_entries=[gt_entry, no_gt_entry],
        sample_payloads={
            "file:///preds/sample-gt.json": payload_gt,
            "file:///preds/s.json": _sample_payload("scene-no-gt", "sample-no-gt"),
        },
    )

    manifest = await evaluate_center_distance_detection(request)

    skipped = manifest.metadata.get("skipped_shards", [])
    no_gt_skipped = [s for s in skipped if s["scene_id"] == "scene-no-gt"]
    assert len(no_gt_skipped) == 1
    assert no_gt_skipped[0]["reason"] == "scene_has_no_ground_truth"
    assert no_gt_skipped[0]["scene_id"] == "scene-no-gt"


# ── missing_gt_policy=fail raises on non-GT shard ────────────────────────────


async def test_fail_policy_raises_on_non_gt_shard():
    # Include a GT entry so annotation_count > 0 and the shard loop runs.
    gt_entry = _scene_entry("scene-gt", has_ground_truth=True, annotation_count=5)
    no_gt_entry = _scene_entry("scene-no-gt", has_ground_truth=False)
    shard_gt = _shard("scene-gt", "sample-gt", "file:///preds/sample-gt.json")
    shard_no_gt = _shard("scene-no-gt", "sample-no-gt", "file:///preds/s.json")

    request = _build_request(
        shards=[shard_gt, shard_no_gt],
        scene_entries=[gt_entry, no_gt_entry],
        sample_payloads={
            "file:///preds/sample-gt.json": _sample_payload("scene-gt", "sample-gt"),
            "file:///preds/s.json": _sample_payload("scene-no-gt", "sample-no-gt"),
        },
        missing_gt_policy="fail",
    )

    with pytest.raises(ValueError, match="scene_id='scene-no-gt'"):
        await evaluate_center_distance_detection(request)


# ── shard scene_id / payload scene_id mismatch → warning ─────────────────────


async def test_shard_scene_id_mismatch_with_payload_produces_warning():
    """When shard.scene_id and the sample payload's resolved scene differ, a warning
    is emitted and evaluation continues using the payload's scene.
    """
    entry_a = _scene_entry("scene-a", has_ground_truth=True, annotation_count=10)
    entry_b = _scene_entry("scene-b", has_ground_truth=True, annotation_count=5)

    # shard says scene-a but payload belongs to scene-b (different sample_id)
    # We simulate this by having the shard report scene_id=scene-a but the
    # payload sample_id maps to scene-b in the scene index.
    payload = _sample_payload("scene-b", "sample-b", predictions=[])

    shard = DetectionPredictionShardRef(
        scene_id="scene-a",  # wrong
        sample_id="sample-b",
        uri="file:///preds/sample-b.json",
        prediction_count=0,
    )

    request = _build_request(
        shards=[shard],
        scene_entries=[entry_a, entry_b],
        sample_payloads={"file:///preds/sample-b.json": payload},
    )

    manifest = await evaluate_center_distance_detection(request)

    warnings = manifest.metadata.get("warnings", [])
    mismatch_warnings = [
        w for w in warnings if w.get("type") == "shard_scene_id_mismatch"
    ]
    assert len(mismatch_warnings) == 1
    assert mismatch_warnings[0]["shard_scene_id"] == "scene-a"
    assert mismatch_warnings[0]["payload_scene_id"] == "scene-b"


# ── evaluated_scene_ids and skipped_scene_ids are populated ───────────────────


async def test_evaluated_scene_ids_populated_in_metadata():
    gt_entry = _scene_entry("scene-gt", has_ground_truth=True, annotation_count=5)
    payload = _sample_payload("scene-gt", "sample-001", predictions=[])
    shard = _shard("scene-gt", "sample-001", "file:///preds/sample-001.json")

    request = _build_request(
        shards=[shard],
        scene_entries=[gt_entry],
        sample_payloads={"file:///preds/sample-001.json": payload},
    )

    manifest = await evaluate_center_distance_detection(request)

    assert "scene-gt" in manifest.metadata.get("evaluated_scene_ids", [])
    assert manifest.metadata.get("skipped_scene_ids") == []
