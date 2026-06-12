from __future__ import annotations

from sqlalchemy.ext.asyncio import AsyncSession

from sceneops_core.inference.schemas.runs import InferenceRunRecord
from sceneops_core.runs.schemas import RunStatus
from sceneops_db.postgres import PostgresInferenceRunRepository


class InferenceRunStore:
    def __init__(self, session: AsyncSession) -> None:
        self._repo = PostgresInferenceRunRepository(session)

    async def get(self, run_id: str) -> InferenceRunRecord | None:
        return await self._repo.get(run_id)

    async def create(self, run: InferenceRunRecord) -> InferenceRunRecord:
        return await self._repo.create(run)

    async def save(self, run: InferenceRunRecord) -> InferenceRunRecord:
        return await self._repo.update(run)

    async def upsert(self, run: InferenceRunRecord) -> InferenceRunRecord:
        existing = await self._repo.get(run.run_id)
        if existing is None:
            return await self._repo.create(run)
        return await self._repo.update(run)

    async def list(
        self,
        *,
        status: RunStatus | None = None,
        dataset_id: str | None = None,
        dataset_version: str | None = None,
        model_id: str | None = None,
        model_version: str | None = None,
        job_id: str | None = None,
        pipeline_run_id: str | None = None,
        limit: int = 100,
        offset: int = 0,
    ) -> list[InferenceRunRecord]:
        return await self._repo.list(
            status=status,
            dataset_id=dataset_id,
            dataset_version=dataset_version,
            model_id=model_id,
            model_version=model_version,
            job_id=job_id,
            pipeline_run_id=pipeline_run_id,
            limit=limit,
            offset=offset,
        )
