from __future__ import annotations

from typing import Protocol, TypeAlias, runtime_checkable

from sceneops_core.runs.schemas import RunStatus, RunType
from sceneops_core.scenarios.schemas.records import ScenarioSetRecord
from sceneops_core.scenarios.schemas.runs import (
    ScenarioMiningRunRecord,
    ScenarioReadinessRunRecord,
)

ScenarioRunRecord: TypeAlias = ScenarioMiningRunRecord | ScenarioReadinessRunRecord


@runtime_checkable
class ScenarioSetRepository(Protocol):
    async def create(self, record: ScenarioSetRecord) -> ScenarioSetRecord: ...

    async def upsert(self, record: ScenarioSetRecord) -> ScenarioSetRecord: ...

    async def get(self, scenario_set_id: str) -> ScenarioSetRecord | None: ...

    async def update(self, record: ScenarioSetRecord) -> ScenarioSetRecord: ...

    async def list(
        self,
        *,
        dataset_id: str | None = None,
        dataset_version: str | None = None,
        limit: int = 100,
        offset: int = 0,
    ) -> list[ScenarioSetRecord]: ...


@runtime_checkable
class ScenarioRunRepository(Protocol):
    async def create(self, run: ScenarioRunRecord) -> ScenarioRunRecord: ...

    async def get(self, run_id: str) -> ScenarioRunRecord | None: ...

    async def update(self, run: ScenarioRunRecord) -> ScenarioRunRecord: ...

    async def list(
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
    ) -> list[ScenarioRunRecord]: ...
