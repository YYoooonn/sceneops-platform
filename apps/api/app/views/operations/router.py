from __future__ import annotations

from fastapi import APIRouter, Query

from app.core.pagination import PaginationDep
from app.views.operations.dependencies import OperationsServiceDep
from app.views.operations.schemas import (
    OperationFailuresResponse,
    OperationSummaryResponse,
    OperationTimelineResponse,
    RecentExecutionsResponse,
    RecentJobsResponse,
    RecentPipelinesResponse,
)

router = APIRouter()


@router.get("/summary", response_model=OperationSummaryResponse)
async def get_operations_summary(
    service: OperationsServiceDep,
) -> OperationSummaryResponse:
    return await service.get_summary()


@router.get("/timeline", response_model=OperationTimelineResponse)
async def get_operations_timeline(
    service: OperationsServiceDep,
    limit: int = Query(50, ge=1, le=200),
) -> OperationTimelineResponse:
    return await service.get_timeline(limit=limit)


@router.get("/jobs/recent", response_model=RecentJobsResponse)
async def get_recent_jobs(
    service: OperationsServiceDep,
    pagination: PaginationDep,
) -> RecentJobsResponse:
    return await service.get_recent_jobs(
        limit=pagination.limit, offset=pagination.offset
    )


@router.get("/pipelines/recent", response_model=RecentPipelinesResponse)
async def get_recent_pipelines(
    service: OperationsServiceDep,
    pagination: PaginationDep,
) -> RecentPipelinesResponse:
    return await service.get_recent_pipelines(
        limit=pagination.limit, offset=pagination.offset
    )


@router.get("/executions/recent", response_model=RecentExecutionsResponse)
async def get_recent_executions(
    service: OperationsServiceDep,
    pagination: PaginationDep,
) -> RecentExecutionsResponse:
    return await service.get_recent_executions(
        limit=pagination.limit, offset=pagination.offset
    )


@router.get("/failures", response_model=OperationFailuresResponse)
async def get_failures(
    service: OperationsServiceDep,
    pagination: PaginationDep,
) -> OperationFailuresResponse:
    return await service.get_failures(limit=pagination.limit, offset=pagination.offset)
