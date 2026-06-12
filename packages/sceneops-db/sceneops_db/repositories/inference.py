from __future__ import annotations

from typing import Protocol, runtime_checkable

from sceneops_core.inference.schemas.runs import InferenceRunRecord
from sceneops_core.runs.schemas import RunStatus


@runtime_checkable
class InferenceRunRepository(Protocol):
    async def create(self, run: InferenceRunRecord) -> InferenceRunRecord: ...

    async def get(self, run_id: str) -> InferenceRunRecord | None: ...

    async def update(self, run: InferenceRunRecord) -> InferenceRunRecord: ...

    async def list(
        self,
        *,
        status: RunStatus | None = None,
        dataset_id: str | None = None,
        dataset_version: str | None = None,
        model_id: str | None = None,
        model_version: str | None = None,
        job_id: str | None = None,
        pipeline_run_id: str | None = None,
        limit: int = 100,
        offset: int = 0,
    ) -> list[InferenceRunRecord]: ...
