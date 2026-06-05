from __future__ import annotations

from sqlalchemy.ext.asyncio import AsyncSession

from sceneops_core.scenarios.schemas.records import ScenarioSetRecord
from sceneops_db.postgres import PostgresScenarioSetRepository


class ScenarioStore:
    def __init__(self, session: AsyncSession) -> None:
        self._sets = PostgresScenarioSetRepository(session)

    async def get(self, scenario_set_id: str) -> ScenarioSetRecord | None:
        return await self._sets.get(scenario_set_id)

    async def create(self, record: ScenarioSetRecord) -> ScenarioSetRecord:
        return await self._sets.create(record)

    async def save(self, record: ScenarioSetRecord) -> ScenarioSetRecord:
        return await self._sets.update(record)

    async def upsert(self, record: ScenarioSetRecord) -> ScenarioSetRecord:
        return await self._sets.upsert(record)

    async def list(
        self,
        *,
        dataset_id: str | None = None,
        dataset_version: str | None = None,
        limit: int = 100,
        offset: int = 0,
    ) -> list[ScenarioSetRecord]:
        return await self._sets.list(
            dataset_id=dataset_id,
            dataset_version=dataset_version,
            limit=limit,
            offset=offset,
        )
