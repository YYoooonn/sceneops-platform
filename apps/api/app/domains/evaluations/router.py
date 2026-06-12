from __future__ import annotations

from fastapi import APIRouter

from app.core.errors import raise_not_found
from app.core.pagination import PaginationDep
from app.domains.evaluations.dependencies import EvaluationServiceDep
from app.domains.evaluations.schemas import (
    EvaluationMetricsResponse,
    EvaluationRunListResponse,
    EvaluationRunResponse,
)
from app.platform.artifacts.schemas import ArtifactListResponse
from sceneops_core.evaluations.schemas.enums import EvaluationTaskType
from sceneops_core.runs.schemas import RunStatus

router = APIRouter()


@router.get("/runs", response_model=EvaluationRunListResponse)
async def list_evaluation_runs(
    *,
    service: EvaluationServiceDep,
    pagination: PaginationDep,
    dataset_id: str | None = None,
    dataset_version: str | None = None,
    model_id: str | None = None,
    model_version: str | None = None,
    inference_run_id: str | None = None,
    task_type: EvaluationTaskType | None = None,
    evaluator_id: str | None = None,
    status: RunStatus | None = None,
    job_id: str | None = None,
    pipeline_run_id: str | None = None,
) -> EvaluationRunListResponse:
    return await service.list_runs(
        dataset_id=dataset_id,
        dataset_version=dataset_version,
        model_id=model_id,
        model_version=model_version,
        inference_run_id=inference_run_id,
        task_type=task_type,
        evaluator_id=evaluator_id,
        status=status,
        job_id=job_id,
        pipeline_run_id=pipeline_run_id,
        limit=pagination.limit,
        offset=pagination.offset,
    )


@router.get("/runs/{evaluation_run_id}", response_model=EvaluationRunResponse)
async def get_evaluation_run(
    evaluation_run_id: str,
    service: EvaluationServiceDep,
) -> EvaluationRunResponse:
    result = await service.get_run(evaluation_run_id)
    if result is None:
        raise_not_found("Evaluation run", evaluation_run_id)
    return result


@router.get(
    "/runs/{evaluation_run_id}/metrics", response_model=EvaluationMetricsResponse
)
async def get_evaluation_run_metrics(
    evaluation_run_id: str,
    service: EvaluationServiceDep,
) -> EvaluationMetricsResponse:
    result = await service.get_run_metrics(evaluation_run_id)
    if result is None:
        raise_not_found("Evaluation metrics", evaluation_run_id)
    return result


@router.get("/runs/{evaluation_run_id}/artifacts", response_model=ArtifactListResponse)
async def list_evaluation_run_artifacts(
    evaluation_run_id: str,
    service: EvaluationServiceDep,
    pagination: PaginationDep,
) -> ArtifactListResponse:
    result = await service.list_run_artifacts(
        evaluation_run_id, limit=pagination.limit, offset=pagination.offset
    )
    if result is None:
        raise_not_found("Evaluation run", evaluation_run_id)
    return ArtifactListResponse(artifacts=result, count=len(result))
