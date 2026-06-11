"""Tests for evaluation artifact writers.

Covers:
- write_skipped_evaluation_manifest sets evaluation_manifest_uri (non-None)
- write_final_evaluation_manifest sets evaluation_manifest_uri (non-None)
- "no ground truth" evaluation path returns manifest with non-None evaluation_manifest_uri
- "no evaluable shards" evaluation path returns manifest with non-None evaluation_manifest_uri
- skipped manifests still write to the artifact store
"""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock

from sceneops_core.evaluations.schemas.manifests import DetectionEvaluationManifest
from sceneops_core.inference.schemas.manifests import DetectionPredictionManifest
from sceneops_worker.evaluation.detection.artifacts import (
    write_final_evaluation_manifest,
    write_skipped_evaluation_manifest,
)
from sceneops_worker.evaluation.detection.accumulation import EvaluationAccumulator
from sceneops_worker.evaluation.detection.base import DetectionEvaluationRequest
from sceneops_worker.evaluation.detection.center_distance import (
    evaluate_center_distance_detection,
)
from sceneops_worker.evaluation.detection.loading import (
    EvaluationSceneEntry,
)


EVAL_MANIFEST_URI = "file:///runs/evaluations/eval-001/evaluation.json"
METRICS_URI = "file:///runs/evaluations/eval-001/metrics.json"
SAMPLES_ROOT_URI = "file:///runs/evaluations/eval-001/samples/"


def _run_store() -> MagicMock:
    store = MagicMock()
    store.evaluation_run_manifest_uri = MagicMock(return_value=EVAL_MANIFEST_URI)
    store.evaluation_run_metrics_uri = MagicMock(return_value=METRICS_URI)
    store.evaluation_samples_root_uri = MagicMock(return_value=SAMPLES_ROOT_URI)
    store.write_evaluation_run_manifest = AsyncMock(return_value=EVAL_MANIFEST_URI)
    store.write_sample_evaluation_manifest = AsyncMock(return_value=None)
    return store


def _prediction_manifest(prediction_count: int = 0) -> DetectionPredictionManifest:
    return DetectionPredictionManifest(
        inference_run_id="infer-001",
        dataset_id="nuscenes",
        dataset_version="v1.0-mini",
        model_id="dummy",
        model_version="v1",
        prediction_shards=[],
    )


def _skipped_request() -> DetectionEvaluationRequest:
    dataset_manifest = MagicMock()
    dataset_manifest.dataset_id = "nuscenes"
    dataset_manifest.dataset_version = "v1.0-mini"
    dataset_manifest.scenes = []
    return DetectionEvaluationRequest(
        dataset_manifest=dataset_manifest,
        scene_artifact_store=MagicMock(),
        run_artifact_store=_run_store(),
        inference_run_id="infer-001",
        evaluation_run_id="eval-001",
        match_distance_m=2.0,
    )


# ── write_skipped_evaluation_manifest ────────────────────────────────────────


async def test_skipped_manifest_has_non_null_evaluation_manifest_uri():
    """write_skipped_evaluation_manifest must set evaluation_manifest_uri."""
    request = _skipped_request()
    manifest = await write_skipped_evaluation_manifest(
        request=request,
        prediction_manifest=_prediction_manifest(),
        reason="no ground truth",
    )
    assert isinstance(manifest, DetectionEvaluationManifest)
    assert manifest.evaluation_manifest_uri is not None
    assert manifest.evaluation_manifest_uri == EVAL_MANIFEST_URI


async def test_skipped_manifest_status_is_skipped():
    request = _skipped_request()
    manifest = await write_skipped_evaluation_manifest(
        request=request,
        prediction_manifest=_prediction_manifest(),
        reason="test skip",
    )
    assert manifest.status == "skipped"


async def test_skipped_manifest_reason_in_metadata():
    request = _skipped_request()
    manifest = await write_skipped_evaluation_manifest(
        request=request,
        prediction_manifest=_prediction_manifest(),
        reason="no_gt",
        metadata={"extra": "value"},
    )
    assert manifest.metadata.get("reason") == "no_gt"
    assert manifest.metadata.get("extra") == "value"


async def test_skipped_manifest_writes_to_artifact_store():
    request = _skipped_request()
    await write_skipped_evaluation_manifest(
        request=request,
        prediction_manifest=_prediction_manifest(),
        reason="no ground truth",
    )
    request.run_artifact_store.write_evaluation_run_manifest.assert_called_once()


# ── write_final_evaluation_manifest ──────────────────────────────────────────


async def test_final_manifest_has_non_null_evaluation_manifest_uri():
    """write_final_evaluation_manifest must set evaluation_manifest_uri."""
    request = _skipped_request()
    accumulator = EvaluationAccumulator()
    manifest = await write_final_evaluation_manifest(
        request=request,
        prediction_manifest=_prediction_manifest(),
        accumulator=accumulator,
        evaluated_sample_count=1,
    )
    assert manifest.evaluation_manifest_uri is not None
    assert manifest.evaluation_manifest_uri == EVAL_MANIFEST_URI


async def test_final_manifest_has_non_null_metrics_uri():
    request = _skipped_request()
    accumulator = EvaluationAccumulator()
    manifest = await write_final_evaluation_manifest(
        request=request,
        prediction_manifest=_prediction_manifest(),
        accumulator=accumulator,
        evaluated_sample_count=1,
    )
    assert manifest.metrics_uri is not None
    assert manifest.metrics_uri == METRICS_URI


# ── full skipped evaluation paths ─────────────────────────────────────────────


def _no_gt_request() -> DetectionEvaluationRequest:
    """Request where all scenes have zero annotation_count → triggers skipped path."""
    dataset_manifest = MagicMock()
    dataset_manifest.dataset_id = "nuscenes"
    dataset_manifest.dataset_version = "v1.0-mini"

    no_gt_entry = EvaluationSceneEntry(
        scene_id="scene-001",
        scene_manifest_uri="file:///scenes/scene-001/manifest.json",
        manifest=MagicMock(
            scene_id="scene-001",
            annotation_count=0,
            sample_count=2,
            frame_count=4,
            has_ground_truth=False,
            ground_truth_source=None,
            samples=[],
        ),
        sample_count=2,
        frame_count=4,
        annotation_count=0,
        has_ground_truth=False,
        ground_truth_source=None,
    )
    dataset_manifest.scenes = [
        MagicMock(
            scene_id="scene-001",
            scene_manifest_uri="file:///scenes/scene-001/manifest.json",
        )
    ]

    run_store = _run_store()
    run_store.load_inference_prediction_manifest = AsyncMock(
        return_value=DetectionPredictionManifest(
            inference_run_id="infer-001",
            dataset_id="nuscenes",
            dataset_version="v1.0-mini",
            model_id="dummy",
            model_version="v1",
            prediction_shards=[],
        )
    )

    scene_store = MagicMock()
    scene_store.load_scene_manifest = AsyncMock(
        return_value=no_gt_entry.manifest,
    )

    return DetectionEvaluationRequest(
        dataset_manifest=dataset_manifest,
        scene_artifact_store=scene_store,
        run_artifact_store=run_store,
        inference_run_id="infer-001",
        evaluation_run_id="eval-001",
        match_distance_m=2.0,
    )


async def test_no_gt_dataset_skipped_manifest_has_non_null_evaluation_manifest_uri():
    """When the whole dataset has no GT, the returned manifest has evaluation_manifest_uri set."""
    request = _no_gt_request()
    manifest = await evaluate_center_distance_detection(request)
    assert manifest.status == "skipped"
    assert manifest.evaluation_manifest_uri is not None, (
        "evaluation_manifest_uri must not be None — "
        "this would cause ArtifactRef(uri=None) in evaluate_detection"
    )
