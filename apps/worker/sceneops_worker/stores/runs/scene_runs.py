from __future__ import annotations

from sqlalchemy.ext.asyncio import AsyncSession

from sceneops_core.runs.schemas import RunStatus, RunType
from sceneops_db.converters.scenes import SceneRunRecord
from sceneops_db.postgres import PostgresSceneRunRepository


class SceneRunStore:
    def __init__(self, session: AsyncSession) -> None:
        self._repo = PostgresSceneRunRepository(session)

    async def get(self, run_id: str) -> SceneRunRecord | None:
        return await self._repo.get(run_id)

    async def create(self, run: SceneRunRecord) -> SceneRunRecord:
        return await self._repo.create(run)

    async def save(self, run: SceneRunRecord) -> SceneRunRecord:
        return await self._repo.update(run)

    async def list(
        self,
        *,
        type: RunType | None = None,
        status: RunStatus | None = None,
        scene_id: str | None = None,
        dataset_id: str | None = None,
        dataset_version: str | None = None,
        job_id: str | None = None,
        pipeline_run_id: str | None = None,
        limit: int = 100,
        offset: int = 0,
    ) -> list[SceneRunRecord]:
        return await self._repo.list(
            type=type,
            status=status,
            scene_id=scene_id,
            dataset_id=dataset_id,
            dataset_version=dataset_version,
            job_id=job_id,
            pipeline_run_id=pipeline_run_id,
            limit=limit,
            offset=offset,
        )
