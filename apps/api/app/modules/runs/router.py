from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException

from app.core.dependencies import get_run_service
from app.modules.runs.service import RunService
from sceneops_core.schemas.datasets import DatasetValidationStatus
from sceneops_core.schemas.runs import (
    DatasetValidationRunDetailResponse,
    DatasetValidationRunListResponse,
    EvaluationRunDetailResponse,
    EvaluationRunListResponse,
    InferenceRunDetailResponse,
    InferenceRunListResponse,
    RunStatus,
)

router = APIRouter(
    prefix="/runs",
    tags=["runs"],
)


@router.get(
    "/inference",
    response_model=InferenceRunListResponse,
    response_model_by_alias=True,
)
async def list_inference_runs(
    dataset_id: str | None = None,
    dataset_version: str | None = None,
    model_id: str | None = None,
    model_version: str | None = None,
    status: RunStatus | None = None,
    service: RunService = Depends(get_run_service),
) -> InferenceRunListResponse:
    return await service.list_inference_runs(
        dataset_id=dataset_id,
        dataset_version=dataset_version,
        model_id=model_id,
        model_version=model_version,
        status=status,
    )


@router.get(
    "/inference/{run_id}",
    response_model=InferenceRunDetailResponse,
    response_model_by_alias=True,
)
async def get_inference_run(
    run_id: str,
    service: RunService = Depends(get_run_service),
) -> InferenceRunDetailResponse:
    response = await service.get_inference_run(run_id)
    if response is None:
        raise HTTPException(
            status_code=404, detail=f"Inference run not found: {run_id}"
        )
    return response


@router.get(
    "/evaluations",
    response_model=EvaluationRunListResponse,
    response_model_by_alias=True,
)
async def list_evaluation_runs(
    dataset_id: str | None = None,
    dataset_version: str | None = None,
    model_id: str | None = None,
    model_version: str | None = None,
    inference_run_id: str | None = None,
    status: RunStatus | None = None,
    service: RunService = Depends(get_run_service),
) -> EvaluationRunListResponse:
    return await service.list_evaluation_runs(
        dataset_id=dataset_id,
        dataset_version=dataset_version,
        model_id=model_id,
        model_version=model_version,
        inference_run_id=inference_run_id,
        status=status,
    )


@router.get(
    "/evaluations/{evaluation_run_id}",
    response_model=EvaluationRunDetailResponse,
    response_model_by_alias=True,
)
async def get_evaluation_run(
    evaluation_run_id: str,
    service: RunService = Depends(get_run_service),
) -> EvaluationRunDetailResponse:
    response = await service.get_evaluation_run(evaluation_run_id)
    if response is None:
        raise HTTPException(
            status_code=404,
            detail=f"Evaluation run not found: {evaluation_run_id}",
        )
    return response


@router.get(
    "/validations",
    response_model=DatasetValidationRunListResponse,
    response_model_by_alias=True,
)
async def list_validation_runs(
    dataset_id: str | None = None,
    dataset_version: str | None = None,
    status: RunStatus | None = None,
    validation_status: DatasetValidationStatus | None = None,
    service: RunService = Depends(get_run_service),
) -> EvaluationRunListResponse:
    return await service.list_validation_run(
        dataset_id=dataset_id,
        dataset_version=dataset_version,
        status=status,
        validation_status=validation_status,
    )


@router.get(
    "/validations/{validation_run_id}",
    response_model=DatasetValidationRunDetailResponse,
    response_model_by_alias=True,
)
async def get_validation_run(
    validation_run_id: str,
    service: RunService = Depends(get_run_service),
) -> DatasetValidationRunDetailResponse:
    response = await service.get_validation_run(validation_run_id)
    if response is None:
        raise HTTPException(
            status_code=404,
            detail=f"Evaluation run not found: {validation_run_id}",
        )
    return response
