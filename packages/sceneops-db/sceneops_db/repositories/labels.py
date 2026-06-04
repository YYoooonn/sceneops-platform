from __future__ import annotations

from typing import Protocol, TypeAlias, runtime_checkable

from sceneops_core.labels.schemas.runs import (
    DatasetAutoLabelRunRecord,
    SceneAutoLabelRunRecord,
)
from sceneops_core.runs.schemas import RunStatus, RunType

LabelRunRecord: TypeAlias = SceneAutoLabelRunRecord | DatasetAutoLabelRunRecord


@runtime_checkable
class LabelRunRepository(Protocol):
    async def create(self, run: LabelRunRecord) -> LabelRunRecord: ...

    async def get(self, run_id: str) -> LabelRunRecord | None: ...

    async def update(self, run: LabelRunRecord) -> LabelRunRecord: ...

    async def list(
        self,
        *,
        type: RunType | None = None,
        status: RunStatus | None = None,
        dataset_id: str | None = None,
        dataset_version: str | None = None,
        scene_id: str | None = None,
        labeler_id: str | None = None,
        job_id: str | None = None,
        pipeline_run_id: str | None = None,
        limit: int = 100,
        offset: int = 0,
    ) -> list[LabelRunRecord]: ...
