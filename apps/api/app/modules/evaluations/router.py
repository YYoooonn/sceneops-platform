from __future__ import annotations

from typing import Annotated

from fastapi import APIRouter, Query

from sceneops_core.evaluations import (
    EvaluationComparisonResponse,
    EvaluationTaskType,
    # LeaderboardSortBy,
    ModelVersionEvaluationHistoryResponse,
)
from sceneops_core.runs.schemas import RunStatus

from app.modules.evaluations.dependencies import EvaluationQueryServiceDep


router = APIRouter(prefix="/evaluations", tags=["evaluations"])


@router.get("/compare", response_model=EvaluationComparisonResponse)
async def compare_evaluations(
    *,
    dataset_id: str,
    dataset_version: str,
    service: EvaluationQueryServiceDep,
    task_type: EvaluationTaskType = EvaluationTaskType.DETECTION,
    evaluator_id: str | None = None,
    status: RunStatus | None = RunStatus.SUCCEEDED,
    limit: Annotated[int | None, Query(ge=1, le=500)] = None,
) -> EvaluationComparisonResponse:
    return await service.compare_by_dataset(
        dataset_id=dataset_id,
        dataset_version=dataset_version,
        task_type=task_type,
        evaluator_id=evaluator_id,
        status=status,
        limit=limit,
    )


@router.get(
    "/models/{model_id}/versions/{model_version}",
    response_model=ModelVersionEvaluationHistoryResponse,
)
async def list_model_version_evaluations(
    *,
    model_id: str,
    model_version: str,
    task_type: EvaluationTaskType | None = None,
    status: RunStatus | None = RunStatus.SUCCEEDED,
    limit: Annotated[int | None, Query(ge=1, le=500)] = None,
    service: EvaluationQueryServiceDep,
) -> ModelVersionEvaluationHistoryResponse:
    return await service.list_by_model_version(
        model_id=model_id,
        model_version=model_version,
        task_type=task_type,
        status=status,
        limit=limit,
    )
