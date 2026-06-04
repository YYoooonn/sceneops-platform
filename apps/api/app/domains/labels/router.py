from __future__ import annotations

from fastapi import APIRouter

from app.core.errors import raise_not_found
from app.core.pagination import PaginationDep
from app.domains.labels.dependencies import LabelServiceDep
from app.domains.labels.schemas import (
    DatasetLabelRunListResponse,
    DatasetLabelRunResponse,
    SceneLabelRunListResponse,
    SceneLabelRunResponse,
)
from app.platform.artifacts.schemas import ArtifactListResponse
from sceneops_core.runs.schemas import RunStatus

router = APIRouter()


# --- scene auto-label runs ---


@router.get("/scene-runs", response_model=SceneLabelRunListResponse)
async def list_scene_label_runs(
    *,
    service: LabelServiceDep,
    pagination: PaginationDep,
    scene_id: str | None = None,
    dataset_id: str | None = None,
    dataset_version: str | None = None,
    labeler_id: str | None = None,
    status: RunStatus | None = None,
    job_id: str | None = None,
    pipeline_run_id: str | None = None,
) -> SceneLabelRunListResponse:
    return await service.list_scene_runs(
        scene_id=scene_id,
        dataset_id=dataset_id,
        dataset_version=dataset_version,
        labeler_id=labeler_id,
        status=status,
        job_id=job_id,
        pipeline_run_id=pipeline_run_id,
        limit=pagination.limit,
        offset=pagination.offset,
    )


@router.get("/scene-runs/{run_id}", response_model=SceneLabelRunResponse)
async def get_scene_label_run(
    run_id: str, service: LabelServiceDep
) -> SceneLabelRunResponse:
    result = await service.get_scene_run(run_id)
    if result is None:
        raise_not_found("Scene label run", run_id)
    return result


@router.get("/scene-runs/{run_id}/artifacts", response_model=ArtifactListResponse)
async def list_scene_label_run_artifacts(
    run_id: str, service: LabelServiceDep, pagination: PaginationDep
) -> ArtifactListResponse:
    result = await service.list_scene_run_artifacts(
        run_id, limit=pagination.limit, offset=pagination.offset
    )
    if result is None:
        raise_not_found("Scene label run", run_id)
    return ArtifactListResponse(artifacts=result, count=len(result))


# --- dataset auto-label runs ---


@router.get("/dataset-runs", response_model=DatasetLabelRunListResponse)
async def list_dataset_label_runs(
    *,
    service: LabelServiceDep,
    pagination: PaginationDep,
    dataset_id: str | None = None,
    dataset_version: str | None = None,
    labeler_id: str | None = None,
    status: RunStatus | None = None,
    job_id: str | None = None,
    pipeline_run_id: str | None = None,
) -> DatasetLabelRunListResponse:
    return await service.list_dataset_runs(
        dataset_id=dataset_id,
        dataset_version=dataset_version,
        labeler_id=labeler_id,
        status=status,
        job_id=job_id,
        pipeline_run_id=pipeline_run_id,
        limit=pagination.limit,
        offset=pagination.offset,
    )


@router.get("/dataset-runs/{run_id}", response_model=DatasetLabelRunResponse)
async def get_dataset_label_run(
    run_id: str, service: LabelServiceDep
) -> DatasetLabelRunResponse:
    result = await service.get_dataset_run(run_id)
    if result is None:
        raise_not_found("Dataset label run", run_id)
    return result


@router.get("/dataset-runs/{run_id}/artifacts", response_model=ArtifactListResponse)
async def list_dataset_label_run_artifacts(
    run_id: str, service: LabelServiceDep, pagination: PaginationDep
) -> ArtifactListResponse:
    result = await service.list_dataset_run_artifacts(
        run_id, limit=pagination.limit, offset=pagination.offset
    )
    if result is None:
        raise_not_found("Dataset label run", run_id)
    return ArtifactListResponse(artifacts=result, count=len(result))
