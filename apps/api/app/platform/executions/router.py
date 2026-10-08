from __future__ import annotations

from fastapi import APIRouter

from app.core.errors import raise_not_found
from app.core.pagination import PaginationDep
from app.platform.executions.dependencies import ExecutionServiceDep
from sceneops_execution.executions.schemas import (
    ExecutionListResponse,
    ExecutionResponse,
)
from sceneops_core.executions.schemas import (
    ExecutionBackend,
    ExecutionKind,
)

router = APIRouter()


@router.get("", response_model=ExecutionListResponse)
async def list_executions(
    *,
    service: ExecutionServiceDep,
    pagination: PaginationDep,
    execution_backend: ExecutionBackend | None = None,
    execution_kind: ExecutionKind | None = None,
    resource_id: str | None = None,
) -> ExecutionListResponse:
    executions = await service.list_executions(
        execution_backend=execution_backend,
        execution_kind=execution_kind,
        resource_id=resource_id,
        limit=pagination.limit,
        offset=pagination.offset,
    )
    return ExecutionListResponse(executions=executions, count=len(executions))


@router.get("/{execution_id}", response_model=ExecutionResponse)
async def get_execution(
    execution_id: str,
    service: ExecutionServiceDep,
) -> ExecutionResponse:
    execution = await service.get_execution(execution_id)
    if execution is None:
        raise_not_found("Execution", execution_id)
    return ExecutionResponse(execution=execution)
