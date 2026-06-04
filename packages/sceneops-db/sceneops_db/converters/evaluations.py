from __future__ import annotations

from typing import Any

from sceneops_core.evaluations.schemas.runs import EvaluationRunRecord

from sceneops_db.models.evaluations import EvaluationRunModel

from ._utils import (
    base_run_to_values,
    enum_to_value,
    error_from_json,
    metadata_from_model,
)


def evaluation_run_model_to_record(model: EvaluationRunModel) -> EvaluationRunRecord:
    return EvaluationRunRecord(
        run_id=model.run_id,
        type=model.type,
        status=model.status,
        dataset_id=model.dataset_id,
        dataset_version=model.dataset_version,
        model_id=model.model_id,
        model_version=model.model_version,
        inference_run_id=model.inference_run_id,
        predictions_root_uri=model.predictions_root_uri,
        evaluator_id=model.evaluator_id,
        task_type=model.task_type,
        sample_count=model.sample_count,
        evaluation_manifest_uri=model.evaluation_manifest_uri,
        metrics_uri=model.metrics_uri,
        pipeline_run_id=model.pipeline_run_id,
        pipeline_step_run_id=model.pipeline_step_run_id,
        job_id=model.job_id,
        params=model.params or {},
        result=model.result,
        error=error_from_json(model.error),
        artifact_root_uri=model.artifact_root_uri,
        manifest_uri=model.manifest_uri,
        summary=model.summary or {},
        metrics=model.metrics or {},
        class_metrics=model.class_metrics or {},
        created_at=model.created_at,
        updated_at=model.updated_at,
        started_at=model.started_at,
        finished_at=model.finished_at,
        metadata=metadata_from_model(model),
    )


def evaluation_run_record_to_values(record: EvaluationRunRecord) -> dict[str, Any]:
    base = base_run_to_values(record)
    return {
        **base,
        "dataset_id": record.dataset_id,
        "dataset_version": record.dataset_version,
        "model_id": record.model_id,
        "model_version": record.model_version,
        "inference_run_id": record.inference_run_id,
        "predictions_root_uri": record.predictions_root_uri,
        "evaluator_id": record.evaluator_id,
        "task_type": enum_to_value(record.task_type),
        "sample_count": record.sample_count,
        "evaluation_manifest_uri": record.evaluation_manifest_uri,
        "metrics_uri": record.metrics_uri,
        "summary": record.summary or {},
        "metrics": record.metrics or {},
        "class_metrics": record.class_metrics or {},
    }
