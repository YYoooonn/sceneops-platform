from __future__ import annotations

from fastapi import APIRouter

from app.core.errors import raise_bad_request, raise_not_found
from app.core.pagination import PaginationDep
from app.platform.executions.dependencies import ExecutionServiceDep
from app.platform.pipelines.dependencies import PipelineServiceDep
from app.platform.pipelines.schemas import (
    PipelineDefinitionListResponse,
    PipelineDefinitionResponse,
    PipelineExecuteResponse,
    PipelineRunDetailResponse,
    PipelineRunListResponse,
    PipelineStepRunListResponse,
)
from sceneops_core.pipelines.schemas import (
    CreatePipelineRunRequest,
    PipelineRunStatus,
    PipelineType,
)

router = APIRouter()


# --- definitions ---


@router.get("/definitions", response_model=PipelineDefinitionListResponse)
async def list_pipeline_definitions(
    service: PipelineServiceDep,
) -> PipelineDefinitionListResponse:
    definitions = service.list_pipeline_definitions()
    return PipelineDefinitionListResponse(
        definitions=definitions, count=len(definitions)
    )


@router.get("/definitions/{pipeline_type}", response_model=PipelineDefinitionResponse)
async def get_pipeline_definition(
    pipeline_type: PipelineType,
    service: PipelineServiceDep,
) -> PipelineDefinitionResponse:
    definition = service.get_pipeline_definition(pipeline_type)
    if definition is None:
        raise_not_found("Pipeline definition", pipeline_type)
    return PipelineDefinitionResponse(definition=definition)


# --- pipeline runs ---


@router.post("/runs", response_model=PipelineRunDetailResponse, status_code=201)
async def create_pipeline_run(
    request: CreatePipelineRunRequest,
    service: PipelineServiceDep,
) -> PipelineRunDetailResponse:
    return await service.create_pipeline_run(request)


@router.get("/runs", response_model=PipelineRunListResponse)
async def list_pipeline_runs(
    *,
    service: PipelineServiceDep,
    pagination: PaginationDep,
    status: PipelineRunStatus | None = None,
    pipeline_type: str | None = None,
    dataset_id: str | None = None,
    dataset_version: str | None = None,
) -> PipelineRunListResponse:
    return await service.list_pipeline_runs(
        status=status,
        pipeline_type=pipeline_type,
        dataset_id=dataset_id,
        dataset_version=dataset_version,
        limit=pagination.limit,
        offset=pagination.offset,
    )


@router.get("/runs/{pipeline_run_id}", response_model=PipelineRunDetailResponse)
async def get_pipeline_run(
    pipeline_run_id: str,
    service: PipelineServiceDep,
) -> PipelineRunDetailResponse:
    result = await service.get_pipeline_run(pipeline_run_id)
    if result is None:
        raise_not_found("Pipeline run", pipeline_run_id)
    return result


@router.get("/runs/{pipeline_run_id}/steps", response_model=PipelineStepRunListResponse)
async def list_pipeline_steps(
    pipeline_run_id: str,
    service: PipelineServiceDep,
) -> PipelineStepRunListResponse:
    result = await service.list_pipeline_step_runs(pipeline_run_id)
    if result is None:
        raise_not_found("Pipeline run", pipeline_run_id)
    return result


@router.post("/runs/{pipeline_run_id}/execute", response_model=PipelineExecuteResponse)
async def execute_pipeline_run(
    pipeline_run_id: str,
    service: PipelineServiceDep,
    execution_service: ExecutionServiceDep,
) -> PipelineExecuteResponse:
    try:
        await service.validate_executable(pipeline_run_id)
    except FileNotFoundError:
        raise_not_found("Pipeline run", pipeline_run_id)
    except ValueError as exc:
        raise_bad_request(str(exc))

    execution = await execution_service.dispatch_pipeline(pipeline_run_id)
    return PipelineExecuteResponse(execution=execution)
