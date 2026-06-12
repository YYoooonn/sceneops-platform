from __future__ import annotations

from typing import Protocol, TypeAlias, runtime_checkable

from sceneops_core.runs.schemas import RunStatus, RunType
from sceneops_core.scenes.schemas import (
    SceneStatus,
    SceneGenerationMethod,
    SceneOriginType,
    SceneRecord,
)
from sceneops_core.scenes.schemas.runs import (
    SceneComparisonRunRecord,
    ScenePackageExportRunRecord,
    SceneProfileRunRecord,
    SceneReconstructionRunRecord,
    SceneValidationRunRecord,
)

SceneRunRecord: TypeAlias = (
    SceneValidationRunRecord
    | SceneProfileRunRecord
    | SceneComparisonRunRecord
    | SceneReconstructionRunRecord
    | ScenePackageExportRunRecord
)


@runtime_checkable
class SceneRepository(Protocol):
    async def create(self, scene: SceneRecord) -> SceneRecord: ...

    async def upsert(self, scene: SceneRecord) -> SceneRecord: ...

    async def get(self, scene_id: str) -> SceneRecord | None: ...

    async def update(self, scene: SceneRecord) -> SceneRecord: ...

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
    ) -> list[SceneRecord]: ...


@runtime_checkable
class SceneRunRepository(Protocol):
    async def create(self, run: SceneRunRecord) -> SceneRunRecord: ...

    async def get(self, run_id: str) -> SceneRunRecord | None: ...

    async def update(self, run: SceneRunRecord) -> SceneRunRecord: ...

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
    ) -> list[SceneRunRecord]: ...

    async def list_latest_by_dataset_version(
        self,
        *,
        dataset_id: str,
        dataset_version: str,
        run_type: RunType,
    ) -> dict[str, SceneRunRecord]: ...
