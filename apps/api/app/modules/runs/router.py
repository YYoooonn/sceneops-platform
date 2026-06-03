from __future__ import annotations

from fastapi import APIRouter, HTTPException

from app.modules.runs.dependencies import RunServiceDep
from sceneops_core.datasets.schemas import DatasetValidationStatus
from sceneops_core.runs.schemas import (
    DatasetProfileRunDetailResponse,
    DatasetProfileRunListResponse,
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
    *,
    dataset_id: str | None = None,
    dataset_version: str | None = None,
    model_id: str | None = None,
    model_version: str | None = None,
    status: RunStatus | None = None,
    service: RunServiceDep,
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
    service: RunServiceDep,
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
    *,
    service: RunServiceDep,
    dataset_id: str | None = None,
    dataset_version: str | None = None,
    model_id: str | None = None,
    model_version: str | None = None,
    inference_run_id: str | None = None,
    status: RunStatus | None = None,
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
    service: RunServiceDep,
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
    *,
    service: RunServiceDep,
    dataset_id: str | None = None,
    dataset_version: str | None = None,
    status: RunStatus | None = None,
    validation_status: DatasetValidationStatus | None = None,
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
    service: RunServiceDep,
) -> DatasetValidationRunDetailResponse:
    response = await service.get_validation_run(validation_run_id)
    if response is None:
        raise HTTPException(
            status_code=404,
            detail=f"Validation run not found: {validation_run_id}",
        )
    return response


@router.get(
    "/profiles",
    response_model=DatasetProfileRunListResponse,
    response_model_by_alias=True,
)
async def list_profile_runs(
    *,
    service: RunServiceDep,
    dataset_id: str | None = None,
    dataset_version: str | None = None,
    status: RunStatus | None = None,
) -> DatasetProfileRunListResponse:
    return await service.list_profile_runs(
        dataset_id=dataset_id,
        dataset_version=dataset_version,
        status=status,
    )


@router.get(
    "/profiles/{profile_run_id}",
    response_model=DatasetProfileRunDetailResponse,
    response_model_by_alias=True,
)
async def get_profile_run(
    profile_run_id: str,
    service: RunServiceDep,
) -> DatasetProfileRunDetailResponse:
    response = await service.get_profile_run(profile_run_id)
    if response is None:
        raise HTTPException(
            status_code=404,
            detail=f"Profile run not found: {profile_run_id}",
        )
    return response
