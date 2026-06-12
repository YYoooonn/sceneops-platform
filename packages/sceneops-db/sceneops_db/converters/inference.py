from __future__ import annotations

from typing import Any

from sceneops_core.inference.schemas.runs import InferenceRunRecord

from sceneops_db.models.inference import InferenceRunModel

from ._utils import base_run_to_values, error_from_json, metadata_from_model


def inference_run_model_to_record(model: InferenceRunModel) -> InferenceRunRecord:
    return InferenceRunRecord(
        run_id=model.run_id,
        type=model.type,
        status=model.status,
        dataset_id=model.dataset_id,
        dataset_version=model.dataset_version,
        model_id=model.model_id,
        model_version=model.model_version,
        dataset_manifest_uri=model.dataset_manifest_uri,
        inference_backend=model.inference_backend,
        predictions_root_uri=model.predictions_root_uri,
        prediction_manifest_uri=model.prediction_manifest_uri,
        sample_count=model.sample_count,
        prediction_count=model.prediction_count,
        pipeline_run_id=model.pipeline_run_id,
        pipeline_task_run_id=model.pipeline_task_run_id,
        job_id=model.job_id,
        params=model.params or {},
        result=model.result,
        error=error_from_json(model.error),
        artifact_root_uri=model.artifact_root_uri,
        manifest_uri=model.manifest_uri,
        metrics=model.metrics or {},
        created_at=model.created_at,
        updated_at=model.updated_at,
        started_at=model.started_at,
        finished_at=model.finished_at,
        metadata=metadata_from_model(model),
    )


def inference_run_record_to_values(record: InferenceRunRecord) -> dict[str, Any]:
    base = base_run_to_values(record)
    return {
        **base,
        "dataset_id": record.dataset_id,
        "dataset_version": record.dataset_version,
        "model_id": record.model_id,
        "model_version": record.model_version,
        "dataset_manifest_uri": record.dataset_manifest_uri,
        "inference_backend": record.inference_backend,
        "predictions_root_uri": record.predictions_root_uri,
        "prediction_manifest_uri": record.prediction_manifest_uri,
        "sample_count": record.sample_count,
        "prediction_count": record.prediction_count,
        "metrics": record.metrics or {},
    }
