from __future__ import annotations

from typing import Annotated

from fastapi import APIRouter, Query

from app.modules.operations.dependencies import OperationsServiceDep
from sceneops_core.operations.schemas import (
    JobTimelineResponse,
    OperationsSummaryResponse,
    PipelineTimelineResponse,
)

router = APIRouter(prefix="/operations", tags=["operations"])


@router.get(
    "/jobs/{job_id}/timeline",
    response_model=JobTimelineResponse,
)
async def get_job_timeline(
    *,
    job_id: str,
    service: OperationsServiceDep,
) -> JobTimelineResponse:
    return await service.get_job_timeline(job_id=job_id)


@router.get(
    "/pipelines/{pipeline_run_id}/timeline",
    response_model=PipelineTimelineResponse,
)
async def get_pipeline_timeline(
    *,
    pipeline_run_id: str,
    service: OperationsServiceDep,
) -> PipelineTimelineResponse:
    return await service.get_pipeline_timeline(
        pipeline_run_id=pipeline_run_id,
    )


@router.get(
    "/summary",
    response_model=OperationsSummaryResponse,
)
async def get_operations_summary(
    *,
    service: OperationsServiceDep,
    recent_failure_limit: Annotated[int, Query(ge=1, le=100)] = 10,
) -> OperationsSummaryResponse:
    return await service.get_summary(
        recent_failure_limit=recent_failure_limit,
    )
