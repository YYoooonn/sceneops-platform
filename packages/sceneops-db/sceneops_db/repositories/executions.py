from __future__ import annotations

from typing import Protocol, runtime_checkable

from sceneops_core.executions.schemas import (
    ExecutionBackend,
    ExecutionDispatchResult,
    ExecutionKind,
    ExecutionStatus,
)


@runtime_checkable
class ExecutionRecordRepository(Protocol):
    async def create(
        self,
        execution: ExecutionDispatchResult,
    ) -> ExecutionDispatchResult: ...

    async def get(self, execution_id: str) -> ExecutionDispatchResult | None: ...

    async def update(
        self,
        execution: ExecutionDispatchResult,
    ) -> ExecutionDispatchResult: ...

    async def list(
        self,
        *,
        execution_backend: ExecutionBackend | None = None,
        execution_kind: ExecutionKind | None = None,
        resource_id: str | None = None,
        status: ExecutionStatus | None = None,
        limit: int = 100,
        offset: int = 0,
    ) -> list[ExecutionDispatchResult]: ...

    async def count_by_status(self) -> dict[str, int]: ...
