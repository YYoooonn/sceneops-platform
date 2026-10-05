"""Evaluation artifact writers: the manifest records the exact pins it
consumed and always names the artifacts it wrote."""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock

from sceneops_core.evaluations.schemas import EvaluationInputs
from sceneops_core.inference.schemas import (
    DetectionPredictionManifest,
    PredictionRevisionRef,
)
from sceneops_core.labels import LabelSetRef
from sceneops_worker.evaluation.detection.accumulation import EvaluationAccumulator
from sceneops_worker.evaluation.detection.artifacts import (
    write_final_evaluation_manifest,
    write_skipped_evaluation_manifest,
)
from sceneops_worker.evaluation.detection.base import DetectionEvaluationRequest
from tests.derived.labels_support import label_document

EVAL_MANIFEST_URI = "file:///runs/evaluations/eval-001/evaluation.json"
METRICS_URI = "file:///runs/evaluations/eval-001/metrics.json"
SAMPLES_ROOT_URI = "file:///runs/evaluations/eval-001/samples/"
CHECKSUM = "sha256:" + "a" * 64


def _run_store() -> MagicMock:
    store = MagicMock()
    store.evaluation_run_manifest_uri = MagicMock(return_value=EVAL_MANIFEST_URI)
    store.evaluation_run_metrics_uri = MagicMock(return_value=METRICS_URI)
    store.evaluation_samples_root_uri = MagicMock(return_value=SAMPLES_ROOT_URI)
    store.write_evaluation_run_manifest = AsyncMock(return_value=EVAL_MANIFEST_URI)
    return store


def _request() -> DetectionEvaluationRequest:
    prediction = DetectionPredictionManifest(
        inference_run_id="infer-001",
        dataset_id="ds",
        dataset_version="v1",
        config={"model_id": "dummy", "model_version": "v1"},
        inputs=[],
        scene_count=0,
        sample_count=0,
        prediction_count=0,
        evaluable_prediction_count=0,
        lifting_succeeded_count=0,
        lifting_failed_count=0,
        lifting_not_applicable_count=0,
        prediction_shards=[],
    )
    return DetectionEvaluationRequest(
        evaluation_run_id="eval-001",
        inference_run_id="infer-001",
        dataset_id="ds",
        dataset_version="v1",
        inputs=EvaluationInputs(
            prediction=PredictionRevisionRef(
                inference_run_id="infer-001",
                manifest_artifact_id="predmanifest-1",
                manifest_checksum=CHECKSUM,
            ),
            label_set=LabelSetRef(
                label_set_id="gt",
                manifest_artifact_id="labelset-1",
                manifest_checksum=CHECKSUM,
            ),
        ),
        prediction=prediction,
        label_set=label_document("gt", covered=[1]),
        views={},
        run_artifact_store=_run_store(),
        match_distance_m=2.0,
    )


async def test_skipped_manifest_records_inputs_reason_and_location():
    request = _request()
    manifest = await write_skipped_evaluation_manifest(
        request=request, reason="no covered samples", metadata={"x": 1}
    )
    assert manifest.status == "skipped"
    assert manifest.evaluation_manifest_uri == EVAL_MANIFEST_URI
    assert manifest.inputs == request.inputs
    assert manifest.metadata == {"x": 1, "reason": "no covered samples"}
    assert (manifest.model_id, manifest.model_version) == ("dummy", "v1")
    request.run_artifact_store.write_evaluation_run_manifest.assert_awaited_once()


async def test_final_manifest_records_inputs_locations_and_metrics():
    request = _request()
    accumulator = EvaluationAccumulator()
    accumulator.add(
        {
            "tp": 3,
            "fp": 1,
            "fn": 1,
            "total_center_distance_error": 1.5,
            "matched_count": 3,
            "prediction_count": 5,
            "class_metrics": {"vehicle.car": {"tp": 3, "fp": 1, "fn": 1}},
            "not_localized_prediction_count": 1,
        }
    )
    manifest = await write_final_evaluation_manifest(
        request=request, accumulator=accumulator, evaluated_sample_count=2
    )
    assert manifest.status == "succeeded"
    assert manifest.inputs == request.inputs
    assert (manifest.evaluation_manifest_uri, manifest.metrics_uri) == (
        EVAL_MANIFEST_URI,
        METRICS_URI,
    )
    assert manifest.samples_root_uri == SAMPLES_ROOT_URI
    assert manifest.evaluation_unit == "label"
    assert manifest.ground_truth_count == 4
    assert manifest.metrics["not_localized_prediction_count"] == 1
    assert manifest.primary_metric_name == "precision"
    assert manifest.primary_metric_value == 0.75
