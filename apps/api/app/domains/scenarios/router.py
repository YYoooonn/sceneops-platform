from __future__ import annotations

from fastapi import APIRouter

from app.core.errors import raise_not_found
from app.core.pagination import PaginationDep
from app.domains.scenarios.dependencies import ScenarioServiceDep
from app.domains.scenarios.schemas import (
    CreateScenarioSetRequest,
    ScenarioSetListResponse,
    ScenarioSetResponse,
)
from app.platform.artifacts.schemas import ArtifactListResponse

router = APIRouter()


@router.get("", response_model=ScenarioSetListResponse)
async def list_scenario_sets(
    *,
    service: ScenarioServiceDep,
    pagination: PaginationDep,
    dataset_id: str | None = None,
    dataset_version: str | None = None,
) -> ScenarioSetListResponse:
    return await service.list_scenario_sets(
        dataset_id=dataset_id,
        dataset_version=dataset_version,
        limit=pagination.limit,
        offset=pagination.offset,
    )


@router.post("", response_model=ScenarioSetResponse, status_code=201)
async def create_scenario_set(
    request: CreateScenarioSetRequest,
    service: ScenarioServiceDep,
) -> ScenarioSetResponse:
    return await service.create_scenario_set(request)


@router.get("/{scenario_set_id}", response_model=ScenarioSetResponse)
async def get_scenario_set(
    scenario_set_id: str, service: ScenarioServiceDep
) -> ScenarioSetResponse:
    result = await service.get_scenario_set(scenario_set_id)
    if result is None:
        raise_not_found("Scenario set", scenario_set_id)
    return result


@router.get("/{scenario_set_id}/artifacts", response_model=ArtifactListResponse)
async def list_scenario_set_artifacts(
    scenario_set_id: str,
    service: ScenarioServiceDep,
    pagination: PaginationDep,
) -> ArtifactListResponse:
    result = await service.list_scenario_set_artifacts(
        scenario_set_id, limit=pagination.limit, offset=pagination.offset
    )
    if result is None:
        raise_not_found("Scenario set", scenario_set_id)
    return ArtifactListResponse(artifacts=result, count=len(result))
