from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException

from app.core.dependencies import get_artifact_service
from app.modules.artifacts.service import ArtifactService
from sceneops_core.schemas.common import JsonDict

router = APIRouter(
    prefix="/artifacts",
    tags=["artifacts"],
)


@router.get(
    "/datasets/{dataset_id}/versions/{dataset_version}/manifest",
    response_model=dict,
    response_model_by_alias=True,
)
async def get_dataset_manifest_artifact(
    dataset_id: str,
    dataset_version: str,
    service: ArtifactService = Depends(get_artifact_service),
) -> JsonDict:
    try:
        return await service.get_dataset_manifest(
            dataset_id=dataset_id,
            dataset_version=dataset_version,
        )
    except FileNotFoundError as error:
        raise HTTPException(status_code=404, detail=str(error)) from error


@router.get(
    "/runs/inference/{run_id}",
    response_model=dict,
    response_model_by_alias=True,
)
async def get_inference_run_artifact(
    run_id: str,
    service: ArtifactService = Depends(get_artifact_service),
) -> JsonDict:
    try:
        return await service.get_inference_run_manifest(run_id)
    except FileNotFoundError as error:
        raise HTTPException(status_code=404, detail=str(error)) from error


@router.get(
    "/runs/evaluations/{evaluation_run_id}",
    response_model=dict,
    response_model_by_alias=True,
)
async def get_evaluation_run_artifact(
    evaluation_run_id: str,
    service: ArtifactService = Depends(get_artifact_service),
) -> JsonDict:
    try:
        return await service.get_evaluation_run_manifest(evaluation_run_id)
    except FileNotFoundError as error:
        raise HTTPException(status_code=404, detail=str(error)) from error
