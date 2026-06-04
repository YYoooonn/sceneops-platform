from __future__ import annotations

from typing import Any

from sceneops_core.pipelines.schemas import PipelineRunManifest, PipelineStepRunManifest
from sceneops_core.pipelines.schemas.results import (
    PipelineRunResult,
    PipelineStepResult,
)

from sceneops_db.models.pipelines import PipelineRunModel, PipelineStepRunModel

from ._utils import error_from_json, metadata_from_model, values_with_metadata


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
        result=PipelineRunResult.model_validate(model.result) if model.result else None,
        error=error_from_json(model.error),
        created_at=model.created_at,
        updated_at=model.updated_at,
        started_at=model.started_at,
        finished_at=model.finished_at,
        metadata=metadata_from_model(model),
    )


def pipeline_run_manifest_to_values(run: PipelineRunManifest) -> dict[str, Any]:
    return values_with_metadata(run.model_dump(mode="json"))


def pipeline_step_run_model_to_manifest(
    model: PipelineStepRunModel,
) -> PipelineStepRunManifest:
    return PipelineStepRunManifest(
        pipeline_step_run_id=model.pipeline_step_run_id,
        pipeline_run_id=model.pipeline_run_id,
        step_id=model.step_id,
        step_name=model.step_name,
        step_order=model.step_order,
        status=model.status,
        job_type=model.job_type,
        job_id=model.job_id,
        depends_on_step_ids=list(model.depends_on_step_ids or []),
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
    return values_with_metadata(step.model_dump(mode="json"))
