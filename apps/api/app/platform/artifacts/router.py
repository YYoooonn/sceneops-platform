from __future__ import annotations

from fastapi import APIRouter

from app.core.errors import raise_not_found
from app.core.pagination import PaginationDep
from app.platform.artifacts.dependencies import ArtifactServiceDep
from app.platform.artifacts.schemas import ArtifactListResponse, ArtifactResponse
from sceneops_core.artifacts.schemas import ArtifactKind

router = APIRouter()


@router.get("", response_model=ArtifactListResponse)
async def list_artifacts(
    *,
    service: ArtifactServiceDep,
    pagination: PaginationDep,
    kind: ArtifactKind | None = None,
    owner_type: str | None = None,
    owner_id: str | None = None,
    dataset_id: str | None = None,
    dataset_version: str | None = None,
    scene_id: str | None = None,
    scenario_set_id: str | None = None,
    run_id: str | None = None,
    job_id: str | None = None,
    pipeline_run_id: str | None = None,
) -> ArtifactListResponse:
    artifacts = await service.list_artifacts(
        kind=kind,
        owner_type=owner_type,
        owner_id=owner_id,
        dataset_id=dataset_id,
        dataset_version=dataset_version,
        scene_id=scene_id,
        scenario_set_id=scenario_set_id,
        run_id=run_id,
        job_id=job_id,
        pipeline_run_id=pipeline_run_id,
        limit=pagination.limit,
        offset=pagination.offset,
    )
    return ArtifactListResponse(artifacts=artifacts, count=len(artifacts))


@router.get("/{artifact_id}", response_model=ArtifactResponse)
async def get_artifact(
    artifact_id: str,
    service: ArtifactServiceDep,
) -> ArtifactResponse:
    artifact = await service.get_artifact(artifact_id)
    if artifact is None:
        raise_not_found("Artifact", artifact_id)
    return ArtifactResponse(artifact=artifact)
