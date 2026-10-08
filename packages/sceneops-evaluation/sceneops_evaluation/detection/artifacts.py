"""Per-sample results and run-level manifest assembly for detection
evaluations.

These functions are evaluator-algorithm-agnostic: any evaluator that produces
an EvaluationAccumulator and a DetectionEvaluationRequest can use them. They
build the evaluation manifest in memory; publishing and registering it as a
checksum-pinned revision is the evaluate_detection handler's job.
"""

from __future__ import annotations

from typing import Any

from sceneops_core.evaluations.schemas import EvaluationSampleShardRef
from sceneops_core.evaluations.schemas.manifests import DetectionEvaluationManifest
from sceneops_evaluation.detection.accumulation import EvaluationAccumulator
from sceneops_evaluation.detection.base import DetectionEvaluationRequest
from sceneops_derived import RunArtifactStore


def _model_identity(request: DetectionEvaluationRequest) -> tuple[Any, Any]:
    config = request.prediction.config
    return config.get("model_id"), config.get("model_version")


async def write_sample_evaluation(
    *,
    run_artifact_store: RunArtifactStore,
    evaluation_run_id: str,
    scene_id: str,
    sample_id: str,
    sample_eval: dict[str, Any],
) -> EvaluationSampleShardRef:
    """Persist one sample's evaluation result write-once; the returned ref
    pins its bytes for the run manifest."""
    written = await run_artifact_store.write_sample_evaluation(
        evaluation_run_id=evaluation_run_id,
        scene_id=scene_id,
        sample_id=sample_id,
        result=sample_eval,
    )
    return EvaluationSampleShardRef(
        scene_id=scene_id,
        sample_id=sample_id,
        uri=written.uri,
        checksum=written.checksum,
    )


def build_skipped_evaluation_manifest(
    *,
    request: DetectionEvaluationRequest,
    reason: str,
    metadata: dict[str, Any] | None = None,
) -> DetectionEvaluationManifest:
    model_id, model_version = _model_identity(request)
    return DetectionEvaluationManifest(
        evaluation_run_id=request.evaluation_run_id,
        inference_run_id=request.inference_run_id,
        dataset_id=request.dataset_id,
        dataset_version=request.dataset_version,
        model_id=model_id,
        model_version=model_version,
        inputs=request.inputs,
        status="skipped",
        match_distance_m=request.match_distance_m,
        metadata={**metadata, "reason": reason} if metadata else {"reason": reason},
    )


def build_final_evaluation_manifest(
    *,
    request: DetectionEvaluationRequest,
    accumulator: EvaluationAccumulator,
    sample_shards: list[EvaluationSampleShardRef],
    evaluation_unit: str = "label",
    metadata: dict[str, Any] | None = None,
) -> DetectionEvaluationManifest:
    metrics = accumulator.build_metrics()
    primary_metric_value = metrics.get("precision")
    primary_metric_name = "precision" if primary_metric_value is not None else None
    model_id, model_version = _model_identity(request)
    return DetectionEvaluationManifest(
        evaluation_run_id=request.evaluation_run_id,
        inference_run_id=request.inference_run_id,
        dataset_id=request.dataset_id,
        dataset_version=request.dataset_version,
        model_id=model_id,
        model_version=model_version,
        inputs=request.inputs,
        status="succeeded",
        match_distance_m=request.match_distance_m,
        sample_count=len(sample_shards),
        prediction_count=accumulator.raw_prediction_count,
        evaluable_prediction_count=accumulator.evaluable_prediction_count,
        lifting_failed_prediction_count=accumulator.lifting_failed_prediction_count,
        ground_truth_count=accumulator.ground_truth_count,
        evaluation_unit=evaluation_unit,
        primary_metric_name=primary_metric_name,
        primary_metric_value=primary_metric_value,
        metrics=metrics,
        class_metrics=accumulator.build_class_metrics(),
        metadata=metadata if metadata else {},
        sample_shards=sorted(sample_shards, key=lambda s: (s.scene_id, s.sample_id)),
    )
