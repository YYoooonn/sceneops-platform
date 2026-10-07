from __future__ import annotations

from sqlalchemy.ext.asyncio import AsyncSession

from sceneops_core.executions.schemas import ExecutionDispatchResult
from sceneops_db.postgres import PostgresExecutionRecordRepository


class ExecutionRecordStore:
    """The dispatch records of the Jobs a worker submits, in the same shape the
    API records for the dispatches it makes."""

    def __init__(self, session: AsyncSession) -> None:
        self._repo = PostgresExecutionRecordRepository(session)

    async def create(
        self, execution: ExecutionDispatchResult
    ) -> ExecutionDispatchResult:
        return await self._repo.create(execution)
