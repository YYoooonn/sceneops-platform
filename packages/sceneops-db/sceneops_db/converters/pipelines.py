from __future__ import annotations

from typing import Any

from sceneops_core.pipelines.schemas import PipelineRunManifest, PipelineTaskRunManifest
from sceneops_core.pipelines.schemas.results import (
    PipelineRunResult,
    PipelineTaskResult,
)

from sceneops_db.models.pipelines import PipelineRunModel, PipelineTaskRunModel

from ._utils import (
    enum_to_value,
    error_from_json,
    metadata_from_model,
    values_with_metadata,
)


def _remap_legacy_pipeline_result(result: dict) -> dict:
    # Handle field renames in stored results:
    # 1. "steps" key → "tasks" (pipeline step→task rename)
    # 2. Within each item:
    #    - step_id/pipeline_step_id → pipeline_task_id (very old + pre-task-rename data)
    #    - step_name/pipeline_step_name → pipeline_task_name
    if "steps" not in result and "tasks" not in result:
        return result

    items = result.get("tasks") or result.get("steps") or []
    remapped = []
    for item in items:
        if (
            "step_id" in item
            and "pipeline_task_id" not in item
            and "pipeline_step_id" not in item
        ):
            item = {
                **item,
                "pipeline_task_id": item["step_id"],
                "pipeline_task_name": item.get("step_name", item["step_id"]),
            }
        elif "pipeline_step_id" in item and "pipeline_task_id" not in item:
            item = {
                **item,
                "pipeline_task_id": item["pipeline_step_id"],
                "pipeline_task_name": item.get(
                    "pipeline_step_name", item["pipeline_step_id"]
                ),
            }
        remapped.append(item)

    new_result = {k: v for k, v in result.items() if k not in ("steps", "tasks")}
    new_result["tasks"] = remapped
    return new_result


def pipeline_run_model_to_manifest(model: PipelineRunModel) -> PipelineRunManifest:
    return PipelineRunManifest(
        pipeline_run_id=model.pipeline_run_id,
        type=model.type,
        status=model.status,
        dataset_id=model.dataset_id,
        dataset_version=model.dataset_version,
        model_id=model.model_id,
        model_version=model.model_version,
        params=model.params or {},
        result=PipelineRunResult.model_validate(
            _remap_legacy_pipeline_result(model.result)
        )
        if model.result
        else None,
        error=error_from_json(model.error),
        execution_key=model.execution_key,
        created_at=model.created_at,
        updated_at=model.updated_at,
        started_at=model.started_at,
        finished_at=model.finished_at,
        metadata=metadata_from_model(model),
    )


def pipeline_run_manifest_to_values(run: PipelineRunManifest) -> dict[str, Any]:
    data = run.model_dump(mode="python")
    # result/error are stored as JSON dicts
    if data.get("result") is not None:
        data["result"] = run.result.model_dump(mode="json") if run.result else None
    if data.get("error") is not None:
        data["error"] = run.error.model_dump(mode="json") if run.error else None
    # enums → values
    data["type"] = enum_to_value(run.type)
    data["status"] = enum_to_value(run.status)
    return values_with_metadata(data)


def pipeline_task_run_model_to_manifest(
    model: PipelineTaskRunModel,
) -> PipelineTaskRunManifest:
    return PipelineTaskRunManifest(
        pipeline_task_run_id=model.pipeline_task_run_id,
        pipeline_run_id=model.pipeline_run_id,
        pipeline_task_id=model.pipeline_task_id,
        pipeline_task_name=model.pipeline_task_name,
        task_order=model.task_order,
        status=model.status,
        job_type=model.job_type,
        job_id=model.job_id,
        depends_on_task_ids=list(model.depends_on_task_ids or []),
        params=model.params or {},
        result=PipelineTaskResult.model_validate(model.result)
        if model.result
        else None,
        error=error_from_json(model.error),
        created_at=model.created_at,
        updated_at=model.updated_at,
        started_at=model.started_at,
        finished_at=model.finished_at,
        metadata=metadata_from_model(model),
    )


def pipeline_task_run_manifest_to_values(
    task: PipelineTaskRunManifest,
) -> dict[str, Any]:
    data = task.model_dump(mode="python")
    # result/error stored as JSON
    if data.get("result") is not None:
        data["result"] = task.result.model_dump(mode="json") if task.result else None
    if data.get("error") is not None:
        data["error"] = task.error.model_dump(mode="json") if task.error else None
    # enums → values
    data["status"] = task.status.value if hasattr(task.status, "value") else task.status
    data["job_type"] = enum_to_value(task.job_type)
    return values_with_metadata(data)
