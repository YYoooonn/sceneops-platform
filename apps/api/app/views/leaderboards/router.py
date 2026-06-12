from __future__ import annotations

from fastapi import APIRouter, Query

from app.core.pagination import PaginationDep
from app.views.leaderboards.dependencies import LeaderboardServiceDep
from app.views.leaderboards.schemas import (
    DatasetVersionEvaluationResponse,
    LeaderboardResponse,
    ModelEvaluationHistoryResponse,
)
from sceneops_core.evaluations.schemas.enums import EvaluationTaskType
from sceneops_core.runs.schemas import RunStatus

router = APIRouter()


@router.get("/evaluations", response_model=LeaderboardResponse)
async def list_evaluation_leaderboard(
    *,
    service: LeaderboardServiceDep,
    pagination: PaginationDep,
    dataset_id: str | None = None,
    dataset_version: str | None = None,
    model_id: str | None = None,
    model_version: str | None = None,
    task_type: EvaluationTaskType | None = None,
    evaluator_id: str | None = None,
    status: RunStatus | None = None,
    metric: str | None = Query(
        None, description="Metric key to sort by (e.g. precision, recall, f1)"
    ),
    order: str = Query("desc", pattern="^(asc|desc)$"),
) -> LeaderboardResponse:
    return await service.list_evaluations(
        dataset_id=dataset_id,
        dataset_version=dataset_version,
        model_id=model_id,
        model_version=model_version,
        task_type=task_type,
        evaluator_id=evaluator_id,
        status=status,
        metric_name=metric,
        order=order,
        limit=pagination.limit,
        offset=pagination.offset,
    )


@router.get("/models/{model_id}", response_model=ModelEvaluationHistoryResponse)
async def get_model_evaluation_history(
    model_id: str,
    service: LeaderboardServiceDep,
    pagination: PaginationDep,
    model_version: str | None = None,
    task_type: EvaluationTaskType | None = None,
) -> ModelEvaluationHistoryResponse:
    return await service.get_model_history(
        model_id,
        model_version=model_version,
        task_type=task_type,
        limit=pagination.limit,
        offset=pagination.offset,
    )


@router.get(
    "/datasets/{dataset_id}/versions/{version}",
    response_model=DatasetVersionEvaluationResponse,
)
async def get_dataset_version_evaluations(
    dataset_id: str,
    version: str,
    service: LeaderboardServiceDep,
    pagination: PaginationDep,
    task_type: EvaluationTaskType | None = None,
) -> DatasetVersionEvaluationResponse:
    return await service.get_dataset_version_evaluations(
        dataset_id,
        version,
        task_type=task_type,
        limit=pagination.limit,
        offset=pagination.offset,
    )
