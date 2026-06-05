from __future__ import annotations

from typing import Any

from sceneops_core.jobs.schemas import JobEvent, JobManifest
from sceneops_core.jobs.schemas.steps import JobStep

from sceneops_db.models.jobs import JobEventModel, JobModel

from ._utils import (
    enum_to_value,
    error_from_json,
    error_to_json,
    metadata_from_model,
    values_with_metadata,
)


def job_model_to_manifest(model: JobModel) -> JobManifest:
    return JobManifest(
        job_id=model.job_id,
        type=model.type,
        status=model.status,
        dataset_id=model.dataset_id,
        dataset_version=model.dataset_version,
        params=model.params or {},
        steps=[JobStep.model_validate(s) for s in (model.steps or [])],
        result=model.result,
        error=error_from_json(model.error),
        pipeline_run_id=model.pipeline_run_id,
        pipeline_step_run_id=model.pipeline_step_run_id,
        pipeline_step_id=model.pipeline_step_id,
        retry_count=model.retry_count,
        max_retries=model.max_retries,
        worker_id=model.worker_id,
        queued_at=model.queued_at,
        locked_at=model.locked_at,
        heartbeat_at=model.heartbeat_at,
        started_at=model.started_at,
        finished_at=model.finished_at,
        created_at=model.created_at,
        updated_at=model.updated_at,
        metadata=metadata_from_model(model),
    )


def job_manifest_to_values(job: JobManifest) -> dict[str, Any]:
    data = job.model_dump(mode="python")
    # enums → values for DB
    data["type"] = enum_to_value(job.type)
    data["status"] = enum_to_value(job.status)
    # steps stored as JSON list
    data["steps"] = [s.model_dump(mode="json") for s in job.steps]
    # error stored as JSON
    data["error"] = error_to_json(job.error)
    return values_with_metadata(data)


def job_event_model_to_event(model: JobEventModel) -> JobEvent:
    return JobEvent(
        event_id=model.event_id,
        job_id=model.job_id,
        type=model.type,
        level=model.level,
        job_type=model.job_type,
        status=model.status,
        step_id=model.step_id,
        step_name=model.step_name,
        step_status=model.step_status,
        pipeline_run_id=model.pipeline_run_id,
        pipeline_step_run_id=model.pipeline_step_run_id,
        pipeline_step_id=model.pipeline_step_id,
        worker_id=model.worker_id,
        attempt=model.attempt,
        message=model.message,
        error=error_from_json(model.error),
        data=model.data or {},
        created_at=model.created_at,
    )


def job_event_to_values(event: JobEvent) -> dict[str, Any]:
    # JobEventModel has no metadata_ column — build dict explicitly.
    return {
        "event_id": event.event_id,
        "job_id": event.job_id,
        "type": enum_to_value(event.type),
        "level": enum_to_value(event.level),
        "job_type": enum_to_value(event.job_type),
        "status": enum_to_value(event.status),
        "step_id": event.step_id,
        "step_name": event.step_name,
        "step_status": enum_to_value(event.step_status),
        "pipeline_run_id": event.pipeline_run_id,
        "pipeline_step_run_id": event.pipeline_step_run_id,
        "pipeline_step_id": event.pipeline_step_id,
        "worker_id": event.worker_id,
        "attempt": event.attempt,
        "message": event.message,
        "error": error_to_json(event.error),
        "data": event.data or {},
        "created_at": event.created_at,
    }
