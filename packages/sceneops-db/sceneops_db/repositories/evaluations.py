from __future__ import annotations

from typing import Protocol, runtime_checkable

from sceneops_core.evaluations.schemas import EvaluationTaskType
from sceneops_core.evaluations.schemas.runs import EvaluationRunRecord
from sceneops_core.runs.schemas import RunStatus


@runtime_checkable
class EvaluationRunRepository(Protocol):
    async def create(self, run: EvaluationRunRecord) -> EvaluationRunRecord: ...

    async def get(self, run_id: str) -> EvaluationRunRecord | None: ...

    async def update(self, run: EvaluationRunRecord) -> EvaluationRunRecord: ...

    async def list(
        self,
        *,
        status: RunStatus | None = None,
        task_type: EvaluationTaskType | None = None,
        dataset_id: str | None = None,
        dataset_version: str | None = None,
        model_id: str | None = None,
        model_version: str | None = None,
        inference_run_id: str | None = None,
        evaluator_id: str | None = None,
        job_id: str | None = None,
        pipeline_run_id: str | None = None,
        limit: int = 100,
        offset: int = 0,
    ) -> list[EvaluationRunRecord]: ...
