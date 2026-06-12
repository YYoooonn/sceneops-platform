from __future__ import annotations

from sqlalchemy.ext.asyncio import AsyncSession

from sceneops_core.runs.schemas import RunStatus, RunType
from sceneops_db.converters.datasets import DatasetRunRecord
from sceneops_db.postgres import PostgresDatasetRunRepository


class DatasetRunStore:
    def __init__(self, session: AsyncSession) -> None:
        self._repo = PostgresDatasetRunRepository(session)

    async def get(self, run_id: str) -> DatasetRunRecord | None:
        return await self._repo.get(run_id)

    async def create(self, run: DatasetRunRecord) -> DatasetRunRecord:
        return await self._repo.create(run)

    async def save(self, run: DatasetRunRecord) -> DatasetRunRecord:
        return await self._repo.update(run)

    async def list(
        self,
        *,
        type: RunType | None = None,
        status: RunStatus | None = None,
        dataset_id: str | None = None,
        dataset_version: str | None = None,
        job_id: str | None = None,
        pipeline_run_id: str | None = None,
        limit: int = 100,
        offset: int = 0,
    ) -> list[DatasetRunRecord]:
        return await self._repo.list(
            type=type,
            status=status,
            dataset_id=dataset_id,
            dataset_version=dataset_version,
            job_id=job_id,
            pipeline_run_id=pipeline_run_id,
            limit=limit,
            offset=offset,
        )
