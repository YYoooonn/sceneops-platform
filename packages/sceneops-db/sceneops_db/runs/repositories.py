from __future__ import annotations

from typing import Protocol

from sceneops_core.schemas.runs import (
    EvaluationRunRecord,
    InferenceRunRecord,
    RunStatus,
)


class InferenceRunRepository(Protocol):
    async def upsert(self, record: InferenceRunRecord) -> InferenceRunRecord:
        ...

    async def get(self, run_id: str) -> InferenceRunRecord:
        ...

    async def list(
        self,
        *,
        dataset_id: str | None = None,
        dataset_version: str | None = None,
        model_id: str | None = None,
        model_version: str | None = None,
        status: RunStatus | None = None,
    ) -> list[InferenceRunRecord]:
        ...


class EvaluationRunRepository(Protocol):
    async def upsert(self, record: EvaluationRunRecord) -> EvaluationRunRecord:
        ...

    async def get(self, evaluation_run_id: str) -> EvaluationRunRecord:
        ...

    async def list(
        self,
        *,
        dataset_id: str | None = None,
        dataset_version: str | None = None,
        model_id: str | None = None,
        model_version: str | None = None,
        inference_run_id: str | None = None,
        status: RunStatus | None = None,
    ) -> list[EvaluationRunRecord]:
        ...
