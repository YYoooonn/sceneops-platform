from __future__ import annotations

from sqlalchemy.ext.asyncio import AsyncSession

from sceneops_core.scenes.schemas import (
    SceneGenerationMethod,
    SceneOriginType,
    SceneRecord,
    SceneStatus,
)
from sceneops_db.postgres import PostgresSceneRepository


class SceneStore:
    def __init__(self, session: AsyncSession) -> None:
        self._repo = PostgresSceneRepository(session)

    async def get(self, scene_id: str) -> SceneRecord | None:
        return await self._repo.get(scene_id)

    async def create(self, scene: SceneRecord) -> SceneRecord:
        return await self._repo.create(scene)

    async def save(self, scene: SceneRecord) -> SceneRecord:
        return await self._repo.update(scene)

    async def upsert(self, scene: SceneRecord) -> SceneRecord:
        return await self._repo.upsert(scene)

    async def list(
        self,
        *,
        dataset_id: str | None = None,
        dataset_version: str | None = None,
        status: SceneStatus | None = None,
        origin_type: SceneOriginType | None = None,
        generation_method: SceneGenerationMethod | None = None,
        limit: int = 100,
        offset: int = 0,
    ) -> list[SceneRecord]:
        return await self._repo.list(
            dataset_id=dataset_id,
            dataset_version=dataset_version,
            status=status,
            origin_type=origin_type,
            generation_method=generation_method,
            limit=limit,
            offset=offset,
        )
