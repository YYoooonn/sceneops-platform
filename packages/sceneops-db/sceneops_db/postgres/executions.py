from __future__ import annotations

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from sceneops_core.executions.schemas import (
    ExecutionBackend,
    ExecutionDispatchResult,
    ExecutionKind,
)

from sceneops_db.converters.executions import (
    execution_model_to_result,
    execution_result_to_values,
)
from sceneops_db.models.executions import ExecutionRecordModel

from ._utils import apply_pagination, enum_value


class PostgresExecutionRecordRepository:
    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def create(
        self,
        execution: ExecutionDispatchResult,
    ) -> ExecutionDispatchResult:
        model = ExecutionRecordModel(**execution_result_to_values(execution))
        self._session.add(model)
        await self._session.flush()
        await self._session.refresh(model)
        return execution_model_to_result(model)

    async def get(self, execution_id: str) -> ExecutionDispatchResult | None:
        stmt = select(ExecutionRecordModel).where(
            ExecutionRecordModel.execution_id == execution_id
        )
        result = await self._session.execute(stmt)
        model = result.scalar_one_or_none()
        return execution_model_to_result(model) if model is not None else None

    async def list(
        self,
        *,
        execution_backend: ExecutionBackend | None = None,
        execution_kind: ExecutionKind | None = None,
        resource_id: str | None = None,
        limit: int = 100,
        offset: int = 0,
    ) -> list[ExecutionDispatchResult]:
        stmt = select(ExecutionRecordModel)
        if execution_backend is not None:
            stmt = stmt.where(
                ExecutionRecordModel.execution_backend == enum_value(execution_backend)
            )
        if execution_kind is not None:
            stmt = stmt.where(
                ExecutionRecordModel.execution_kind == enum_value(execution_kind)
            )
        if resource_id is not None:
            stmt = stmt.where(ExecutionRecordModel.resource_id == resource_id)
        stmt = apply_pagination(
            stmt.order_by(ExecutionRecordModel.created_at.desc()),
            limit=limit,
            offset=offset,
        )
        result = await self._session.execute(stmt)
        return [execution_model_to_result(m) for m in result.scalars().all()]
