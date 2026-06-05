from __future__ import annotations

from typing import Any

from sceneops_core.pipelines.schemas import PipelineRunManifest, PipelineStepRunManifest
from sceneops_core.pipelines.schemas.results import (
    PipelineRunResult,
    PipelineStepResult,
)

from sceneops_db.models.pipelines import PipelineRunModel, PipelineStepRunModel

from ._utils import (
    enum_to_value,
    error_from_json,
    metadata_from_model,
    values_with_metadata,
)


def _remap_legacy_pipeline_result(result: dict) -> dict:
    # Stored before rename: steps[]{step_id, step_name} → {pipeline_step_id, pipeline_step_name}
    if "steps" not in result:
        return result
    remapped_steps = []
    for step in result["steps"]:
        if "step_id" in step and "pipeline_step_id" not in step:
            step = {
                **step,
                "pipeline_step_id": step["step_id"],
                "pipeline_step_name": step.get("step_name", step["step_id"]),
            }
        remapped_steps.append(step)
    return {**result, "steps": remapped_steps}


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


def pipeline_step_run_model_to_manifest(
    model: PipelineStepRunModel,
) -> PipelineStepRunManifest:
    return PipelineStepRunManifest(
        pipeline_step_run_id=model.pipeline_step_run_id,
        pipeline_run_id=model.pipeline_run_id,
        pipeline_step_id=model.pipeline_step_id,
        pipeline_step_name=model.pipeline_step_name,
        step_order=model.step_order,
        status=model.status,
        job_type=model.job_type,
        job_id=model.job_id,
        depends_on_step_ids=list(
            model.depends_on_step_ids or []
        ),  # DB column stays depends_on_step_ids
        params=model.params or {},
        result=PipelineStepResult.model_validate(model.result)
        if model.result
        else None,
        error=error_from_json(model.error),
        created_at=model.created_at,
        updated_at=model.updated_at,
        started_at=model.started_at,
        finished_at=model.finished_at,
        metadata=metadata_from_model(model),
    )


def pipeline_step_run_manifest_to_values(
    step: PipelineStepRunManifest,
) -> dict[str, Any]:
    data = step.model_dump(mode="python")
    # result/error stored as JSON
    if data.get("result") is not None:
        data["result"] = step.result.model_dump(mode="json") if step.result else None
    if data.get("error") is not None:
        data["error"] = step.error.model_dump(mode="json") if step.error else None
    # enums → values
    data["status"] = step.status.value if hasattr(step.status, "value") else step.status
    data["job_type"] = enum_to_value(step.job_type)
    return values_with_metadata(data)
