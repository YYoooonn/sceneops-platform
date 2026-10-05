from __future__ import annotations

from sceneops_core.artifacts.schemas import ArtifactRecord
from sceneops_db.repositories.artifacts import ArtifactRepository
from sceneops_db.repositories.scenarios import ScenarioSetRepository

from app.domains.scenarios.schemas import (
    ScenarioSetListResponse,
    ScenarioSetResponse,
)


class ScenarioService:
    def __init__(
        self,
        *,
        repository: ScenarioSetRepository,
        artifact_repository: ArtifactRepository,
    ) -> None:
        self._repository = repository
        self._artifact_repository = artifact_repository

    async def list_scenario_sets(
        self,
        *,
        dataset_id: str | None = None,
        dataset_version: str | None = None,
        limit: int = 100,
        offset: int = 0,
    ) -> ScenarioSetListResponse:
        records = await self._repository.list(
            dataset_id=dataset_id,
            dataset_version=dataset_version,
            limit=limit,
            offset=offset,
        )
        return ScenarioSetListResponse(scenario_sets=records, count=len(records))

    async def get_scenario_set(
        self, scenario_set_id: str
    ) -> ScenarioSetResponse | None:
        record = await self._repository.get(scenario_set_id)
        if record is None:
            return None
        return ScenarioSetResponse(scenario_set=record)

    async def list_scenario_set_artifacts(
        self, scenario_set_id: str, *, limit: int = 100, offset: int = 0
    ) -> list[ArtifactRecord] | None:
        record = await self._repository.get(scenario_set_id)
        if record is None:
            return None
        return await self._artifact_repository.list(
            scenario_set_id=scenario_set_id, limit=limit, offset=offset
        )
