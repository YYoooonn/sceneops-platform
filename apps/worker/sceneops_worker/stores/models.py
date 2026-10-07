from __future__ import annotations

from sqlalchemy.ext.asyncio import AsyncSession

from sceneops_core.models.schemas import (
    ModelRecord,
    ModelVersionRecord,
)
from sceneops_db.postgres import PostgresModelRepository, PostgresModelVersionRepository


class ModelStore:
    def __init__(self, session: AsyncSession) -> None:
        self._models = PostgresModelRepository(session)
        self._versions = PostgresModelVersionRepository(session)

    async def get(self, model_id: str) -> ModelRecord | None:
        return await self._models.get(model_id)

    async def get_version(
        self,
        *,
        model_id: str,
        version: str,
    ) -> ModelVersionRecord | None:
        return await self._versions.get(model_id=model_id, version=version)
