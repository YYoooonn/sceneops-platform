from __future__ import annotations

from sceneops_core.schemas.models import ModelVersionRecord
from sceneops_db.model_registry import PostgresModelVersionRepository
from sceneops_db.session import async_session_scope


class ModelRegistryStore:
    async def get_version(
        self,
        *,
        model_id: str,
        model_version: str,
    ) -> ModelVersionRecord:
        async with async_session_scope() as session:
            repository = PostgresModelVersionRepository(session)
            return await repository.get(
                model_id=model_id,
                version=model_version,
            )
