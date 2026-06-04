from __future__ import annotations

from sqlalchemy.ext.asyncio import AsyncSession

from sceneops_core.evaluations.schemas import EvaluationTaskType
from sceneops_core.evaluations.schemas.runs import EvaluationRunRecord
from sceneops_core.runs.schemas import RunStatus
from sceneops_db.postgres import PostgresEvaluationRunRepository


class EvaluationRunStore:
    def __init__(self, session: AsyncSession) -> None:
        self._repo = PostgresEvaluationRunRepository(session)

    async def get(self, run_id: str) -> EvaluationRunRecord | None:
        return await self._repo.get(run_id)

    async def create(self, run: EvaluationRunRecord) -> EvaluationRunRecord:
        return await self._repo.create(run)

    async def save(self, run: EvaluationRunRecord) -> EvaluationRunRecord:
        return await self._repo.update(run)

    async def list(
        self,
        *,
        status: RunStatus | None = None,
        task_type: EvaluationTaskType | None = None,
        dataset_id: str | None = None,
        dataset_version: str | None = None,
        model_id: str | None = None,
        job_id: str | None = None,
        pipeline_run_id: str | None = None,
        limit: int = 100,
        offset: int = 0,
    ) -> list[EvaluationRunRecord]:
        return await self._repo.list(
            status=status,
            task_type=task_type,
            dataset_id=dataset_id,
            dataset_version=dataset_version,
            model_id=model_id,
            job_id=job_id,
            pipeline_run_id=pipeline_run_id,
            limit=limit,
            offset=offset,
        )
