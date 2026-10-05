from __future__ import annotations

from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from sceneops_core.executions.schemas import ExecutionDispatchResult
from sceneops_db.postgres.executions import PostgresExecutionRecordRepository
from sceneops_db.postgres.pipelines import (
    PostgresPipelineRunRepository,
    PostgresPipelineTaskRunRepository,
)

from app.platform.executions.backends.base import PipelineExecutionBackend
from app.platform.executions.service import ExecutionService
from app.platform.pipelines.service import PipelineService


class PipelineDispatchFacade:
    def __init__(
        self,
        *,
        session_factory: async_sessionmaker[AsyncSession],
        pipeline_backend: PipelineExecutionBackend,
    ) -> None:
        self._session_factory = session_factory
        self._pipeline_backend = pipeline_backend

    async def dispatch(self, pipeline_run_id: str) -> ExecutionDispatchResult:
        # commit-before-backend-dispatch prevents worker RUNNING/SUCCEEDED state
        # from being overwritten by delayed API QUEUED commits. If backend dispatch
        # fails after commit, the pipeline run intentionally remains QUEUED and can
        # be redispatched.
        async with self._session_factory() as session:
            pipeline_service = PipelineService(
                pipeline_repository=PostgresPipelineRunRepository(session),
                task_repository=PostgresPipelineTaskRunRepository(session),
            )
            execution_service = ExecutionService(
                pipeline_backend=self._pipeline_backend,
                record_repository=PostgresExecutionRecordRepository(session),
            )

            run = await pipeline_service.mark_queued(pipeline_run_id)
            await session.commit()

            execution = await execution_service.dispatch_pipeline(
                pipeline_run_id, run.type
            )
            await session.commit()

            return execution
