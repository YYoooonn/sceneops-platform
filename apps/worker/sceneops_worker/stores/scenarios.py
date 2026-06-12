from __future__ import annotations

from sqlalchemy.ext.asyncio import AsyncSession

from sceneops_core.runs.schemas import RunStatus, RunType
from sceneops_core.scenarios.schemas.records import ScenarioSetRecord
from sceneops_core.scenarios.schemas.runs import (
    ScenarioMiningRunRecord,
    ScenarioReadinessRunRecord,
)
from sceneops_db.postgres import (
    PostgresScenarioRunRepository,
    PostgresScenarioSetRepository,
)

ScenarioRunRecord = ScenarioMiningRunRecord | ScenarioReadinessRunRecord


class ScenarioStore:
    def __init__(self, session: AsyncSession) -> None:
        self._sets = PostgresScenarioSetRepository(session)
        self._runs = PostgresScenarioRunRepository(session)

    # ── scenario set ─────────────────────────────────────────────────────────

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

    # ── scenario run records ──────────────────────────────────────────────────

    async def get_run(self, run_id: str) -> ScenarioRunRecord | None:
        return await self._runs.get(run_id)

    async def create_run(self, run: ScenarioRunRecord) -> ScenarioRunRecord:
        return await self._runs.create(run)

    async def save_run(self, run: ScenarioRunRecord) -> ScenarioRunRecord:
        return await self._runs.update(run)

    async def upsert_run(self, run: ScenarioRunRecord) -> ScenarioRunRecord:
        existing = await self._runs.get(run.run_id)
        if existing is None:
            return await self._runs.create(run)
        return await self._runs.update(run)

    async def list_runs(
        self,
        *,
        type: RunType | None = None,
        status: RunStatus | None = None,
        scenario_set_id: str | None = None,
        dataset_id: str | None = None,
        dataset_version: str | None = None,
        job_id: str | None = None,
        pipeline_run_id: str | None = None,
        limit: int = 100,
        offset: int = 0,
    ) -> list[ScenarioRunRecord]:
        return await self._runs.list(
            type=type,
            status=status,
            scenario_set_id=scenario_set_id,
            dataset_id=dataset_id,
            dataset_version=dataset_version,
            job_id=job_id,
            pipeline_run_id=pipeline_run_id,
            limit=limit,
            offset=offset,
        )
