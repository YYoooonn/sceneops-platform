from __future__ import annotations

from sqlalchemy.ext.asyncio import AsyncSession

from sceneops_core.runs.schemas import RunStatus, RunType
from sceneops_db.converters.labels import LabelRunRecord
from sceneops_db.postgres import PostgresLabelRunRepository


class LabelRunStore:
    def __init__(self, session: AsyncSession) -> None:
        self._repo = PostgresLabelRunRepository(session)

    async def get(self, run_id: str) -> LabelRunRecord | None:
        return await self._repo.get(run_id)

    async def create(self, run: LabelRunRecord) -> LabelRunRecord:
        return await self._repo.create(run)

    async def save(self, run: LabelRunRecord) -> LabelRunRecord:
        return await self._repo.update(run)

    async def list(
        self,
        *,
        type: RunType | None = None,
        status: RunStatus | None = None,
        dataset_id: str | None = None,
        dataset_version: str | None = None,
        scene_id: str | None = None,
        labeler_id: str | None = None,
        job_id: str | None = None,
        pipeline_run_id: str | None = None,
        limit: int = 100,
        offset: int = 0,
    ) -> list[LabelRunRecord]:
        return await self._repo.list(
            type=type,
            status=status,
            dataset_id=dataset_id,
            dataset_version=dataset_version,
            scene_id=scene_id,
            labeler_id=labeler_id,
            job_id=job_id,
            pipeline_run_id=pipeline_run_id,
            limit=limit,
            offset=offset,
        )
