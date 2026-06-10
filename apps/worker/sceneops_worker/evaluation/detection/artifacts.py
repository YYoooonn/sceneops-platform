"""Artifact writers for detection evaluation runs.

These functions are evaluator-algorithm-agnostic. Any evaluator that produces
an EvaluationAccumulator and a DetectionPredictionManifest can use them.
"""

from __future__ import annotations

from typing import Any

from sceneops_core.common.time import utc_now
from sceneops_core.evaluations.schemas.manifests import DetectionEvaluationManifest
from sceneops_core.inference.schemas.manifests import DetectionPredictionManifest
from sceneops_worker.evaluation.detection.accumulation import EvaluationAccumulator
from sceneops_worker.evaluation.detection.base import DetectionEvaluationRequest
from sceneops_worker.runs import RunArtifactStore


async def write_sample_evaluation(
    *,
    run_artifact_store: RunArtifactStore,
    evaluation_run_id: str,
    sample_id: str,
    sample_eval: dict[str, Any],
) -> None:
    """Persist one sample's evaluation result to the artifact store."""
    await run_artifact_store.write_sample_evaluation_manifest(
        evaluation_run_id=evaluation_run_id,
        sample_id=sample_id,
        manifest=sample_eval,
    )


async def write_final_evaluation_manifest(
    *,
    request: DetectionEvaluationRequest,
    prediction_manifest: DetectionPredictionManifest,
    accumulator: EvaluationAccumulator,
    evaluated_sample_count: int,
    evaluation_unit: str = "annotation",
) -> DetectionEvaluationManifest:
    """Assemble and persist the run-level DetectionEvaluationManifest.

    Writes to:
      runs/evaluations/{evaluation_run_id}/evaluation.json
    """
    metrics = accumulator.build_metrics()
    primary_metric_value = metrics.get("precision")
    primary_metric_name = "precision" if primary_metric_value is not None else None

    evaluation_manifest_uri = request.run_artifact_store.evaluation_run_manifest_uri(
        request.evaluation_run_id
    )
    metrics_uri = request.run_artifact_store.evaluation_run_metrics_uri(
        request.evaluation_run_id
    )
    samples_root_uri = request.run_artifact_store.evaluation_samples_root_uri(
        request.evaluation_run_id
    )

    evaluation_manifest = DetectionEvaluationManifest(
        evaluation_run_id=request.evaluation_run_id,
        inference_run_id=request.inference_run_id,
        dataset_id=request.dataset_manifest.dataset_id,
        dataset_version=request.dataset_manifest.dataset_version,
        model_id=prediction_manifest.model_id,
        model_version=prediction_manifest.model_version,
        status="succeeded",
        match_distance_m=request.match_distance_m,
        sample_count=evaluated_sample_count,
        prediction_count=accumulator.raw_prediction_count,
        evaluable_prediction_count=accumulator.evaluable_prediction_count,
        lifting_failed_prediction_count=accumulator.lifting_failed_prediction_count,
        ground_truth_count=accumulator.ground_truth_count,
        evaluation_unit=evaluation_unit,
        primary_metric_name=primary_metric_name,
        primary_metric_value=primary_metric_value,
        evaluation_manifest_uri=evaluation_manifest_uri,
        metrics_uri=metrics_uri,
        samples_root_uri=samples_root_uri,
        metrics=metrics,
        class_metrics=accumulator.build_class_metrics(),
        created_at=utc_now(),
    )

    await request.run_artifact_store.write_evaluation_run_manifest(
        evaluation_run_id=request.evaluation_run_id,
        manifest=evaluation_manifest.model_dump(mode="json"),
    )

    return evaluation_manifest
