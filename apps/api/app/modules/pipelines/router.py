from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException

from app.core.dependencies import (
    get_execution_dispatcher,
    get_pipeline_service,
)
from app.modules.executions.dispatchers import ExecutionDispatcher
from app.modules.pipelines.schemas import PipelineExecutionResponse
from app.modules.pipelines.service import PipelineService
from sceneops_core.schemas.executions import ExecutionStatus
from sceneops_core.schemas.pipelines import (
    CreatePipelineRunRequest,
    PipelineRunDetailResponse,
    PipelineRunListResponse,
    PipelineRunStatus,
)

router = APIRouter(prefix="/pipelines", tags=["pipelines"])


@router.post("/runs", response_model=PipelineRunDetailResponse)
async def create_pipeline_run(
    request: CreatePipelineRunRequest,
    service: PipelineService = Depends(get_pipeline_service),
) -> PipelineRunDetailResponse:
    return await service.create_pipeline_run(request)


@router.get("/runs", response_model=PipelineRunListResponse)
async def list_pipeline_runs(
    status: PipelineRunStatus | None = None,
    pipeline_type: str | None = None,
    dataset_id: str | None = None,
    dataset_version: str | None = None,
    service: PipelineService = Depends(get_pipeline_service),
) -> PipelineRunListResponse:
    return await service.list_pipeline_runs(
        status=status,
        pipeline_type=pipeline_type,
        dataset_id=dataset_id,
        dataset_version=dataset_version,
    )


@router.get("/runs/{pipeline_run_id}", response_model=PipelineRunDetailResponse)
async def get_pipeline_run(
    pipeline_run_id: str,
    service: PipelineService = Depends(get_pipeline_service),
) -> PipelineRunDetailResponse:
    result = await service.get_pipeline_run_detail(pipeline_run_id)

    if result is None:
        raise HTTPException(status_code=404, detail="Pipeline run not found")

    return result


@router.post(
    "/runs/{pipeline_run_id}/execute", response_model=PipelineExecutionResponse
)
async def execute_pipeline_run(
    pipeline_run_id: str,
    service: PipelineService = Depends(get_pipeline_service),
    dispatcher: ExecutionDispatcher = Depends(get_execution_dispatcher),
) -> PipelineExecutionResponse:
    try:
        await service.validate_executable(pipeline_run_id)
    except FileNotFoundError as error:
        raise HTTPException(
            status_code=404,
            detail=str(error),
        ) from error
    except RuntimeError as error:
        raise HTTPException(
            status_code=409,
            detail=str(error),
        ) from error

    result = dispatcher.dispatch_pipeline_run(
        pipeline_run_id=pipeline_run_id,
    )

    return PipelineExecutionResponse(
        pipeline_run_id=pipeline_run_id,
        execution_id=result.execution_id,
        execution_backend=result.execution_backend,
        execution_kind=result.execution_kind,
        status=result.status,
        queued=result.status == ExecutionStatus.QUEUED,
    )
