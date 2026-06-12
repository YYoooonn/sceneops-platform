from __future__ import annotations

from typing import Protocol, runtime_checkable

from sceneops_core.models.schemas import (
    ModelBackend,
    ModelRecord,
    ModelTaskType,
    ModelVersionRecord,
    ModelVersionStatus,
)


@runtime_checkable
class ModelRepository(Protocol):
    async def create(self, model: ModelRecord) -> ModelRecord: ...

    async def upsert(self, model: ModelRecord) -> ModelRecord: ...

    async def get(self, model_id: str) -> ModelRecord | None: ...

    async def update(self, model: ModelRecord) -> ModelRecord: ...

    async def list(
        self,
        *,
        task_type: ModelTaskType | None = None,
        limit: int = 100,
        offset: int = 0,
    ) -> list[ModelRecord]: ...


@runtime_checkable
class ModelVersionRepository(Protocol):
    async def create(self, version: ModelVersionRecord) -> ModelVersionRecord: ...

    async def upsert(self, version: ModelVersionRecord) -> ModelVersionRecord: ...

    async def get(
        self,
        *,
        model_id: str,
        version: str,
    ) -> ModelVersionRecord | None: ...

    async def update(self, version: ModelVersionRecord) -> ModelVersionRecord: ...

    async def list(
        self,
        *,
        model_id: str | None = None,
        task_type: ModelTaskType | None = None,
        backend: ModelBackend | None = None,
        status: ModelVersionStatus | None = None,
        limit: int = 100,
        offset: int = 0,
    ) -> list[ModelVersionRecord]: ...
