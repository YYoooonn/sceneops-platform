from __future__ import annotations

from typing import Protocol

from sceneops_core.models.schemas import (
    ModelRecord,
    ModelVersionRecord,
)


class ModelRepository(Protocol):
    async def upsert(self, record: ModelRecord) -> ModelRecord: ...

    async def get(self, model_id: str) -> ModelRecord: ...

    async def list(self) -> list[ModelRecord]: ...


class ModelVersionRepository(Protocol):
    async def upsert(self, record: ModelVersionRecord) -> ModelVersionRecord: ...

    async def get(
        self,
        *,
        model_id: str,
        version: str,
    ) -> ModelVersionRecord: ...

    async def list(
        self,
        *,
        model_id: str,
    ) -> list[ModelVersionRecord]: ...
